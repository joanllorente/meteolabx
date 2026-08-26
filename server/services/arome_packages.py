"""Cliente de los paquetes GRIB2 de AROME.

La API "ciblée" que alimenta el resto del visor entrega **un campo por
petición**: montar un perfil vertical cuesta ahí 102 descargas por hora de
predicción. Los paquetes traen el mismo dato agrupado —un GRIB2 multimensaje
con todos los niveles y siete plazos— y transfieren tres veces menos bytes.

El fichero se guarda en disco mientras se usa, se lee mensaje a mensaje y se
descarta: descomprimirlo entero en memoria serían ~1,9 GB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import numpy as np
import rasterio
import requests

from server.services.meteofrance_auth import authorization_headers


PACKAGE_BASE = "https://public-api.meteofrance.fr/previnum/DPPaquetAROME/v1"
# Cada paquete cubre siete plazos horarios consecutivos.
BLOCK_HOURS = 7
GDAL_CACHE_MB = int(os.getenv("METEOLABX_GDAL_CACHE_MB", "64"))
# Elementos del paquete isobárico IP1, con la clave que usa el perfil.
IP1_ELEMENTS = {
    "TMP": "temperature",
    "RH": "relative_humidity",
    "UGRD": "u",
    "VGRD": "v",
    "GP": "geopotential",
}


class AromePackageError(RuntimeError):
    """El paquete no se pudo descargar o no contiene lo esperado."""


def _cache_dir() -> Path:
    configured = os.getenv("METEOLABX_AROME_PACKAGE_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    # Al temporal del contenedor, nunca al volumen: son cientos de megas.
    return Path(tempfile.gettempdir()) / "meteolabx-arome-packages"


def block_range(run: datetime, valid_time: datetime) -> str:
    """Rango de plazos, en el formato `00H06H` que espera la API."""
    horizon = int((valid_time - run).total_seconds() // 3600)
    if horizon < 0:
        raise AromePackageError("La hora pedida es anterior a la pasada.")
    start = (horizon // BLOCK_HOURS) * BLOCK_HOURS
    return f"{start:02d}H{start + BLOCK_HOURS - 1:02d}H"


def _package_path(package: str, run: datetime, block: str) -> Path:
    stamp = run.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    return _cache_dir() / f"{package}-{stamp}-{block}.grib2"


def _is_downloaded(destination: Path) -> bool:
    try:
        return destination.stat().st_size > 0
    except OSError:
        return False


def ensure_package(package: str, run: datetime, valid_time: datetime) -> Path:
    """Descarga el bloque que contiene esa hora, si no está ya en disco.

    Un bloque cubre siete plazos y los procesos van por horas consecutivas, así
    que varios piden el mismo fichero casi a la vez. Sin coordinarlos, seis
    workers se bajaban seis copias de medio giga simultáneamente y se robaban
    el ancho de banda entre ellos. Con el cerrojo baja uno y los demás esperan
    a encontrarlo hecho, que es justo lo que iban a tardar de todas formas.
    """
    block = block_range(run, valid_time)
    destination = _package_path(package, run, block)
    if _is_downloaded(destination):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_suffix(".lock")
    with lock_path.open("a+", encoding="ascii") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            # Puede haberlo bajado otro mientras esperábamos el turno.
            if _is_downloaded(destination):
                return destination
            return _download_package(package, run, block, destination)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _download_package(
    package: str, run: datetime, block: str, destination: Path
) -> Path:
    partial = destination.with_suffix(f".{os.getpid()}.part")
    url = f"{PACKAGE_BASE}/models/AROME/grids/0.025/packages/{package}/productARO"
    parameters = {
        "referencetime": run.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time": block,
        "format": "grib2",
    }
    try:
        with requests.get(
            url,
            headers=authorization_headers(),
            params=parameters,
            timeout=1800,
            stream=True,
        ) as response:
            if response.status_code != 200:
                raise AromePackageError(
                    f"El paquete {package} {block} no está disponible "
                    f"(HTTP {response.status_code})."
                )
            with partial.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        # Se renombra al final para que nadie lea un fichero a medio bajar.
        partial.replace(destination)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise AromePackageError(f"No se pudo descargar {package} {block}: {exc}") from exc
    return destination


def read_isobaric_profile(
    path: Path, valid_time: datetime, levels_hpa: list[float]
) -> tuple[dict[str, dict[float, np.ndarray]], tuple[Any, Any, Any]]:
    """Campos del perfil para una hora, leídos mensaje a mensaje.

    Devuelve `({"temperature": {850.0: array, ...}, ...}, geometría)` con las
    unidades tal cual las publica el paquete: °C, %, m/s y m²/s².

    La geometría es la del paquete, no la de quien pregunta: son rejillas que
    pueden no coincidir —un recorte del WCS frente al dominio completo del
    GRIB— y darles la ajena convierte los valores en basura sin avisar.
    """
    wanted_levels = {int(round(level * 100)) for level in levels_hpa}
    stamp = int(valid_time.astimezone(timezone.utc).timestamp())
    profile: dict[str, dict[float, np.ndarray]] = {
        name: {} for name in IP1_ELEMENTS.values()
    }
    # GDAL cachea bloques del GRIB y su límite por defecto es un porcentaje de
    # la RAM de la máquina: sobre un fichero de medio giga se quedaba con casi
    # un giga por proceso, memoria que le hace falta al diagnóstico. Acotarlo
    # no cuesta tiempo: los mensajes se leen una vez y en orden.
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB), rasterio.open(path) as dataset:
        geometria = (dataset.transform, dataset.crs, dataset.bounds)
        for index in range(1, dataset.count + 1):
            tags = dataset.tags(index)
            element = IP1_ELEMENTS.get(tags.get("GRIB_ELEMENT", ""))
            if element is None:
                continue
            if int(tags.get("GRIB_VALID_TIME", -1)) != stamp:
                continue
            short_name = tags.get("GRIB_SHORT_NAME", "")
            if not short_name.endswith("-ISBL"):
                continue
            level_pa = int(short_name.split("-", 1)[0])
            if level_pa not in wanted_levels:
                continue
            # read() de una sola banda: el fichero nunca entra entero en memoria.
            # El paquete marca las celdas fuera del dominio con 9999; el resto
            # del pipeline espera NaN, igual que entrega el WCS.
            values = dataset.read(index, masked=True).astype(float)
            profile[element][level_pa / 100.0] = values.filled(np.nan)
    faltan = [name for name, campos in profile.items() if not campos]
    if faltan:
        raise AromePackageError(
            f"El paquete no trae {', '.join(faltan)} para "
            f"{valid_time:%Y-%m-%dT%H:%M}Z."
        )
    return profile, geometria


# Campos de superficie que el diagnóstico convectivo necesita, repartidos entre
# los dos paquetes de superficie. La temperatura a 2 m no está aquí: se sigue
# pidiendo al WCS porque es la referencia que fija la geometría del recorte.
# Comprobado contra el WCS sobre la misma pasada y hora: viento y presión
# coinciden hasta el último bit del float, y el rocío difiere 0,039 °C como
# máximo, que es la precisión con la que el paquete empaqueta ese campo.
SURFACE_ELEMENTS: dict[str, dict[tuple[str, str], tuple[str, str]]] = {
    "SP1": {
        ("UGRD", "10-HTGL"): ("surface_u", "m/s"),
        ("VGRD", "10-HTGL"): ("surface_v", "m/s"),
    },
    "SP2": {
        ("DPT", "2-HTGL"): ("surface_dewpoint", "C"),
        ("PRES", "0-SFC"): ("surface_pressure", "Pa"),
    },
}


def read_surface_fields(
    path: Path, valid_time: datetime, wanted: dict[tuple[str, str], tuple[str, str]]
) -> tuple[dict[str, tuple[np.ndarray, str]], tuple[Any, Any, Any]]:
    """Campos de superficie de una hora, leídos mensaje a mensaje.

    Devuelve `({"surface_u": (array, "m/s"), ...}, geometría)` con las unidades
    tal cual las publica el paquete. Faltar un campo no es un error aquí: quien
    llama decide si completa por el WCS o se queda sin él.

    La geometría es la del paquete, no la de quien pregunta: son rejillas que
    pueden no coincidir —un recorte del WCS frente al dominio completo del
    GRIB— y darles la ajena convierte los valores en basura sin avisar.
    """
    stamp = int(valid_time.astimezone(timezone.utc).timestamp())
    salida: dict[str, tuple[np.ndarray, str]] = {}
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB), rasterio.open(path) as dataset:
        geometria = (dataset.transform, dataset.crs, dataset.bounds)
        for index in range(1, dataset.count + 1):
            tags = dataset.tags(index)
            clave = (tags.get("GRIB_ELEMENT", ""), tags.get("GRIB_SHORT_NAME", ""))
            destino = wanted.get(clave)
            if destino is None or destino[0] in salida:
                continue
            if int(tags.get("GRIB_VALID_TIME", -1)) != stamp:
                continue
            # Igual que en el perfil: 9999 marca fuera de dominio y el resto
            # del pipeline espera NaN, que es lo que entrega el WCS.
            values = dataset.read(index, masked=True).astype(float)
            salida[destino[0]] = (values.filled(np.nan), destino[1])
    return salida, geometria


def discard_packages_before(run: datetime) -> list[Path]:
    """Borra los paquetes de pasadas anteriores; ocupan cientos de megas.

    También sus cerrojos, que no pesan nada pero se acumularían pasada tras
    pasada sin que nadie volviera a mirarlos.
    """
    stamp = run.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    removed: list[Path] = []
    try:
        candidates = list(_cache_dir().glob("*.grib2"))
        candidates += list(_cache_dir().glob("*.lock"))
    except OSError:
        return removed
    for path in candidates:
        partes = path.stem.split("-")
        if len(partes) >= 2 and partes[1] < stamp:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                continue
    return removed

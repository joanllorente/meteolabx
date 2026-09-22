"""Cliente de los paquetes GRIB2 de AROME.

La API "ciblée" que alimenta el resto del visor entrega **un campo por
petición**: montar un perfil vertical cuesta ahí 102 descargas por hora de
predicción. Los paquetes traen el mismo dato agrupado —un GRIB2 multimensaje
con todos los niveles y siete plazos— y transfieren tres veces menos bytes.

El fichero se guarda en disco mientras se usa, se lee mensaje a mensaje y se
descarta: descomprimirlo entero en memoria serían ~1,9 GB.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import fcntl
from functools import lru_cache
import json
import logging
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
logger = logging.getLogger("meteolabx.arome_packages")

# Los paquetes no se reparten en bloques iguales: el primero cubre siete
# plazos (0 a 6) y los siguientes seis, hasta un último de tres. Suponer que
# todos median siete generaba rangos que no existen —07H13H, 14H20H— y la API
# respondía 404, de modo que solo el primer bloque llegaba a descargarse y el
# resto de la pasada acababa resolviéndose campo a campo por el WCS.
BLOCK_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 6), (7, 12), (13, 18), (19, 24), (25, 30),
    (31, 36), (37, 42), (43, 48), (49, 51),
)
MAX_HORIZON_H = BLOCK_BOUNDS[-1][1]
GDAL_CACHE_MB = int(os.getenv("METEOLABX_GDAL_CACHE_MB", "64"))
# Elementos del paquete isobárico IP1, con la clave que usa el perfil.
IP1_ELEMENTS = {
    "TMP": "temperature",
    "RH": "relative_humidity",
    "UGRD": "u",
    "VGRD": "v",
    "GP": "geopotential",
}


# Elementos del paquete IP3. Los nombres de la documentación (TD, VV2) no
# tienen por qué ser los que GDAL expone en GRIB_ELEMENT, así que se admiten
# variantes y se registra lo que trae el fichero cuando no aparece ninguna.
IP3_ELEMENTS: dict[str, tuple[str, ...]] = {
    # Rocío isobárico: el único campo por el que DCAPE seguía pidiendo al WCS.
    # DEPR es T−Td: no puede leerse como Td sin la temperatura ambiental.
    "dewpoint": ("DPT", "TD"),
    # Velocidad vertical geométrica, en m/s (positiva hacia arriba).
    "vertical_velocity": ("DZDT", "VV2", "WZ", "W"),
}


class AromePackageError(RuntimeError):
    """El paquete no se pudo descargar o no contiene lo esperado."""


class AromePackageNotReady(AromePackageError):
    """Transient publication/rate-limit failure, safe to retry within a budget."""

    def __init__(self, message: str, retry_after: float = 15.0):
        super().__init__(message)
        self.retry_after = retry_after


def _cache_dir() -> Path:
    configured = os.getenv("METEOLABX_AROME_PACKAGE_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    # Por omisión, al temporal del contenedor. Conviene apuntarla al volumen
    # con METEOLABX_AROME_PACKAGE_CACHE_DIR cuando haya sitio: el temporal se
    # pierde en cada reinicio y una pasada son unos 8 GB de bloques que habría
    # que volver a bajar, justo cuando el servicio acaba de caerse.
    return Path(tempfile.gettempdir()) / "meteolabx-arome-packages"


def block_range(run: datetime, valid_time: datetime) -> str:
    """Rango de plazos, en el formato `00H06H` que espera la API."""
    horizon = int((valid_time - run).total_seconds() // 3600)
    if horizon < 0:
        raise AromePackageError("La hora pedida es anterior a la pasada.")
    for inicio, fin in BLOCK_BOUNDS:
        if horizon <= fin:
            return f"{inicio:02d}H{fin:02d}H"
    raise AromePackageError(
        f"El plazo +{horizon} h pasa del horizonte de los paquetes "
        f"(+{MAX_HORIZON_H} h)."
    )


def blocks_up_to(horizon_h: int) -> list[int]:
    """Primer plazo de cada bloque necesario para cubrir ese horizonte.

    Sirve para adelantar los paquetes de una pasada entera sin depender de qué
    horas haya anunciado todavía el catálogo.
    """
    return [inicio for inicio, _fin in BLOCK_BOUNDS if inicio <= horizon_h]


def _package_path(package: str, run: datetime, block: str) -> Path:
    stamp = run.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    return _cache_dir() / f"{package}-{stamp}-{block}.grib2"


def _downloads_log(run: datetime) -> Path:
    stamp = run.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    return _cache_dir() / f"downloads-{stamp}.jsonl"


def _record_download(package: str, run: datetime, block: str, size: int, seconds: float, **metrics) -> None:
    """Apunta una descarga para poder resumir después lo que costó la pasada.

    Se escribe en disco, no en memoria: cada trabajo aislado es un proceso
    aparte y lo que bajara allí no llegaría a un acumulador del padre. Una
    línea por descarga, en modo append, que es atómico para líneas cortas y no
    necesita coordinar a nadie.
    """
    linea = json.dumps({
        **metrics,
        "package": package, "block": block,
        "bytes": int(size), "seconds": round(float(seconds), 1),
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }, ensure_ascii=False)
    try:
        with _downloads_log(run).open("a", encoding="utf-8") as registro:
            registro.write(linea + "\n")
    except OSError:
        # Contabilizar no puede estorbar a descargar.
        logger.debug("No se pudo apuntar la descarga de %s %s", package, block)


def _wall_seconds(intervalos: list[tuple[float, float]]) -> float:
    """Tiempo de reloj con al menos una descarga en marcha.

    Sumar la duración de cada descarga cuenta dos veces lo que se solapa: con
    cuatro a la vez, un minuto de reloj eran cuatro «minutos de descarga», y
    los bytes divididos entre esa suma daban la velocidad de cada una, no la
    que entraba al contenedor.
    """
    total = 0.0
    fin_tramo = None
    inicio_tramo = None
    for inicio, fin in sorted(intervalos):
        if fin_tramo is None or inicio > fin_tramo:
            if fin_tramo is not None:
                total += fin_tramo - inicio_tramo
            inicio_tramo, fin_tramo = inicio, fin
        else:
            fin_tramo = max(fin_tramo, fin)
    if fin_tramo is not None:
        total += fin_tramo - inicio_tramo
    return total


def download_stats(run: datetime) -> dict[str, Any]:
    """Cuántos paquetes, cuántos bytes y cuánto tiempo lleva esta pasada.

    `seconds` suma la duración de cada descarga; `wall_seconds` es el tiempo de
    reloj en que hubo alguna activa. Bytes entre el primero dan la velocidad
    media de una descarga; entre el segundo, el caudal real del contenedor.
    """
    resumen = {"packages": 0, "bytes": 0, "seconds": 0.0, "wall_seconds": 0.0}
    try:
        contenido = _downloads_log(run).read_text(encoding="utf-8")
    except OSError:
        return resumen
    intervalos: list[tuple[float, float]] = []
    for linea in contenido.splitlines():
        try:
            registro = json.loads(linea)
        except ValueError:
            continue
        resumen["packages"] += 1
        resumen["bytes"] += int(registro.get("bytes", 0))
        duracion = float(registro.get("seconds", 0.0))
        resumen["seconds"] += duracion
        try:
            # `at` se apunta al terminar, así que el tramo va de at - seconds a at.
            fin = datetime.fromisoformat(str(registro["at"]).replace("Z", "+00:00")).timestamp()
            intervalos.append((fin - duracion, fin))
        except (KeyError, ValueError):
            pass
        if "headers_seconds" in registro:
            resumen["timed_packages"] = resumen.get("timed_packages", 0) + 1
            for key in ("headers_seconds", "first_chunk_seconds", "body_seconds", "transfer_seconds"):
                resumen[key] = round(resumen.get(key, 0.0) + float(registro.get(key, 0.0)), 3)
            resumen["max_observed_downloads"] = max(resumen.get("max_observed_downloads", 0),
                int(registro.get("active_downloads_start", 0)), int(registro.get("active_downloads_end", 0)))
    resumen["seconds"] = round(resumen["seconds"], 1)
    resumen["wall_seconds"] = round(_wall_seconds(intervalos), 1)
    return resumen


def _is_downloaded(destination: Path) -> bool:
    try:
        return destination.stat().st_size > 0
    except OSError:
        return False


def _partial_sizes(destination: Path) -> dict[tuple[str, int], int]:
    """Snapshot only this package's partial files; no locks or mutations."""
    sizes = {}
    for path in destination.parent.glob(f"{destination.stem}.*.part"):
        try:
            stat = path.stat()
            sizes[(path.name, stat.st_ino)] = stat.st_size
        except OSError:
            continue
    return sizes


def ensure_package(package: str, run: datetime, valid_time: datetime, *,
                   lock_timeout_s: float | None = None,
                   download_if_missing: bool = True) -> Path:
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
    espera = time.monotonic()
    with lock_path.open("a+", encoding="ascii") as lock_handle:
        # None: follow growth, not a wall-clock deadline. Explicit timeouts
        # retain their meaning; prefetch uses zero to skip an occupied lock.
        stall_budget = max(0.0, float(os.getenv("METEOLABX_AROME_PACKAGE_STALL_S", "60")))
        previous_sizes = _partial_sizes(destination)
        last_progress = espera
        while True:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if lock_timeout_s is not None:
                    remaining = max(0.0, lock_timeout_s) - (now - espera)
                else:
                    sizes = _partial_sizes(destination)
                    if any(size > previous_sizes.get(key, 0) for key, size in sizes.items()):
                        last_progress = now
                    previous_sizes = sizes
                    remaining = stall_budget - (now - last_progress)
                if remaining <= 0:
                    reason = (f"sin progreso durante {stall_budget:.0f} s" if lock_timeout_s is None
                              else f"tras {max(0.0, lock_timeout_s):.0f} s de espera")
                    raise AromePackageError(f"{package} {block}: descarga en curso {reason}")
                time.sleep(min(0.25, remaining))
        turno = time.monotonic() - espera
        try:
            # Puede haberlo bajado otro mientras esperábamos el turno.
            if _is_downloaded(destination):
                if turno > 1.0:
                    logger.info(
                        "%s %s lo bajó otro proceso; %.0f s de espera en vez "
                        "de una segunda descarga.", package, block, turno
                    )
                return destination
            if not download_if_missing:
                raise AromePackageError(f"{package} {block}: paquete no preparado")
            descarga = time.monotonic()
            resultado = _download_package(package, run, block, destination)
            logger.info(
                "%s %s descargado: %.0f MB en %.0f s%s.",
                package, block,
                resultado.stat().st_size / 1e6,
                time.monotonic() - descarga,
                f" (tras {turno:.0f} s de cola)" if turno > 1.0 else "",
            )
            return resultado
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def package_ready(package: str, run: datetime, valid_time: datetime) -> bool:
    """Indica si el bloque de esa hora ya está descargado, sin bajar nada."""
    try:
        return _is_downloaded(_package_path(package, run, block_range(run, valid_time)))
    except AromePackageError:
        return False


def _active_download_count() -> int:
    """Non-intrusive approximation: includes orphaned .part files after crashes."""
    return sum(1 for _ in _cache_dir().glob("*.part"))


def _download_package(
    package: str, run: datetime, block: str, destination: Path
) -> Path:
    partial = destination.with_suffix(f".{os.getpid()}.part")
    url = f"{PACKAGE_BASE}/models/AROME/grids/0.025/packages/{package}/productARO"
    parameters = {
        "referencetime": run.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time": block, "format": "grib2",
    }
    # Obtain credentials before timing the HTTP request itself.
    headers = authorization_headers()
    started = time.monotonic()
    first_chunk = None
    size = 0
    try:
        # Visible while awaiting HTTP headers as well as while receiving data.
        partial.touch()
        active_start = _active_download_count()
        with requests.get(url, headers=headers, params=parameters,
                          timeout=(30, 1800), stream=True) as response:
            headers_at = time.monotonic()
            if response.status_code != 200:
                message = f"El paquete {package} {block} no está disponible (HTTP {response.status_code})."
                if response.status_code in (404, 429, 500, 502, 503, 504):
                    try:
                        retry_after = max(1.0, float(response.headers.get("Retry-After", "15")))
                    except (TypeError, ValueError):
                        retry_after = 15.0
                    raise AromePackageNotReady(message, retry_after)
                raise AromePackageError(message)
            with partial.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    if first_chunk is None:
                        first_chunk = time.monotonic()
                    handle.write(chunk)
                    size += len(chunk)
        finished = time.monotonic()
        if size == 0:
            raise AromePackageError(f"Paquete vacío: {package} {block}")
        active_end = _active_download_count()
        partial.replace(destination)
        body_seconds = finished - headers_at
        transfer_seconds = finished - first_chunk
        metrics = {
            "headers_seconds": round(headers_at - started, 3),
            "first_chunk_seconds": round(first_chunk - started, 3),
            "body_seconds": round(body_seconds, 3),
            "transfer_seconds": round(transfer_seconds, 3),
            "body_mb_s": round(size / 1e6 / max(body_seconds, 1e-6), 3),
            "active_downloads_start": active_start,
            "active_downloads_end": active_end,
        }
        _record_download(package, run, block, size, finished - started, **metrics)
        logger.info("Descarga %s %s: bytes=%d headers=%.3fs first_chunk=%.3fs "
                    "body=%.3fs transfer=%.3fs body_rate=%.3f MB/s active_start=%d active_end=%d",
                    package, block, size, metrics["headers_seconds"], metrics["first_chunk_seconds"],
                    body_seconds, transfer_seconds, metrics["body_mb_s"],
                    active_start, metrics["active_downloads_end"])
    except requests.RequestException as exc:
        raise AromePackageError(f"No se pudo descargar {package} {block}: {exc}") from exc
    finally:
        partial.unlink(missing_ok=True)
    return destination


class IsobaricLevels:
    """Los niveles de un elemento, descodificados cuando se piden.

    Un perfil devuelto de una vez mantiene sus veinticuatro niveles en float64
    hasta que quien llama termina de montar el suyo: sobre el dominio entero
    son ochenta megas por elemento, vivos durante todo el montaje y encima de
    los perfiles que se están llenando. Pedidos de uno en uno, cada capa muere
    en la misma vuelta en que se escribió.

    No guarda lo que entrega: pedir dos veces el mismo nivel lo descodifica dos
    veces. Quien lo necesite más de una vez debe quedárselo él.
    """

    __slots__ = ("_package", "_bands")

    def __init__(self, package: "IsobaricPackage", bands: dict[float, int]):
        self._package = package
        self._bands = dict(bands)

    def __contains__(self, level: float) -> bool:
        return float(level) in self._bands

    def __len__(self) -> int:
        return len(self._bands)

    def __iter__(self):
        return iter(self._bands)

    def __getitem__(self, level: float) -> np.ndarray:
        return self._package.decode(self._bands[float(level)])

    def keys(self):
        return self._bands.keys()

    def values(self):
        for level in self._bands:
            yield self[level]

    def items(self):
        for level in self._bands:
            yield level, self[level]

    def clear(self) -> None:
        """Suelta estos niveles y, con el último, cierra el fichero."""
        if self._bands:
            self._bands.clear()
            self._package.release()


class IsobaricPackage:
    """Un paquete isobárico abierto, con el mapa de dónde vive cada nivel.

    El índice se construye leyendo etiquetas, que son texto: lo caro es
    descomprimir, y eso se deja para cuando alguien pida el nivel.
    """

    def __init__(self, stack: ExitStack, dataset: Any, bands: dict[str, dict[float, int]], seen: set[str]):
        self._stack = stack
        self._dataset = dataset
        self._bands = bands
        self.seen = seen
        self.geometry = (dataset.transform, dataset.crs, dataset.bounds)
        self._holders = 0

    @property
    def elements(self) -> tuple[str, ...]:
        return tuple(self._bands)

    def fields(self, element: str) -> IsobaricLevels:
        """Los niveles de un elemento. Quien los pide se compromete a soltarlos."""
        self._holders += 1
        return IsobaricLevels(self, self._bands.get(element, {}))

    def decode(self, band: int) -> np.ndarray:
        # El límite de caché de GDAL se aplica en cada lectura: sin él se queda
        # con un porcentaje de la RAM de la máquina, que sobre medio giga de
        # GRIB era casi un giga por proceso.
        # El paquete marca las celdas fuera del dominio con 9999; el resto del
        # pipeline espera NaN, igual que entrega el WCS.
        with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB):
            values = self._dataset.read(band, masked=True).astype(float, copy=False)
        return values.filled(np.nan)

    def release(self) -> None:
        self._holders -= 1
        if self._holders <= 0:
            self.close()

    def close(self) -> None:
        self._bands = {}
        self._stack.close()


def _index_isobaric_bands(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    by_element: dict[str, str],
) -> IsobaricPackage:
    """Abre el paquete y localiza cada nivel sin descodificar ninguno."""
    wanted_levels = {int(round(level * 100)) for level in levels_hpa}
    stamp = int(valid_time.astimezone(timezone.utc).timestamp())
    bands: dict[str, dict[float, int]] = {}
    seen: set[str] = set()
    stack = ExitStack()
    try:
        # Env es local al hilo y debe cerrarse en orden LIFO. IP1/IP3
        # permanecen abiertos y se liberan en distinto orden: no conservar
        # sus Env en los ExitStack de los datasets.
        with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB):
            dataset = stack.enter_context(rasterio.open(path))
            for index in range(1, dataset.count + 1):
                tags = dataset.tags(index)
                element = tags.get("GRIB_ELEMENT", "")
                seen.add(element)
                name = by_element.get(element)
                if name is None:
                    continue
                if int(tags.get("GRIB_VALID_TIME", -1)) != stamp:
                    continue
                short_name = tags.get("GRIB_SHORT_NAME", "")
                if not short_name.endswith("-ISBL"):
                    continue
                level_pa = int(short_name.split("-", 1)[0])
                if level_pa not in wanted_levels:
                    continue
                bands.setdefault(name, {})[level_pa / 100.0] = index
    except BaseException:
        stack.close()
        raise
    return IsobaricPackage(stack, dataset, bands, seen)


def open_isobaric_profile(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    elements: tuple[str, ...] = (),
) -> IsobaricPackage:
    """IP1 abierto y indexado, para leerlo nivel a nivel.

    `elements` limita lo que se podrá descodificar. Descomprimir un elemento
    que nadie va a mirar son seis megas por nivel: la cizalladura sólo necesita
    el viento, y descodificarle además temperatura, humedad y geopotencial es
    tirar la mitad del trabajo.

    La geometría es la del paquete, no la de quien pregunta: son rejillas que
    pueden no coincidir —un recorte del WCS frente al dominio completo del
    GRIB— y darles la ajena convierte los valores en basura sin avisar.
    """
    buscados = set(elements) if elements else set(IP1_ELEMENTS.values())
    _log_package_decode(path.name, tuple(sorted(buscados)))
    desconocidos = buscados - set(IP1_ELEMENTS.values())
    if desconocidos:
        raise AromePackageError(
            f"IP1 no publica {', '.join(sorted(desconocidos))}."
        )
    package = _index_isobaric_bands(
        path,
        valid_time,
        levels_hpa,
        {clave: nombre for clave, nombre in IP1_ELEMENTS.items() if nombre in buscados},
    )
    faltan = sorted(buscados - set(package.elements))
    if faltan:
        package.close()
        raise AromePackageError(
            f"El paquete no trae {', '.join(faltan)} para "
            f"{valid_time:%Y-%m-%dT%H:%M}Z."
        )
    return package


def open_isobaric_extras(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    wanted: dict[str, tuple[str, ...]],
) -> IsobaricPackage:
    """IP3 abierto e indexado, para leerlo nivel a nivel.

    Acepta varias grafías por campo porque los nombres de la documentación no
    coinciden necesariamente con los que expone GDAL. Si alguno no aparece se
    registran los elementos que sí trae el fichero: es la forma de averiguar
    cómo se llaman de verdad sin tener que adivinar dos veces.
    """
    package = _index_isobaric_bands(
        path,
        valid_time,
        levels_hpa,
        {grafia: nombre for nombre, grafias in wanted.items() for grafia in grafias},
    )
    faltan = [nombre for nombre in wanted if nombre not in package.elements]
    if faltan:
        logger.info(
            "IP3 no trae %s con los nombres esperados. Elementos del fichero: %s",
            ", ".join(faltan), ", ".join(sorted(package.seen)),
        )
    else:
        # Aunque salga todo: saber qué más trae el paquete es lo que permite
        # decidir si un campo nuevo cuesta una descarga o ya está pagado.
        _log_package_inventory(path.name, tuple(sorted(package.seen)))
    return package


def read_isobaric_profile(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    elements: tuple[str, ...] = (),
) -> tuple[dict[str, dict[float, np.ndarray]], tuple[Any, Any, Any]]:
    """Campos del perfil para una hora, descodificados de una vez.

    Devuelve `({"temperature": {850.0: array, ...}, ...}, geometría)` con las
    unidades tal cual las publica el paquete: °C, %, m/s y m²/s².

    Quien monte un perfil nivel a nivel debe usar `open_isobaric_profile`: esto
    retiene todos los niveles a la vez.
    """
    package = open_isobaric_profile(path, valid_time, levels_hpa, elements)
    try:
        return (
            {name: dict(package.fields(name).items()) for name in package.elements},
            package.geometry,
        )
    finally:
        package.close()


def read_isobaric_extras(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    wanted: dict[str, tuple[str, ...]],
) -> tuple[dict[str, dict[float, np.ndarray]], tuple[Any, Any, Any]]:
    """Campos isobáricos de IP3 para una hora, descodificados de una vez."""
    package = open_isobaric_extras(path, valid_time, levels_hpa, wanted)
    try:
        return (
            {
                name: dict(package.fields(name).items())
                if name in package.elements
                else {}
                for name in wanted
            },
            package.geometry,
        )
    finally:
        package.close()


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
            values = dataset.read(index, masked=True).astype(float, copy=False)
            salida[destino[0]] = (values.filled(np.nan), destino[1])
    return salida, geometria


def read_isobaric_extras(
    path: Path,
    valid_time: datetime,
    levels_hpa: list[float],
    wanted: dict[str, tuple[str, ...]],
) -> tuple[dict[str, dict[float, np.ndarray]], tuple[Any, Any, Any]]:
    """Campos isobáricos de IP3 para una hora, leídos mensaje a mensaje.

    Acepta varias grafías por campo porque los nombres de la documentación no
    coinciden necesariamente con los que expone GDAL. Si alguno no aparece se
    registran los elementos que sí trae el fichero: es la forma de averiguar
    cómo se llaman de verdad sin tener que adivinar dos veces.
    """
    wanted_levels = {int(round(level * 100)) for level in levels_hpa}
    stamp = int(valid_time.astimezone(timezone.utc).timestamp())
    por_elemento = {
        grafia: nombre for nombre, grafias in wanted.items() for grafia in grafias
    }
    salida: dict[str, dict[float, np.ndarray]] = {nombre: {} for nombre in wanted}
    vistos: set[str] = set()
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB), rasterio.open(path) as dataset:
        geometria = (dataset.transform, dataset.crs, dataset.bounds)
        for index in range(1, dataset.count + 1):
            tags = dataset.tags(index)
            elemento = tags.get("GRIB_ELEMENT", "")
            vistos.add(elemento)
            nombre = por_elemento.get(elemento)
            if nombre is None:
                continue
            if int(tags.get("GRIB_VALID_TIME", -1)) != stamp:
                continue
            short_name = tags.get("GRIB_SHORT_NAME", "")
            if not short_name.endswith("-ISBL"):
                continue
            level_pa = int(short_name.split("-", 1)[0])
            if level_pa not in wanted_levels:
                continue
            values = dataset.read(index, masked=True).astype(float, copy=False)
            salida[nombre][level_pa / 100.0] = values.filled(np.nan)
    faltan = [nombre for nombre, campos in salida.items() if not campos]
    if faltan:
        logger.info(
            "IP3 no trae %s con los nombres esperados. Elementos del fichero: %s",
            ", ".join(faltan), ", ".join(sorted(vistos)),
        )
    else:
        # Aunque salga todo: saber qué más trae el paquete es lo que permite
        # decidir si un campo nuevo cuesta una descarga o ya está pagado.
        _log_package_inventory(path.name, tuple(sorted(vistos)))
    return salida, geometria


@lru_cache(maxsize=32)
def _log_package_decode(nombre: str, elementos: tuple[str, ...]) -> None:
    """Deja constancia de qué se descodifica, una vez por fichero y peticion.

    Cada elemento son unos seis megas por nivel, así que la diferencia entre
    pedir tres y pedir cinco son 150 MB por perfil: sin esta línea no hay forma
    de comprobar desde fuera que la lectura selectiva sigue en pie.
    """
    logger.info(
        "%s: se descodifican %s.", nombre.split("-")[0], ", ".join(elementos)
    )


@lru_cache(maxsize=8)
def _log_package_inventory(nombre: str, elementos: tuple[str, ...]) -> None:
    """Deja constancia de lo que trae un paquete, una vez por fichero."""
    logger.info("%s contiene: %s", nombre.split("-")[0], ", ".join(elementos))


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
        candidates += list(_cache_dir().glob("downloads-*.jsonl"))
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

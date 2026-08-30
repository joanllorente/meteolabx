"""Cliente del open data de ECMWF: IFS 0,25° por rangos de bytes.

Cada plazo de la pasada es un GRIB2 de unos 140 MB con 184 mensajes dentro.
Bajarlo entero para leer dos campos costaría más que toda la pasada de AROME,
así que se usa el fichero `.index` que ECMWF publica al lado: una línea JSON
por mensaje con su desplazamiento y su longitud. Con eso, un mapa de Z500 y
presión son dos peticiones parciales de ~0,9 MB en total.

El coste de un frame es entonces descarga y decodificación, sin perfiles
verticales: segundos, no minutos. Es lo que permite añadir el modelo sin
desplazar el trabajo convectivo de AROME.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterable

import numpy as np
import rasterio
from rasterio.windows import from_bounds
import requests

from server.services.forecast_grid import pack_grid
from server.services.forecast_store import (
    frame_key,
    get_forecast_store,
    latest_manifest_key,
    mark_available,
    mark_error,
    new_manifest,
    prune_retained_runs,
    read_json,
    register_run_slot,
    run_manifest_key,
    write_grid,
    write_json,
)


logger = logging.getLogger("meteolabx.ecmwf_forecast")

FORECAST_MODEL = "ecmwf"
MODEL_LABEL = "ECMWF IFS"
RESOLUTION_LABEL = "0,25°"
BASE_URL = "https://data.ecmwf.int/forecasts"
RESOLUTION = "0p25"
# Desde el 0,25° las cuatro pasadas van en `oper`; `scda` era el nombre del
# flujo de corte corto en las resoluciones antiguas y aquí devuelve 404.
STREAM = "oper"

# La rejilla nativa es global —1440 × 721 = 1.038.240 celdas—, pero un mapa de
# Z500 se mira sobre el Atlántico y Europa. Recortar en la lectura deja el
# frame en ~120.000 celdas: descarga igual, memoria y volumen mucho menores.
# Los límites llevan media celda fuera, como los de AROME.
DEFAULT_DOMAIN = (-80.125, 14.875, 45.125, 75.125)

# Hasta +144 h las cuatro pasadas publican cada 3 h. Las 00 y 12Z siguen hasta
# +360 h cada 6 h; ese tramo se deja fuera por defecto para que el primer mapa
# no dispare ni el tiempo ni el volumen.
STEP_HOURS = 3
DEFAULT_MAX_HORIZON_H = 144
# ECMWF publica el 0,25° alrededor de siete horas después de la pasada.
PUBLICATION_DELAY_H = 6

PRODUCTS: dict[str, dict[str, Any]] = {
    "z500-mslp": {
        "label": "Geopotencial 500 hPa y presión al nivel del mar",
        "unit": "dam",
        "vmin": 480.0,
        "vmax": 600.0,
        "overlay_unit": "hPa",
        # Altura geopotencial en gpm; el visor la enseña en decámetros, que es
        # como se rotula el mapa sinóptico de toda la vida.
        "value": {"param": "gh", "levtype": "pl", "levelist": "500", "scale": 0.1},
        # Presión al nivel del mar en Pa.
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
    },
}


class EcmwfError(RuntimeError):
    """La pasada no está publicada o el mensaje pedido no aparece."""


def domain_bounds() -> tuple[float, float, float, float]:
    """Recorte del dominio, configurable sin tocar el código."""
    crudo = os.getenv("METEOLABX_ECMWF_DOMAIN", "").strip()
    if not crudo:
        return DEFAULT_DOMAIN
    try:
        oeste, sur, este, norte = (float(parte) for parte in crudo.split(","))
    except ValueError:
        logger.warning(
            "METEOLABX_ECMWF_DOMAIN=%r no son cuatro números "
            "«oeste,sur,este,norte»; se usa el dominio por defecto.", crudo
        )
        return DEFAULT_DOMAIN
    return (oeste, sur, este, norte)


def max_horizon_h() -> int:
    try:
        return max(0, int(os.getenv("METEOLABX_ECMWF_MAX_HORIZON_H", str(DEFAULT_MAX_HORIZON_H))))
    except ValueError:
        return DEFAULT_MAX_HORIZON_H


def candidate_steps() -> tuple[int, ...]:
    return tuple(range(0, max_horizon_h() + 1, STEP_HOURS))


def _run_stamp(run: datetime) -> str:
    return run.astimezone(timezone.utc).strftime("%Y%m%d")


def _run_hour(run: datetime) -> str:
    return run.astimezone(timezone.utc).strftime("%H")


def _file_base(run: datetime, step: int) -> str:
    dia = _run_stamp(run)
    hora = _run_hour(run)
    return (
        f"{BASE_URL}/{dia}/{hora}z/ifs/{RESOLUTION}/{STREAM}"
        f"/{dia}{hora}0000-{step}h-{STREAM}-fc"
    )


def index_url(run: datetime, step: int) -> str:
    return f"{_file_base(run, step)}.index"


def grib_url(run: datetime, step: int) -> str:
    return f"{_file_base(run, step)}.grib2"


def _timeout() -> tuple[float, float]:
    return (10.0, float(os.getenv("METEOLABX_ECMWF_TIMEOUT_S", "120")))


def read_index(run: datetime, step: int) -> list[dict[str, Any]]:
    """Mensajes del plazo, con su desplazamiento dentro del GRIB.

    Son 40 KB por plazo y no cambian una vez publicados, así que se cachean:
    los dos campos del mapa salen del mismo índice.
    """
    return _read_index_cached(run.astimezone(timezone.utc).isoformat(), int(step))


@lru_cache(maxsize=256)
def _read_index_cached(run_iso: str, step: int) -> list[dict[str, Any]]:
    run = datetime.fromisoformat(run_iso)
    url = index_url(run, step)
    try:
        respuesta = requests.get(url, timeout=_timeout())
    except requests.RequestException as exc:
        raise EcmwfError(f"No se pudo leer el índice de +{step} h: {exc}") from exc
    if respuesta.status_code != 200:
        raise EcmwfError(
            f"El plazo +{step} h todavía no está publicado "
            f"(HTTP {respuesta.status_code})."
        )
    mensajes = []
    for linea in respuesta.text.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            mensajes.append(json.loads(linea))
        except ValueError:
            continue
    if not mensajes:
        raise EcmwfError(f"El índice de +{step} h vino vacío.")
    return mensajes


def _select_message(
    mensajes: Iterable[dict[str, Any]], selector: dict[str, Any]
) -> dict[str, Any]:
    claves = {
        clave: str(valor)
        for clave, valor in selector.items()
        if clave in {"param", "levtype", "levelist"}
    }
    for mensaje in mensajes:
        if all(str(mensaje.get(clave, "")) == valor for clave, valor in claves.items()):
            return mensaje
    descripcion = " ".join(f"{k}={v}" for k, v in claves.items())
    raise EcmwfError(f"El índice no trae ningún mensaje con {descripcion}.")


def _download_message(run: datetime, step: int, mensaje: dict[str, Any]) -> Path:
    """Baja un solo mensaje GRIB por rango de bytes, a un fichero temporal."""
    inicio = int(mensaje["_offset"])
    fin = inicio + int(mensaje["_length"]) - 1
    url = grib_url(run, step)
    destino = Path(tempfile.mkdtemp(prefix="meteolabx-ecmwf-")) / "mensaje.grib2"
    try:
        with requests.get(
            url,
            headers={"Range": f"bytes={inicio}-{fin}"},
            timeout=_timeout(),
            stream=True,
        ) as respuesta:
            if respuesta.status_code not in (200, 206):
                raise EcmwfError(
                    f"El servidor no sirvió el rango pedido de +{step} h "
                    f"(HTTP {respuesta.status_code})."
                )
            with destino.open("wb") as fichero:
                for trozo in respuesta.iter_content(1024 * 256):
                    fichero.write(trozo)
    except requests.RequestException as exc:
        raise EcmwfError(f"No se pudo descargar {mensaje.get('param')}: {exc}") from exc
    return destino


def _read_message_window(
    ruta: Path, bounds: tuple[float, float, float, float]
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Lee el recorte del dominio, no la rejilla global entera.

    GDAL ya entrega el 0,25° con las longitudes en −180…180 y el norte arriba,
    así que la ventana se saca directamente de los límites pedidos.
    """
    with rasterio.Env(GDAL_CACHEMAX=64), rasterio.open(ruta) as dataset:
        ventana = from_bounds(*bounds, transform=dataset.transform).round_offsets().round_lengths()
        # Un dominio que se salga de la rejilla se recorta a lo que existe.
        ventana = ventana.intersection(
            rasterio.windows.Window(0, 0, dataset.width, dataset.height)
        )
        valores = dataset.read(1, window=ventana, masked=True)
        reales = dataset.window_bounds(ventana)
    datos = np.asarray(valores.filled(np.nan), dtype="float64")
    return datos, tuple(float(valor) for valor in reales)


def _field(
    run: datetime, step: int, selector: dict[str, Any], bounds
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    mensaje = _select_message(read_index(run, step), selector)
    ruta = _download_message(run, step, mensaje)
    try:
        datos, reales = _read_message_window(ruta, bounds)
    finally:
        ruta.unlink(missing_ok=True)
        try:
            ruta.parent.rmdir()
        except OSError:
            pass
    return datos * float(selector.get("scale", 1.0)), reales


def frame_payload(
    product_id: str, run: datetime, step: int
) -> tuple[bytes, dict[str, str]]:
    """Rejilla lista para el visor, en el mismo formato binario que AROME."""
    config = PRODUCTS.get(product_id)
    if config is None:
        raise EcmwfError(f"ECMWF no publica el mapa «{product_id}».")
    bounds = domain_bounds()
    empezado = time.monotonic()
    valores, reales = _field(run, step, config["value"], bounds)
    overlay, _ = _field(run, step, config["overlay"], bounds)
    valid_time = run.astimezone(timezone.utc) + timedelta(hours=step)
    run_iso = run.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    valid_iso = valid_time.isoformat().replace("+00:00", "Z")
    contenido = pack_grid(
        product_id,
        valores,
        bounds=reales,
        unit=str(config["unit"]),
        vmin=float(config["vmin"]),
        vmax=float(config["vmax"]),
        overlay=overlay,
        overlay_unit=str(config["overlay_unit"]),
        metadata={
            "run": run_iso,
            "valid_time": valid_iso,
            "forecast_model": FORECAST_MODEL,
            "calculation_scope": "model",
            # El visor pide las fronteras por dominio; las de ECMWF no son las
            # de AROME, así que el ámbito las distingue en su caché.
            "boundary_scope": FORECAST_MODEL,
            "vertical_kind": None,
            "level": None,
        },
    )
    logger.info(
        "ECMWF %s +%d h: %d × %d celdas en %.1f s",
        product_id, step, valores.shape[1], valores.shape[0],
        time.monotonic() - empezado,
    )
    return contenido, {
        "X-MeteoLabX-Model": FORECAST_MODEL,
        "X-MeteoLabX-Run": run_iso,
        "X-MeteoLabX-Valid-Time": valid_iso,
    }


def parse_run(run_iso: str) -> datetime:
    return datetime.fromisoformat(str(run_iso).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def step_of(run: datetime, valid_iso: str) -> int:
    valido = parse_run(valid_iso)
    horas = (valido - run.astimezone(timezone.utc)).total_seconds() / 3600.0
    step = int(round(horas))
    if abs(horas - step) > 1e-6 or step < 0 or step % STEP_HOURS:
        raise EcmwfError(f"«{valid_iso}» no es un plazo de la pasada {run:%Y-%m-%dT%HZ}.")
    return step


def _index_exists(run: datetime, step: int) -> bool:
    try:
        respuesta = requests.head(index_url(run, step), timeout=_timeout())
    except requests.RequestException:
        return False
    return respuesta.status_code == 200


def available_steps(run: datetime) -> tuple[int, ...]:
    """Plazos ya publicados de esa pasada, comprobados en paralelo.

    Son 49 peticiones HEAD de nada, y evitan anunciar en el visor horas que
    todavía no existen —que es lo que convierte un mapa vacío en un error.
    """
    pasos = candidate_steps()
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ecmwf-head") as pool:
        presentes = list(pool.map(lambda paso: _index_exists(run, paso), pasos))
    return tuple(paso for paso, existe in zip(pasos, presentes) if existe)


def candidate_runs(ahora: datetime | None = None) -> list[datetime]:
    """Pasadas 00/06/12/18Z plausibles, de la más reciente a la más antigua."""
    momento = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ultima = momento - timedelta(hours=PUBLICATION_DELAY_H)
    base = ultima.replace(minute=0, second=0, microsecond=0, hour=(ultima.hour // 6) * 6)
    return [base - timedelta(hours=6 * salto) for salto in range(5)]


def latest_run(ahora: datetime | None = None) -> datetime:
    """Pasada más reciente cuyo plazo 0 esté publicado."""
    for run in candidate_runs(ahora):
        if _index_exists(run, 0):
            return run
    raise EcmwfError("Ninguna pasada reciente de ECMWF está publicada todavía.")


def catalog_payload(run: datetime | None = None) -> dict[str, Any]:
    """Catálogo con la misma forma que el de AROME, para el mismo visor."""
    pasada = run or latest_run()
    pasos = available_steps(pasada)
    run_iso = pasada.isoformat().replace("+00:00", "Z")
    horas = [
        (pasada + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
        for paso in pasos
    ]
    oeste, sur, este, norte = domain_bounds()
    return {
        "model": MODEL_LABEL,
        "resolution": RESOLUTION_LABEL,
        "domain": {
            "label": "Recorte euroatlántico del IFS global",
            "calculation_scope": "model",
            "bounds": [oeste, sur, este, norte],
        },
        "products": {
            product_id: {
                "run": run_iso,
                "valid_times": horas,
                "vmax": config["vmax"],
                "unit": config["unit"],
            }
            for product_id, config in PRODUCTS.items()
        },
        "unavailable_products": {},
    }


def expected_times(run: datetime) -> list[str]:
    """Todas las horas que la pasada llegará a tener, publicadas o no."""
    return [
        (run + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
        for paso in candidate_steps()
    ]


def domain_boundaries() -> list[dict[str, Any]]:
    """Contornos del recorte euroatlántico, sin divisiones administrativas.

    Sobre un dominio que va de Terranova a los Urales, los límites de provincia
    de medio mundo son varios megas de payload y ruido visual encima de un
    mapa sinóptico. Se sirven solo fronteras nacionales y costas.
    """
    # Tarde a propósito: `arome_forecast` arrastra Streamlit por
    # `tabs.arome_forecast`, y el worker de ECMWF no lo necesita para calcular.
    from server.services.arome_forecast import boundaries_for_bounds

    # Y con mucha menos resolución: el detalle de 1:10 m que necesita un mapa
    # de 2,5 km sobre Cataluña son megas de costa noruega en un dominio que va
    # de Terranova a los Urales, donde una celda del modelo mide 25 km.
    return boundaries_for_bounds(
        domain_bounds(),
        scope=FORECAST_MODEL,
        include_admin1=False,
        simplify=0.03,
    )


def run_cycle(max_frames: int = 0) -> dict[str, Any]:
    """Publica los frames de ECMWF que falten de la pasada más reciente.

    Va aparte del grafo de trabajos de AROME a propósito: aquí no hay perfiles
    ni niveles, solo plazos independientes que cuestan segundos. Mezclarlos con
    los niveles convectivos habría dado a los dos modelos una cola común donde
    un fallo de ECMWF podía retrasar un diagnóstico.
    """
    store = get_forecast_store()
    run = latest_run()
    run_iso = run.isoformat().replace("+00:00", "Z")
    catalogo = catalog_payload(run)
    manifiesto = read_json(store, run_manifest_key(run_iso, model=FORECAST_MODEL))
    if not manifiesto:
        manifiesto = new_manifest(
            run_iso,
            expected_times(run),
            catalog_products=catalogo["products"],
            model=FORECAST_MODEL,
        )
    manifiesto["catalog_products"] = catalogo["products"]
    manifiesto["expected_totals"] = {
        product_id: len(candidate_steps()) for product_id in PRODUCTS
    }

    publicados = 0
    fallos = 0
    for product_id in PRODUCTS:
        disponibles = set(
            (manifiesto.get("products", {}).get(product_id) or {}).get(
                "available_times", ()
            )
        )
        for valid_iso in catalogo["products"][product_id]["valid_times"]:
            if valid_iso in disponibles:
                continue
            if max_frames and publicados >= max_frames:
                break
            clave = frame_key(
                run_iso, product_id, valid_iso, model=FORECAST_MODEL
            )
            try:
                contenido, _ = frame_payload(
                    product_id, run, step_of(run, valid_iso)
                )
                write_grid(store, clave, contenido)
            except (EcmwfError, OSError) as exc:
                fallos += 1
                mark_error(manifiesto, product_id, valid_iso, str(exc))
                logger.warning("ECMWF %s %s: %s", product_id, valid_iso, exc)
                continue
            mark_available(manifiesto, product_id, valid_iso)
            publicados += 1

    pendientes = sum(
        len(catalogo["products"][product_id]["valid_times"])
        - len((manifiesto["products"].get(product_id) or {}).get("available_times", ()))
        for product_id in PRODUCTS
    )
    disponibles_total = sum(
        len((manifiesto["products"].get(product_id) or {}).get("available_times", ()))
        for product_id in PRODUCTS
    )
    total = len(candidate_steps()) * len(PRODUCTS)
    manifiesto["status"] = "complete" if pendientes <= 0 else "publishing"
    manifiesto["progress"] = {
        "frames_available": disponibles_total,
        "frames_total": total,
        "percent": round(100.0 * disponibles_total / total, 1) if total else 0.0,
        "error_count": fallos,
        "current_job": None,
        "active_jobs": [],
        "last_completed": None,
    }
    manifiesto["worker_heartbeat_at"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    write_json(store, run_manifest_key(run_iso, model=FORECAST_MODEL), manifiesto)
    write_json(store, latest_manifest_key(FORECAST_MODEL), manifiesto)
    register_run_slot(store, manifiesto)
    prune_retained_runs(store, model=FORECAST_MODEL)
    return {
        "model": FORECAST_MODEL,
        "run": run_iso,
        "frames_published": publicados,
        "failures": fallos,
        "status": manifiesto["status"],
        "progress": manifiesto["progress"],
    }

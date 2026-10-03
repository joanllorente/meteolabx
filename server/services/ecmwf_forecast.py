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
from contextvars import ContextVar, copy_context
import threading
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
from server.services.map_projection import covering_bounds
from server.services.synoptic_diagnostics import coordinates, eady_growth_rate, frontogenesis, q_vectors, smooth

# σ del suavizado de la vorticidad, en celdas de 0,25° (≈ 28 km).
VORTICITY_SIGMA = 1.0
# σ del suavizado de ω, en celdas de 0,25° (≈ 40 km).
OMEGA_SIGMA = 1.5
# σ del suavizado de Eady, en celdas de 0,25° (≈ 40 km): quita el rizado del
# viento sin mover las zonas baroclinas.
EADY_SIGMA = 1.5
from server.services.forecast_store import (
    frame_key,
    ECMWF_PRODUCT_REVISIONS,
    get_forecast_store,
    latest_manifest_key,
    mark_available,
    mark_error,
    new_manifest,
    prune_retained_runs,
    read_json,
    delete_run,
    register_run_slot,
    retained_manifests,
    run_slots_key,
    run_slug,
    run_manifest_key,
    write_grid,
    write_json,
)
from server.services.grib_page_cache import release_completed_map_cache


logger = logging.getLogger("meteolabx.ecmwf_forecast")

FORECAST_MODEL = "ecmwf"
MODEL_LABEL = "ECMWF IFS"
RESOLUTION_LABEL = "0,25°"
BASE_URL = "https://data.ecmwf.int/forecasts"
RESOLUTION = "0p25"
# Desde el 0,25° las cuatro pasadas van en `oper`; `scda` era el nombre del
# flujo de corte corto en las resoluciones antiguas y aquí devuelve 404.
STREAM = "oper"

# La rejilla nativa es global —1440 × 721 = 1.038.240 celdas—, pero cada mapa
# se mira sobre un dominio. Recortar en la lectura deja la descarga igual y la
# memoria y el volumen mucho menores.
# Los límites llevan media celda fuera, como los de AROME.
#
# Dominios regionales, definidos como rectángulos de una cónica conforme de
# Lambert (LCC): centro, paralelos estándar y tamaño en km. El visor pinta en
# esa proyección, que conserva las formas; en latitud y longitud tal cual, el
# este-oeste salía estirado por 1/cos φ, el triple a 70° N. Lo que se lee de
# ECMWF es el recuadro de latitud y longitud que cubre el rectángulo entero,
# para que no queden esquinas vacías. Todos caben en −180…180: ninguno cruza el
# antimeridiano, y por eso Asia oriental y Australia no van más al este.
DEFAULT_DOMAIN_ID = "europe"
DOMAINS: dict[str, dict[str, Any]] = {
    "europe": {"label": "Europa y Atlántico", "projection": {
        "lon0": 12.0, "lat0": 52.0, "lat1": 35.0, "lat2": 65.0, "width_km": 7400, "height_km": 5400}},
    # Cierra el hueco de Siberia occidental entre Europa y Asia oriental, y
    # baja al mar Arábigo sin llegar al ecuador.
    "middle-east": {"label": "Oriente Medio y Asia central", "projection": {
        "lon0": 62.0, "lat0": 38.0, "lat1": 25.0, "lat2": 50.0, "width_km": 7400, "height_km": 5400}},
    "north-america": {"label": "Norteamérica", "projection": {
        "lon0": -100.0, "lat0": 45.0, "lat1": 30.0, "lat2": 60.0, "width_km": 8600, "height_km": 6000}},
    # Hasta Filipinas por el sur, donde se forman buena parte de los tifones.
    "east-asia": {"label": "Asia oriental y Pacífico", "projection": {
        "lon0": 120.0, "lat0": 40.0, "lat1": 28.0, "lat2": 52.0, "width_km": 7000, "height_km": 5800}},
    "south-america": {"label": "Sudamérica", "projection": {
        "lon0": -62.0, "lat0": -35.0, "lat1": -20.0, "lat2": -50.0, "width_km": 6800, "height_km": 5800}},
    "australia": {"label": "Australia y Nueva Zelanda", "projection": {
        "lon0": 133.0, "lat0": -32.0, "lat1": -20.0, "lat2": -45.0, "width_km": 6800, "height_km": 5000}},
}
for _dominio in DOMAINS.values():
    _dominio["bounds"] = covering_bounds(_dominio["projection"])
DEFAULT_DOMAIN = DOMAINS[DEFAULT_DOMAIN_ID]["bounds"]

# Niveles del perfil de la intensidad potencial: todos los del open data
# hasta 50 hPa, que es donde tcpyPI deja de mirar el sondeo.
GPI_LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50)
# σ del suavizado de la vorticidad absoluta de 850 hPa, en celdas de 0,25°
# (≈ 55 km). El índice se ajustó con reanálisis de 2,5°: a 0,25° la vorticidad
# cruda trae núcleos convectivos de una celda que, elevados a 3/2, salpicaban
# el mapa de máximos sin sentido sinóptico.
GPI_VORTICITY_SIGMA = 2.0
# A 0,25° y con campos instantáneos los ingredientes son mucho más extremos
# que las medias mensuales de 2,5° con que se calibró el índice: un sistema
# tropical organizado pasa de 300 y un ambiente propicio en latitudes medias
# ronda 20-100. El visor pinta la escala en tramos casi logarítmicos.
GPI_VMAX = 200.0
# Fracción de tierra por encima de la cual la celda no tiene mar que medir.
SEA_MAX_LAND_FRACTION = 0.1
OMEGA_EARTH = 7.292e-5

# Hasta +144 h las cuatro pasadas publican cada 3 h. Las 00 y 12Z siguen hasta
# +360 h cada 6 h; ese tramo se deja fuera por defecto para que el primer mapa
# no dispare ni el tiempo ni el volumen.
STEP_HOURS = 3
DEFAULT_MAX_HORIZON_H = 144
# ECMWF publica el 0,25° alrededor de siete horas después de la pasada.
PUBLICATION_DELAY_H = 6
# Esperas ante un 429 o 503 del servidor de datos abiertos, en segundos.
RATE_LIMIT_BACKOFF_S = (3.0, 8.0, 20.0)

PRODUCTS: dict[str, dict[str, Any]] = {
    "ecmwf-mslp-theta-e-850": {
        # El mapa de masas de aire, como el de AROME: θe de 850 hPa en color
        # y la presión al nivel del mar en isobaras.
        "label": "θₑ 850 hPa y presión al nivel del mar",
        "unit": "°C",
        "vmin": -10.0,
        "vmax": 60.0,
        "overlay_unit": "hPa",
        "kind": "theta_e",
        "level": 850,
        # Presión al nivel del mar en Pa.
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
    },
    # Precipitación de las 6 horas que acaban en el plazo, con la presión al
    # nivel del mar. `tp` es acumulada desde el inicio, en metros de agua: se
    # resta el plazo de 6 horas antes. No existe antes de la +6.
    "ecmwf-precip-6h": {
        "label": "Precipitación en 6 horas y presión al nivel del mar",
        "unit": "mm", "vmin": 0.0, "vmax": 60.0, "overlay_unit": "hPa",
        "kind": "precip_accum", "hours": 6, "min_step": 6,
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
    },
    # Precipitación acumulada desde el inicio de la pasada: `tp` tal cual, en
    # milímetros. El visor la usa en ventana móvil —lo caído entre A y B es la
    # resta de dos plazos—, así que no lleva isobaras: la presión de B no
    # describe un intervalo. Sale de los mismos mensajes que el de 6 horas, así
    # que no añade descargas. En la +0 es cero por definición.
    "ecmwf-precip-accumulated": {
        "label": "Precipitación acumulada", "unit": "mm", "vmin": 0.0, "vmax": 800.0,
        "kind": "precip_total", "min_step": STEP_HOURS,
    },
    # Agua precipitable: el vapor integrado en la columna (`tcwv`), tal cual
    # lo publica el IFS, en kg/m² —lo mismo que mm de agua—. Un mensaje de
    # ~700 kB por plazo, desde la +0. Sin isolíneas, como el de AROME.
    "ecmwf-precipitable-water": {
        "label": "Agua precipitable", "unit": "kg/m²", "vmin": 0.0, "vmax": 70.0,
        "value": {"param": "tcwv", "levtype": "sfc"},
    },
    # Índice de potencial de génesis de Emanuel y Nolan (2004), con la
    # intensidad potencial de Bister y Emanuel (2002) y las isobaras. Solo
    # sobre el mar: la intensidad potencial necesita la temperatura del agua.
    "ecmwf-gpi": {
        "label": "Índice de potencial de génesis (GPI)", "unit": "", "vmin": 0.0, "vmax": GPI_VMAX,
        "overlay_unit": "hPa", "kind": "gpi",
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
        # Las isobaras siguen sobre tierra, donde el índice no existe.
        "overlay_own_mask": True,
    },
    # Vorticidad absoluta de 850 hPa, la misma que entra en el GPI: `vo`
    # nativa más f, suavizada igual. Multiplicada por el signo de f para que
    # lo ciclónico sea positivo en los dos hemisferios. Con las isobaras.
    "ecmwf-absolute-vorticity-850": {
        "label": "Vorticidad absoluta a 850 hPa", "unit": "10⁻⁵ s⁻¹", "vmin": -5.0, "vmax": 40.0,
        "overlay_unit": "hPa", "kind": "absolute_vorticity", "pressure": 850,
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
        "overlay_own_mask": True,
    },
    # Cizalladura profunda 850-200 hPa: el módulo de la diferencia de viento,
    # el mismo V_shear que entra en el GPI, con el vector para las flechas.
    # Se oculta donde 850 hPa queda bajo el suelo.
    "ecmwf-shear-850-200": {
        "label": "Cizalladura 850-200 hPa", "unit": "m/s", "vmin": 0.0, "vmax": 40.0,
        "kind": "shear_layer", "levels": (850, 200),
    },
    # Intensidad potencial máxima (MPI) de Bister y Emanuel (2002): el viento
    # a 10 m que podría alcanzar un ciclón tropical maduro con ese mar y ese
    # perfil. Es la V_pot del GPI, calculada una sola vez para los dos.
    "ecmwf-mpi": {
        "label": "Intensidad potencial máxima (MPI)", "unit": "m/s", "vmin": 0.0, "vmax": 90.0,
        "overlay_unit": "hPa", "kind": "mpi",
        "overlay": {"param": "msl", "levtype": "sfc", "scale": 0.01},
        "overlay_own_mask": True,
    },
    # Jet stream: velocidad del viento en 300 hPa y sus componentes para las
    # flechas. Sin isohipsas: con el viento flojo sin pintar, la forma del jet
    # ya dibuja la onda.
    "ecmwf-jet-300": {
        "label": "Jet stream en 300 hPa", "unit": "m/s", "vmin": 0.0, "vmax": 75.0,
        "kind": "wind", "pressure": 300,
    },
    # Frontogénesis cinemática en K por 100 km cada 3 h, con las isentrópicas
    # de 850 hPa en K como referencia.
    "ecmwf-frontogenesis-850": {
        "label": "Frontogénesis a 850 hPa", "unit": "K/100 km/3 h",
        "vmin": -4.0, "vmax": 4.0, "overlay_unit": "K", "kind": "frontogenesis", "pressure": 850,
    },
    # Tasa de crecimiento de Eady de la capa 850-500 hPa, en día⁻¹, con las
    # isohipsas de 500 hPa. Se oculta donde 850 hPa roza el suelo (presión en
    # superficie por debajo de 900 hPa): allí la cizalladura es la del
    # rozamiento y el índice sale falseado.
    "ecmwf-eady-850-500": {
        "label": "Tasa de crecimiento de Eady 850-500 hPa", "unit": "día⁻¹",
        "vmin": 0.0, "vmax": 2.5, "overlay_unit": "dam", "kind": "eady",
        "min_surface_hpa": 900,
    },
    # −ω: positivo es ascenso, para que en el mapa el rojo se lea como «sube».
    "ecmwf-omega-700": {
        "label": "Velocidad vertical a 700 hPa", "unit": "Pa/s",
        "vmin": -1.5, "vmax": 1.5, "overlay_unit": "dam", "kind": "omega", "pressure": 700,
        "overlay": {"param": "gh", "levtype": "pl", "levelist": "700", "scale": 0.1},
    },
    # Temperatura con isohipsas del mismo nivel, como los de AROME. El GRIB se
    # lee sin normalizar unidades, así que la temperatura llega en kelvin.
    "ecmwf-temperature-850": {
        "label": "Temperatura y geopotencial 850 hPa", "unit": "°C",
        "vmin": -24.0, "vmax": 36.0, "overlay_unit": "dam",
        "value": {"param": "t", "levtype": "pl", "levelist": "850", "offset": -273.15},
        "overlay": {"param": "gh", "levtype": "pl", "levelist": "850", "scale": 0.1},
        # Bajo el relieve el IFS extrapola: no es aire, y no se enseña.
        "mask_below_hpa": 850,
    },
    "ecmwf-temperature-500": {
        "label": "Temperatura y geopotencial 500 hPa", "unit": "°C",
        "vmin": -42.0, "vmax": -2.0, "overlay_unit": "dam",
        "value": {"param": "t", "levtype": "pl", "levelist": "500", "offset": -273.15},
        "overlay": {"param": "gh", "levtype": "pl", "levelist": "500", "scale": 0.1},
    },
    "relative-vorticity-500": {
        "label": "Vorticidad relativa a 500 hPa", "unit": "10⁻⁵ s⁻¹",
        "vmin": -30.0, "vmax": 30.0, "level": 500,
        "overlay_unit": "dam",
    },
    "q-vectors-700": {
        # −2∇·Q, el término de la ecuación ω: positivo es forzamiento de
        # ascenso, para que en el mapa el rojo se lea como «sube».
        "label": "Vectores Q y forzamiento vertical a 700 hPa", "unit": "10⁻¹⁷ m kg⁻¹ s⁻¹",
        "vmin": -4.0, "vmax": 4.0, "level": 700,
        "overlay_unit": "dam",
    },
}


class EcmwfError(RuntimeError):
    """La pasada no está publicada o el mensaje pedido no aparece."""


def domain_projection(domain: str = DEFAULT_DOMAIN_ID) -> dict[str, Any] | None:
    """LCC con la que el visor pinta el dominio; ninguna si Europa se ha cambiado a mano."""
    if domain == DEFAULT_DOMAIN_ID and os.getenv("METEOLABX_ECMWF_DOMAIN", "").strip():
        return None
    return dict(DOMAINS[domain]["projection"])


def domain_bounds(domain: str = DEFAULT_DOMAIN_ID) -> tuple[float, float, float, float]:
    """Recorte del dominio. El europeo se puede cambiar sin tocar el código."""
    if domain not in DOMAINS:
        raise EcmwfError(f"ECMWF no tiene el dominio «{domain}».")
    if domain != DEFAULT_DOMAIN_ID:
        return DOMAINS[domain]["bounds"]
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


_http_local = threading.local()


def _http() -> requests.Session:
    """Una sesión por hilo, que reutiliza la conexión con data.ecmwf.int.

    Un plazo son ~19 peticiones de medio mega, y abrir TCP y TLS en cada una
    costaba más que la transferencia: medido, 43 s en serie con conexiones
    nuevas y 17 s reutilizándolas. `requests.Session` no es segura entre
    hilos, así que cada uno tiene la suya.
    """
    sesion = getattr(_http_local, "session", None)
    if sesion is None:
        sesion = _http_local.session = requests.Session()
    return sesion


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
        respuesta = _http().get(url, timeout=_timeout())
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
    # El servidor de datos abiertos corta con 429 cuando se le pide mucho
    # seguido. Suele ser momentáneo: unos segundos de espera bastan, y sin
    # reintento el mapa se quedaba en error hasta el ciclo siguiente.
    esperas = RATE_LIMIT_BACKOFF_S
    for intento in range(len(esperas) + 1):
        try:
            with _http().get(
                url,
                headers={"Range": f"bytes={inicio}-{fin}"},
                timeout=_timeout(),
                stream=True,
            ) as respuesta:
                if respuesta.status_code in (429, 503) and intento < len(esperas):
                    time.sleep(esperas[intento])
                    continue
                if respuesta.status_code not in (200, 206):
                    _discard_message(destino)
                    raise EcmwfError(
                        f"El servidor no sirvió el rango pedido de +{step} h "
                        f"(HTTP {respuesta.status_code})."
                    )
                with destino.open("wb") as fichero:
                    for trozo in respuesta.iter_content(1024 * 256):
                        fichero.write(trozo)
                return destino
        except requests.RequestException as exc:
            _discard_message(destino)
            raise EcmwfError(f"No se pudo descargar {mensaje.get('param')}: {exc}") from exc
    raise EcmwfError(f"El servidor no sirvió el rango pedido de +{step} h.")


def _read_message_window(
    ruta: Path, bounds: tuple[float, float, float, float] | None
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Lee el recorte del dominio, no la rejilla global entera.

    GDAL ya entrega el 0,25° con las longitudes en −180…180 y el norte arriba,
    así que la ventana se saca directamente de los límites pedidos.
    """
    with rasterio.Env(GDAL_CACHEMAX=64, GRIB_NORMALIZE_UNITS="NO"), rasterio.open(ruta) as dataset:
        ventana = (rasterio.windows.Window(0, 0, dataset.width, dataset.height)
                   if bounds is None else
                   from_bounds(*bounds, transform=dataset.transform).round_offsets().round_lengths())
        # Un dominio que se salga de la rejilla se recorta a lo que existe.
        ventana = ventana.intersection(
            rasterio.windows.Window(0, 0, dataset.width, dataset.height)
        )
        valores = dataset.read(1, window=ventana, masked=True)
        reales = dataset.window_bounds(ventana)
    datos = np.asarray(valores.filled(np.nan), dtype="float64")
    return datos, tuple(float(valor) for valor in reales)


# Una sesión por plazo: aislada de peticiones HTTP y de otros ciclos.
_shared_fields: ContextVar = ContextVar("ecmwf_shared_fields", default=None)


class shared_downloads:
    """Descarga y decodifica cada campo global una vez por plazo.

    Las tareas comparten la sesión explícitamente mediante copy_context.
    Los bloqueos por campo evitan descargas duplicadas sin serializar campos
    distintos. La memoria se libera al terminar el plazo (no toda la pasada).
    """

    def __enter__(self):
        self.fields = {}
        self.derived = {}
        self.locks = {}
        self.guard = threading.Lock()
        self.token = _shared_fields.set(self)
        return self

    def __exit__(self, *exc):
        _shared_fields.reset(self.token)
        self.fields.clear()
        self.derived.clear()
        return False

    def derive(self, key, calcular):
        """Resultado derivado de los campos del plazo, calculado una sola vez.

        La intensidad potencial la piden el GPI y la MPI de cada dominio; sin
        esto se iteraba dos veces la misma columna.
        """
        with self.guard:
            lock = self.locks.setdefault(("derived", key), threading.Lock())
        with lock:
            if key not in self.derived:
                self.derived[key] = calcular()
            return self.derived[key]

    def load(self, key, run, step, selector):
        """Campo global de la caché; lo baja si nadie lo ha hecho todavía."""
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self.fields:
                message = _select_message(read_index(run, step), selector)
                path = _download_message(run, step, message)
                try:
                    self.fields[key] = _read_message_window(path, None)
                finally:
                    _discard_message(path)
            return self.fields[key]

    def prefetch(self, pool, run, campos):
        """Baja a la vez todos los mensajes que el plazo va a pedir.

        Sin esto, cada mapa descargaba sus campos uno detrás de otro y los
        demás hilos esperaban en el bloqueo del mismo campo: en producción,
        un plazo eran ~60 s de descargas en fila y 2 s de cálculo. En paralelo
        son ~12 s. Un fallo aquí no se anota: el mapa que necesite ese campo
        lo vuelve a pedir y es él quien registra el error.
        """
        futuros = [
            pool.submit(self.load, _field_key(run, paso, selector), run, paso, selector)
            for paso, selector in campos
        ]
        for futuro in futuros:
            try:
                futuro.result()
            except Exception as exc:  # noqa: BLE001 — el mapa lo reintenta y lo anota.
                logger.debug("ECMWF: precarga fallida, el mapa lo reintentará: %s", exc)

    def read(self, key, run, step, selector, bounds):
        values, extent = self.load(key, run, step, selector)
        height, width = values.shape
        transform = rasterio.transform.from_bounds(*extent, width, height)
        window = from_bounds(*bounds, transform=transform).round_offsets().round_lengths()
        window = window.intersection(rasterio.windows.Window(0, 0, width, height))
        rows, cols = window.toslices()
        return values[rows, cols], tuple(rasterio.windows.bounds(window, transform))


def calculation_workers() -> int:
    """Concurrencia independiente de AROME y acotada para no saturar ECMWF."""
    try:
        return max(1, min(8, int(os.getenv("METEOLABX_ECMWF_WORKERS", "4"))))
    except ValueError:
        return 4


def _memory_gb(name: str, default: float) -> int:
    try:
        value = float(os.getenv(name, str(default)))
        if not np.isfinite(value) or value <= 0:
            return int(default * 1024**3)
        return max(1, int(value * 1024**3))
    except ValueError:
        return int(default * 1024**3)


@lru_cache(maxsize=1)
def _warn_unmeasured_memory():
    logger.warning("ECMWF: sin memoria/límite legible; se limita a un worker. "
                   "En contenedores sin límite, configura METEOLABX_FORECAST_MEMORY_LIMIT_GB.")


def memory_worker_capacity(requested: int, cached_bytes: int = 0) -> int:
    """Admite un lote completo antes de que sus arrays aparezcan en el cgroup.

    Los lotes anteriores ya han terminado. Su caché sí aparece en la medida:
    solo se reserva la parte del presupuesto de caché que aún puede crecer.
    Las reservas son estimaciones conservadoras configurables, no picos medidos.
    """
    from server.services.forecast_memory import cgroup_memory

    measured = cgroup_memory(_memory_gb("METEOLABX_FORECAST_MEMORY_LIMIT_GB", 0))
    if measured is None:
        _warn_unmeasured_memory()
        return min(requested, 1)
    used, limit = measured
    per_worker = _memory_gb("METEOLABX_ECMWF_WORKER_MEMORY_GB", 0.5)
    headroom = _memory_gb("METEOLABX_ECMWF_MEMORY_RESERVE_GB", 0.5)
    cache_growth = max(0, _memory_gb("METEOLABX_ECMWF_CACHE_MEMORY_GB", 0.25) - cached_bytes)
    return max(0, min(requested, (limit - used - headroom - cache_growth) // per_worker))


def _discard_message(ruta: Path) -> None:
    ruta.unlink(missing_ok=True)
    try:
        ruta.parent.rmdir()
    except OSError:
        pass


def _field_key(run: datetime, step: int, selector: dict[str, Any]) -> tuple:
    return (
        run.astimezone(timezone.utc).isoformat(), int(step),
        str(selector.get("param")), str(selector.get("levtype")), str(selector.get("levelist", "")),
    )


def _field(
    run: datetime, step: int, selector: dict[str, Any], bounds
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    clave = _field_key(run, step, selector)
    session = _shared_fields.get()
    if session is not None:
        datos, reales = session.read(clave, run, step, selector, bounds)
    else:
        mensaje = _select_message(read_index(run, step), selector)
        ruta = _download_message(run, step, mensaje)
        try:
            datos, reales = _read_message_window(ruta, bounds)
        finally:
            _discard_message(ruta)
    return datos * float(selector.get("scale", 1.0)) + float(selector.get("offset", 0.0)), reales


def _omega_field(run, step, bounds, level):
    """−ω en Pa/s, positivo hacia arriba, con un suavizado ligero.

    La ω del IFS en niveles medios lleva mucha señal de ondas de montaña y de
    convección, de una o dos celdas. Un filtro de 1,5 celdas (unos 40 km, igual
    en km en las dos direcciones) la quita y deja el ascenso organizado.
    """
    halo = (max(-180, bounds[0] - 3), max(-90, bounds[1] - 3),
            min(180, bounds[2] + 3), min(90, bounds[3] + 3))
    omega, reales = _field(run, step, {"param": "w", "levtype": "pl", "levelist": str(level)}, halo)
    presion, reales_p = _field(run, step, {"param": "sp", "levtype": "sfc"}, halo)
    if omega.shape != presion.shape or not np.allclose(reales, reales_p):
        raise EcmwfError("La velocidad vertical no comparte rejilla con la presión.")
    sobre_suelo = np.isfinite(presion) & (presion >= level * 100)
    valores = -smooth(np.where(sobre_suelo, omega, np.nan), OMEGA_SIGMA, reales)
    h, w = valores.shape
    rw, rs, re, rn = reales
    dx, dy = (re - rw) / w, (rn - rs) / h
    x0, x1 = max(0, round((bounds[0] - rw) / dx)), min(w, round((bounds[2] - rw) / dx))
    y0, y1 = max(0, round((rn - bounds[3]) / dy)), min(h, round((rn - bounds[1]) / dy))
    return valores[y0:y1, x0:x1], (rw + x0 * dx, rn - y1 * dy, rw + x1 * dx, rn - y0 * dy)


def _frontogenesis_field(run, step, bounds, level):
    """Frontogénesis en K/100 km/3 h y θ en K para las isentrópicas.

    El halo cubre el filtro: 4σ son 2,5° en latitud y, en longitud, se
    ensancha hacia el norte con 1/cos φ.
    """
    halo = (max(-180, bounds[0] - 10), max(-90, bounds[1] - 4),
            min(180, bounds[2] + 10), min(90, bounds[3] + 4))
    def read(param, surface=False):
        selector = {"param": param, "levtype": "sfc" if surface else "pl"}
        if not surface:
            selector["levelist"] = str(level)
        return _field(run, step, selector, halo)

    temperatura, reales = read("t")
    u, reales_u = read("u")
    v, reales_v = read("v")
    presion, reales_p = read("sp", True)
    if (not (temperatura.shape == u.shape == v.shape == presion.shape)
            or not all(np.allclose(reales, otro) for otro in (reales_u, reales_v, reales_p))):
        raise EcmwfError("Los campos de la frontogénesis no comparten rejilla.")
    sobre_suelo = np.isfinite(presion) & (presion >= level * 100)
    theta = np.where(sobre_suelo, temperatura * (1000.0 / level) ** 0.2857, np.nan)
    u = np.where(sobre_suelo, u, np.nan)
    v = np.where(sobre_suelo, v, np.nan)
    # De K m⁻¹ s⁻¹ a K por 100 km cada 3 horas.
    valores = frontogenesis(theta, u, v, reales) * 1e5 * 10800.0
    h, w = valores.shape
    rw, rs, re, rn = reales
    dx, dy = (re - rw) / w, (rn - rs) / h
    x0, x1 = max(0, round((bounds[0] - rw) / dx)), min(w, round((bounds[2] - rw) / dx))
    y0, y1 = max(0, round((rn - bounds[3]) / dy)), min(h, round((rn - bounds[1]) / dy))
    recorte = np.s_[y0:y1, x0:x1]
    return valores[recorte], theta[recorte], (rw + x0 * dx, rn - y1 * dy, rw + x1 * dx, rn - y0 * dy)


def _eady_field(run, step, bounds, min_surface_hpa):
    """Eady 850-500 hPa en día⁻¹ y el geopotencial de 500 hPa en dam."""
    halo = (max(-180, bounds[0] - 3), max(-90, bounds[1] - 3),
            min(180, bounds[2] + 3), min(90, bounds[3] + 3))
    campos = {}
    reales = None
    for param in ("u", "v", "gh", "t"):
        for nivel in (850, 500):
            campos[f"{param}{nivel}"], rejilla = _field(
                run, step, {"param": param, "levtype": "pl", "levelist": str(nivel)}, halo
            )
            if reales is None:
                reales = rejilla
            elif not np.allclose(reales, rejilla):
                raise EcmwfError("Los campos de Eady no comparten rejilla.")
    presion, rejilla = _field(run, step, {"param": "sp", "levtype": "sfc"}, halo)
    if not np.allclose(reales, rejilla):
        raise EcmwfError("Los campos de Eady no comparten rejilla.")
    valores = eady_growth_rate(
        campos["u850"], campos["v850"], campos["u500"], campos["v500"],
        campos["gh850"], campos["gh500"], campos["t850"], campos["t500"],
        reales, sigma=EADY_SIGMA,
    ) * 86400.0
    valores = np.where(np.isfinite(presion) & (presion >= min_surface_hpa * 100), valores, np.nan)
    overlay = campos["gh500"] * .1
    h, w = valores.shape
    rw, rs, re, rn = reales
    dx, dy = (re - rw) / w, (rn - rs) / h
    x0, x1 = max(0, round((bounds[0] - rw) / dx)), min(w, round((bounds[2] - rw) / dx))
    y0, y1 = max(0, round((rn - bounds[3]) / dy)), min(h, round((rn - bounds[1]) / dy))
    recorte = np.s_[y0:y1, x0:x1]
    return valores[recorte], overlay[recorte], (rw + x0 * dx, rn - y1 * dy, rw + x1 * dx, rn - y0 * dy)


def _theta_e_field(run, step, bounds, level):
    """θe en °C con la formulación de Bolton (1980) de MetPy, como en AROME.

    El rocío sale de la humedad específica y no de la relativa: la del IFS se
    mide respecto al hielo por debajo de 0 °C, y el rocío derivado de ella
    saldría sesgado justo en el aire frío que este mapa tiene que distinguir.
    """
    from server.services.convective_diagnostics import (
        equivalent_potential_temperature_metpy_k,
    )

    def read(param, surface=False):
        selector = {"param": param, "levtype": "sfc" if surface else "pl"}
        if not surface:
            selector["levelist"] = str(level)
        return _field(run, step, selector, bounds)

    temperatura, reales = read("t")
    humedad, reales_q = read("q")
    presion, reales_p = read("sp", True)
    if (temperatura.shape != humedad.shape or temperatura.shape != presion.shape
            or not np.allclose(reales, reales_q) or not np.allclose(reales, reales_p)):
        raise EcmwfError("Los campos de θe no comparten rejilla.")
    # Presión de vapor a partir de la humedad específica, y rocío por Magnus.
    vapor = np.maximum(humedad * level / (0.622 + 0.378 * humedad), 1e-3)
    logaritmo = np.log(vapor / 6.112)
    rocio = 243.5 * logaritmo / (17.67 - logaritmo) + 273.15
    theta_e = equivalent_potential_temperature_metpy_k(level, temperatura, rocio)
    # Bajo el relieve el nivel no es aire: el IFS extrapola y dibujaría la
    # orografía como si fuera una masa.
    theta_e = np.where(np.isfinite(presion) & (presion >= level * 100), theta_e, np.nan)
    return theta_e - 273.15, reales


def _gpi_halo(bounds):
    """Halo para el suavizado de la vorticidad del GPI: 4σ son 2° en latitud
    y, en longitud, se ensancha hacia el norte con 1/cos φ. La MPI usa el
    mismo para compartir el cálculo."""
    return (max(-180, bounds[0] - 6), max(-90, bounds[1] - 3),
            min(180, bounds[2] + 6), min(90, bounds[3] + 3))


def _crop(reales, bounds, *arrays):
    h, w = arrays[0].shape
    rw, rs, re, rn = reales
    dx, dy = (re - rw) / w, (rn - rs) / h
    x0, x1 = max(0, round((bounds[0] - rw) / dx)), min(w, round((bounds[2] - rw) / dx))
    y0, y1 = max(0, round((rn - bounds[3]) / dy)), min(h, round((rn - bounds[1]) / dy))
    recorte = np.s_[y0:y1, x0:x1]
    return [a[recorte] for a in arrays] + [(rw + x0 * dx, rn - y1 * dy, rw + x1 * dx, rn - y0 * dy)]


def _read_on_grid(run, step, halo, que):
    """Lee campos sobre el halo y comprueba que comparten rejilla."""
    reales = None
    def read(selector):
        nonlocal reales
        valores, rejilla = _field(run, step, selector, halo)
        if reales is None:
            reales = rejilla
        elif not np.allclose(reales, rejilla):
            raise EcmwfError(f"Los campos de {que} no comparten rejilla.")
        return valores
    return read, lambda: reales


def _pl(param, level):
    return {"param": param, "levtype": "pl", "levelist": str(level)}


def _sfc(param):
    return {"param": param, "levtype": "sfc"}


def _potential_intensity(run, step, halo):
    """V_pot (m/s) de Bister y Emanuel (2002) y presión al nivel del mar (hPa).

    Sale del núcleo en C++ (traducción de tcpyPI). Como temperatura del mar
    se usa la de piel (`skt`), porque el open data publica esa y no `sst`. No
    son el mismo campo: la de piel es la de la interfaz radiativa aire-mar, y
    el IFS le aplica la capa fría de piel y la capa cálida diurna, así que
    puede apartarse de la SST de masa, sobre todo de día con viento flojo. Es
    una aproximación, no la SST que pide la teoría.

    El open data llega a 10 hPa, pero el perfil se corta en 50 hPa: es el
    techo por defecto de tcpyPI y por encima no cambia el resultado.
    """
    def calcular():
        from server.services._dcape_native import potential_intensity

        read, reales = _read_on_grid(run, step, halo, "la intensidad potencial")
        piel, tierra = read(_sfc("skt")), read(_sfc("lsm"))
        msl, presion = read(_sfc("msl")) * .01, read(_sfc("sp")) * .01
        temperatura = np.stack([read(_pl("t", nivel)) for nivel in GPI_LEVELS]) - 273.15
        humedad = np.stack([read(_pl("q", nivel)) for nivel in GPI_LEVELS])
        # La `skt` de una celda de costa mezcla la del mar con la de la tierra:
        # con media celda de tierra, a mediodía junto a un desierto, salían
        # mares a 43 °C y vientos de 138 m/s. Por debajo de un 10 % de tierra
        # la mezcla ya no se nota.
        sst = np.where(np.isfinite(tierra) & (tierra < SEA_MAX_LAND_FRACTION), piel - 273.15, np.nan)
        mezcla = humedad / np.maximum(1.0 - humedad, 1e-6) * 1000.0
        vpot, _ = potential_intensity(sst, msl, presion, np.asarray(GPI_LEVELS, dtype=float),
                                      temperatura, mezcla)
        return vpot, msl, reales()

    session = _shared_fields.get()
    if session is None:
        return calcular()
    return session.derive(
        ("potential_intensity", run.astimezone(timezone.utc).isoformat(), int(step), tuple(halo)), calcular)


def _mpi_fields(run, step, bounds):
    """MPI en m/s, con la presión al nivel del mar para las isobaras."""
    vpot, msl, reales = _potential_intensity(run, step, _gpi_halo(bounds))
    return _crop(reales, bounds, vpot, msl)


def _absolute_vorticity(vorticidad, reales):
    """η = ζ + f en s⁻¹, con ζ suavizada como en el GPI."""
    _, latitudes = coordinates(vorticidad.shape, reales)
    coriolis = 2 * OMEGA_EARTH * np.sin(latitudes)[:, None]
    return smooth(vorticidad, GPI_VORTICITY_SIGMA, reales) + coriolis, np.sign(latitudes)[:, None]


def _absolute_vorticity_fields(run, step, bounds, level):
    """η·signo(f) en 10⁻⁵ s⁻¹ y la presión al nivel del mar, sin lo que queda bajo el suelo."""
    halo = _gpi_halo(bounds)
    read, reales = _read_on_grid(run, step, halo, "la vorticidad absoluta")
    vorticidad = read(_pl("vo", level))
    presion = read(_sfc("sp"))
    msl = read(_sfc("msl")) * .01
    sobre_suelo = np.isfinite(presion) & (presion >= level * 100)
    eta, signo = _absolute_vorticity(np.where(sobre_suelo, vorticidad, np.nan), reales())
    return _crop(reales(), bounds, np.where(sobre_suelo, eta * signo * 1e5, np.nan), msl)


def _gpi_fields(run, step, bounds):
    """GPI de Emanuel y Nolan (2004) y sus cuatro ingredientes.

    GPI = |10⁵ η|^{3/2} · (H/50)³ · (V_pot/70)³ · (1 + 0,1 V_shear)⁻²

    η es la vorticidad absoluta de 850 hPa, H la humedad relativa de 600 hPa
    en %, V_pot la intensidad potencial en m/s (ver `_potential_intensity`)
    y V_shear el módulo de la cizalladura 850-200 hPa en m/s.
    """
    halo = _gpi_halo(bounds)
    vpot, msl, reales_pi = _potential_intensity(run, step, halo)
    read, reales = _read_on_grid(run, step, halo, "el GPI")
    t600, q600 = read(_pl("t", 600)) - 273.15, read(_pl("q", 600))
    vorticidad = read(_pl("vo", 850))
    u850, v850 = read(_pl("u", 850)), read(_pl("v", 850))
    u200, v200 = read(_pl("u", 200)), read(_pl("v", 200))
    if not np.allclose(reales(), reales_pi):
        raise EcmwfError("Los campos del GPI no comparten rejilla.")

    # Humedad relativa respecto al agua, desde q: la `r` del IFS se mide
    # respecto al hielo por debajo de 0 °C, y a 600 hPa casi siempre lo está.
    vapor = q600 * 600.0 / (0.622 + 0.378 * q600)
    saturacion = 6.112 * np.exp(17.67 * t600 / (243.5 + t600))
    hr600 = np.clip(100.0 * vapor / saturacion, 0.0, 100.0)

    eta = np.abs(_absolute_vorticity(vorticidad, reales())[0])
    cizalladura = np.hypot(u200 - u850, v200 - v850)
    gpi = (np.abs(1e5 * eta) ** 1.5 * (hr600 / 50.0) ** 3 * (vpot / 70.0) ** 3
           * (1.0 + 0.1 * cizalladura) ** -2)
    return _crop(reales(), bounds, gpi, msl)


def _diagnostic_fields(product_id, run, step, bounds):
    # Halo fuera del encuadre para que las derivadas no nazcan en el borde.
    # La vorticidad solo se suaviza una celda: 3° bastan. El filtro de Q
    # abarca 4σ, 6° en latitud, y en longitud se ensancha hacia el norte hasta
    # 24 celdas, 24° a 4σ.
    west, south, east, north = bounds
    level = PRODUCTS[product_id]["level"]
    margin_x, margin_y = (3, 3) if level == 500 else (24, 6)
    halo = (max(-180, west - margin_x), max(-90, south - margin_y),
            min(180, east + margin_x), min(90, north + margin_y))
    def read(param, surface=False):
        selector = {"param": param, "levtype": "sfc" if surface else "pl"}
        if not surface:
            selector["levelist"] = str(level)
        return _field(run, step, selector, halo)

    # En 500 hPa la vorticidad es la nativa del IFS (`vo`), calculada en el
    # espacio espectral del modelo: más exacta que derivarla aquí de u y v.
    first_param, second_param = ("vo", "gh") if level == 500 else ("t", "gh")
    first, real = read(first_param)
    second, second_bounds = read(second_param)
    pressure, pressure_bounds = read("sp", True)
    if (first.shape != second.shape or first.shape != pressure.shape
            or not np.allclose(real, second_bounds) or not np.allclose(real, pressure_bounds)):
        raise EcmwfError("Los campos del diagnóstico no comparten rejilla.")
    above_ground = np.isfinite(pressure) & (pressure >= level * 100)
    first = np.where(above_ground, first, np.nan)
    second = np.where(above_ground, second, np.nan)
    overlay = None
    if level == 500:
        # Suavizado ligero, ~28 km e igual en km en las dos direcciones: quita
        # los filamentos de la escala de rejilla y el rizado espectral sin
        # debilitar los máximos de las vaguadas, que con 2 celdas se perdían.
        values, u, v = smooth(first, VORTICITY_SIGMA, real) * 1e5, None, None
        # En el hemisferio sur la vorticidad ciclónica es negativa. Se muestra
        # multiplicada por el signo de f para que el rojo sea siempre
        # ciclónico, en cualquier dominio; en el norte no cambia nada.
        _, latitudes = coordinates(values.shape, real)
        values = values * np.sign(latitudes)[:, None]
        overlay = second * .1
    else:
        # GDAL entrega las temperaturas GRIB en °C por defecto. _field pide
        # Kelvin explícitamente en _read_message_window para este cálculo.
        u, v, div = q_vectors(first, second, real)
        values = -2.0 * div * 1e17
        # Isohipsas de 700 hPa, sin filtrar: son la referencia sinóptica.
        overlay = second * .1
    h, w = first.shape
    rw, rs, re, rn = real
    dx, dy = (re - rw) / w, (rn - rs) / h
    x0, x1 = max(0, round((west-rw)/dx)), min(w, round((east-rw)/dx))
    y0, y1 = max(0, round((rn-north)/dy)), min(h, round((rn-south)/dy))
    # No publicar las derivadas unilaterales del borde si el halo fue truncado.
    for array in (values, u, v, overlay):
        if array is not None:
            array[:3] = array[-3:] = np.nan
            array[:, :3] = array[:, -3:] = np.nan
    crop = np.s_[y0:y1, x0:x1]
    return (values[crop], None if u is None else u[crop], None if v is None else v[crop],
            (rw+x0*dx, rn-y1*dy, rw+x1*dx, rn-y0*dy),
            None if overlay is None else overlay[crop])


def required_fields(product_id: str, step: int) -> list[tuple[int, dict[str, Any]]]:
    """Mensajes (plazo, selector) que `frame_payload` leerá para ese mapa.

    Solo sirve para precargarlos en paralelo: si esta lista se queda corta,
    el mapa baja lo que falte por su cuenta y sale igual, solo que más lento.
    Un test comprueba que coincide con lo que cada mapa pide de verdad.
    """
    config = PRODUCTS[product_id]
    kind = config.get("kind")

    def pl(param, level):
        return {"param": param, "levtype": "pl", "levelist": str(level)}

    presion = {"param": "sp", "levtype": "sfc"}
    if kind == "precip_accum":
        horas = int(config["hours"])
        if step < horas:
            return []
        tp = {"param": "tp", "levtype": "sfc"}
        return [(step, tp), (step - horas, tp), (step, config["overlay"])]
    if kind == "precip_total":
        return [(step, {"param": "tp", "levtype": "sfc"})] if step >= int(config["min_step"]) else []
    if kind == "wind":
        return [(step, pl(param, config["pressure"])) for param in ("u", "v")]
    if kind == "frontogenesis":
        return [(step, pl(param, config["pressure"])) for param in ("t", "u", "v")] + [(step, presion)]
    if kind == "eady":
        return [(step, pl(param, nivel)) for param in ("u", "v", "gh", "t") for nivel in (850, 500)] + [
            (step, presion)]
    if kind == "omega":
        return [(step, pl("w", config["pressure"])), (step, presion), (step, config["overlay"])]
    if kind == "absolute_vorticity":
        return [(step, pl("vo", config["pressure"])), (step, presion), (step, config["overlay"])]
    if kind == "shear_layer":
        return [(step, pl(param, nivel)) for nivel in config["levels"] for param in ("u", "v")] + [
            (step, presion)]
    if kind in ("gpi", "mpi"):
        campos = ([(step, {"param": param, "levtype": "sfc"}) for param in ("skt", "lsm", "msl", "sp")]
                  + [(step, pl(param, nivel)) for param in ("t", "q") for nivel in GPI_LEVELS])
        if kind == "gpi":
            campos += [(step, pl("vo", 850))] + [
                (step, pl(param, nivel)) for nivel in (850, 200) for param in ("u", "v")]
        return campos
    if kind == "theta_e":
        return [(step, pl(param, config["level"])) for param in ("t", "q")] + [
            (step, presion), (step, config["overlay"])]
    if "level" in config:
        nivel = config["level"]
        return [(step, pl("vo" if nivel == 500 else "t", nivel)), (step, pl("gh", nivel)), (step, presion)]
    campos = [(step, config["value"])]
    if config.get("overlay"):
        campos.append((step, config["overlay"]))
    if config.get("mask_below_hpa"):
        campos.append((step, presion))
    return campos


def frame_payload(
    product_id: str, run: datetime, step: int, domain: str = DEFAULT_DOMAIN_ID
) -> tuple[bytes, dict[str, str]]:
    """Rejilla lista para el visor, en el mismo formato binario que AROME."""
    config = PRODUCTS.get(product_id)
    if config is None:
        raise EcmwfError(f"ECMWF no publica el mapa «{product_id}».")
    bounds = domain_bounds(domain)
    empezado = time.monotonic()
    vector_u = vector_v = overlay = None
    if config.get("kind") == "precip_accum":
        horas = int(config["hours"])
        if step < horas:
            raise EcmwfError(f"La precipitación en {horas} h empieza en la +{horas}.")
        final, reales = _field(run, step, {"param": "tp", "levtype": "sfc"}, bounds)
        inicio, _ = _field(run, step - horas, {"param": "tp", "levtype": "sfc"}, bounds)
        # De metros de agua a milímetros; el redondeo del GRIB puede dejar
        # diferencias negativas de centésimas.
        valores = np.maximum((final - inicio) * 1000.0, 0.0)
        overlay, _ = _field(run, step, config["overlay"], bounds)
    elif config.get("kind") == "precip_total":
        if step < int(config["min_step"]):
            raise EcmwfError("La precipitación acumulada empieza en el primer plazo.")
        total, reales = _field(run, step, {"param": "tp", "levtype": "sfc"}, bounds)
        # De metros de agua a milímetros.
        valores = np.maximum(total * 1000.0, 0.0)
    elif config.get("kind") == "wind":
        nivel = str(config["pressure"])
        vector_u, reales = _field(run, step, {"param": "u", "levtype": "pl", "levelist": nivel}, bounds)
        vector_v, _ = _field(run, step, {"param": "v", "levtype": "pl", "levelist": nivel}, bounds)
        valores = np.hypot(vector_u, vector_v)
    elif config.get("kind") == "frontogenesis":
        valores, overlay, reales = _frontogenesis_field(run, step, bounds, int(config["pressure"]))
    elif config.get("kind") == "eady":
        valores, overlay, reales = _eady_field(run, step, bounds, float(config["min_surface_hpa"]))
    elif config.get("kind") == "omega":
        valores, reales = _omega_field(run, step, bounds, int(config["pressure"]))
        overlay, _ = _field(run, step, config["overlay"], bounds)
        overlay = np.where(np.isfinite(valores), overlay, np.nan)
    elif config.get("kind") == "shear_layer":
        bajo, alto = (str(nivel) for nivel in config["levels"])
        u_bajo, reales = _field(run, step, {"param": "u", "levtype": "pl", "levelist": bajo}, bounds)
        v_bajo, _ = _field(run, step, {"param": "v", "levtype": "pl", "levelist": bajo}, bounds)
        u_alto, _ = _field(run, step, {"param": "u", "levtype": "pl", "levelist": alto}, bounds)
        v_alto, _ = _field(run, step, {"param": "v", "levtype": "pl", "levelist": alto}, bounds)
        presion, _ = _field(run, step, {"param": "sp", "levtype": "sfc"}, bounds)
        sobre_suelo = np.isfinite(presion) & (presion >= float(bajo) * 100)
        vector_u = np.where(sobre_suelo, u_alto - u_bajo, np.nan)
        vector_v = np.where(sobre_suelo, v_alto - v_bajo, np.nan)
        valores = np.hypot(vector_u, vector_v)
    elif config.get("kind") == "gpi":
        valores, overlay, reales = _gpi_fields(run, step, bounds)
    elif config.get("kind") == "absolute_vorticity":
        valores, overlay, reales = _absolute_vorticity_fields(run, step, bounds, int(config["pressure"]))
    elif config.get("kind") == "mpi":
        valores, overlay, reales = _mpi_fields(run, step, bounds)
    elif config.get("kind") == "theta_e":
        valores, reales = _theta_e_field(run, step, bounds, config["level"])
        overlay, _ = _field(run, step, config["overlay"], bounds)
    elif "level" in config:
        valores, vector_u, vector_v, reales, overlay = _diagnostic_fields(product_id, run, step, bounds)
    else:
        valores, reales = _field(run, step, config["value"], bounds)
        if config.get("overlay"):
            overlay, _ = _field(run, step, config["overlay"], bounds)
        if config.get("mask_below_hpa"):
            presion, _ = _field(run, step, {"param": "sp", "levtype": "sfc"}, bounds)
            bajo_tierra = ~(np.isfinite(presion) & (presion >= config["mask_below_hpa"] * 100))
            valores = np.where(bajo_tierra, np.nan, valores)
            if overlay is not None:
                overlay = np.where(bajo_tierra, np.nan, overlay)
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
        overlay_unit=config.get("overlay_unit"),
        overlay_own_mask=bool(config.get("overlay_own_mask")),
        vector_u=vector_u, vector_v=vector_v,
        metadata={
            "run": run_iso,
            "valid_time": valid_iso,
            "forecast_model": FORECAST_MODEL,
            "calculation_scope": "model",
            # El visor pide las fronteras por dominio; las de ECMWF no son las
            # de AROME, así que el ámbito las distingue en su caché.
            "boundary_scope": f"{FORECAST_MODEL}:{domain}",
            "domain": domain,
            # Sin ella el visor pinta en latitud y longitud, como las pasadas
            # guardadas antes de la LCC.
            "projection": domain_projection(domain),
            "vertical_kind": None,
            "level": config.get("level"),
            "vector_unit": "m² kg⁻¹ s⁻¹" if vector_u is not None else None,
        },
    )
    logger.info(
        "ECMWF %s %s +%d h: %d × %d celdas en %.1f s",
        product_id, domain, step, valores.shape[1], valores.shape[0],
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
        respuesta = _http().head(index_url(run, step), timeout=_timeout())
    except requests.RequestException:
        return False
    return respuesta.status_code == 200


def available_steps(run: datetime, known: Iterable[int] = ()) -> tuple[int, ...]:
    """Plazos ya publicados de esa pasada, comprobados en paralelo.

    Son hasta 49 peticiones HEAD de nada, y evitan anunciar en el visor horas
    que todavía no existen —que es lo que convierte un mapa vacío en un error.
    Los plazos de ``known`` ya se vieron publicados y no desaparecen: no se
    vuelven a preguntar. Repetirlas cada minuto, con la pasada ya completa,
    era lo que acababa en 429 del servidor de datos abiertos.
    """
    pasos = candidate_steps()
    conocidos = set(known) & set(pasos)
    dudosos = [paso for paso in pasos if paso not in conocidos]
    if dudosos:
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ecmwf-head") as pool:
            presentes = list(pool.map(lambda paso: _index_exists(run, paso), dudosos))
        conocidos |= {paso for paso, existe in zip(dudosos, presentes) if existe}
    return tuple(paso for paso in pasos if paso in conocidos)


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


def manifest_product_key(product_id: str, domain: str = DEFAULT_DOMAIN_ID) -> str:
    """Clave del mapa en el manifiesto. Europa conserva la de siempre, sin sufijo."""
    return product_id if domain == DEFAULT_DOMAIN_ID else f"{product_id}@{domain}"


def frame_scope(domain: str = DEFAULT_DOMAIN_ID) -> str:
    """Ámbito del frame en el almacén: Europa en la raíz, como hasta ahora."""
    return "model" if domain == DEFAULT_DOMAIN_ID else domain


def catalog_payload(
    run: datetime | None = None, domain: str = DEFAULT_DOMAIN_ID, known_steps: Iterable[int] = ()
) -> dict[str, Any]:
    """Catálogo con la misma forma que el de AROME, para el mismo visor."""
    pasada = run or latest_run()
    pasos = available_steps(pasada, known_steps)
    run_iso = pasada.isoformat().replace("+00:00", "Z")
    oeste, sur, este, norte = domain_bounds(domain)
    return {
        "model": MODEL_LABEL,
        "resolution": RESOLUTION_LABEL,
        "domain": {
            "id": domain,
            "label": DOMAINS[domain]["label"],
            "calculation_scope": "model",
            "bounds": [oeste, sur, este, norte],
        },
        "products": {
            product_id: {
                "run": run_iso,
                "valid_times": [
                    (pasada + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
                    for paso in product_steps(product_id, pasos)
                ],
                "vmax": config["vmax"],
                "unit": config["unit"],
            }
            for product_id, config in PRODUCTS.items()
        },
        "unavailable_products": {},
    }


def product_steps(product_id: str, pasos: Iterable[int]) -> list[int]:
    """Plazos que tiene un mapa: los acumulados no existen antes de su ventana."""
    minimo = int(PRODUCTS[product_id].get("min_step", 0))
    return [paso for paso in pasos if paso >= minimo]


def expected_times(run: datetime) -> list[str]:
    """Todas las horas que la pasada llegará a tener, publicadas o no."""
    return [
        (run + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
        for paso in candidate_steps()
    ]


def domain_boundaries(domain: str = DEFAULT_DOMAIN_ID) -> list[dict[str, Any]]:
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
        domain_bounds(domain),
        scope=f"{FORECAST_MODEL}-{domain}",
        include_admin1=False,
        simplify=0.03,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def emit_run_report(store, manifest: dict[str, Any]) -> bool:
    """Guarda el informe de la pasada y manda el correo, como en AROME.

    ECMWF va fuera del worker de AROME y nunca pasaba por el suyo: sus pasadas
    terminaban sin informe ni correo. Devuelve si llegó a escribirse, para no
    repetirlo en cada ciclo.
    """
    try:
        from server.services.alerts import send as send_alert
        from server.services.run_report import alert_for_report, build_report, save_report

        report = build_report(manifest, previous=retained_manifests(store, model=FORECAST_MODEL))
        save_report(store, report)
        aviso = alert_for_report(report)
        if aviso is not None:
            send_alert(aviso, store=store)
        return True
    except Exception:
        # El informe cuenta cómo fue el trabajo; si falla, la pasada ya está
        # publicada y eso es lo que importa.
        logger.warning("ECMWF: no se pudo emitir el informe de la pasada", exc_info=True)
        return False


# Días hacia atrás que se barren al arrancar en busca de pasadas sustituidas.
ORPHAN_SWEEP_DAYS = 7
_orphans_swept = False


def delete_ecmwf_run(store, run_iso: str) -> None:
    """Borra una pasada entera: Europa, los demás dominios y su manifiesto."""
    for domain in DOMAINS:
        if domain != DEFAULT_DOMAIN_ID:
            store.delete_prefix(
                f"forecast/models/{FORECAST_MODEL}/scopes/{frame_scope(domain)}/runs/{run_slug(run_iso)}")
    delete_run(store, run_iso, model=FORECAST_MODEL)


def _retire_replaced_runs(store, run: datetime, sustituida: str | None) -> None:
    """Borra la pasada que otra ha desplazado de su turno 00/06/12/18.

    El visor solo lista las cuatro de los turnos, pero `register_run_slot`
    solo devuelve la desplazada: AROME la borra y ECMWF no lo hacía. Cada día
    quedaban cuatro pasadas huérfanas, unos 2,5 GB, sin nadie que las podara.
    La primera vez en cada proceso se barren además los últimos días, para
    recoger las que ya se habían quedado.
    """
    global _orphans_swept
    retenidas = {
        str(item.get("run"))
        for item in ((read_json(store, run_slots_key(FORECAST_MODEL)) or {}).get("slots") or {}).values()
    }
    huerfanas = {sustituida} if sustituida else set()
    if not _orphans_swept:
        huerfanas |= {
            (run - timedelta(hours=6 * salto)).isoformat().replace("+00:00", "Z")
            for salto in range(1, ORPHAN_SWEEP_DAYS * 4 + 1)
        }
    for run_iso in sorted(huerfanas - retenidas):
        try:
            delete_ecmwf_run(store, run_iso)
        except OSError:
            logger.warning("ECMWF: no se pudo borrar la pasada sustituida %s", run_iso, exc_info=True)
    _orphans_swept = True


def run_cycle(max_frames: int = 0) -> dict[str, Any]:
    """Publica los frames de ECMWF que falten de la pasada más reciente.

    Va aparte del grafo de trabajos de AROME a propósito: aquí no hay perfiles
    ni niveles, solo plazos independientes que cuestan segundos. Mezclarlos con
    los niveles convectivos habría dado a los dos modelos una cola común donde
    un fallo de ECMWF podía retrasar un diagnóstico.
    """
    store = get_forecast_store()
    # La pasada nunca retrocede de la que el visor ya tiene publicada. Cuando
    # el servidor contesta 429 a los HEAD, `latest_run` caía a una anterior:
    # el 29/09 a las 13:34 recalculó la del día 28 a las 12Z y la dejó como la
    # más reciente del visor.
    publicada = (read_json(store, latest_manifest_key(FORECAST_MODEL)) or {}).get("run")
    try:
        run = latest_run()
    except EcmwfError:
        if not publicada:
            raise
        run = parse_run(publicada)
    if publicada and run < parse_run(publicada):
        logger.info("ECMWF: la comprobación ha fallado para la pasada %s; se sigue con ella.", publicada)
        run = parse_run(publicada)
    run_iso = run.isoformat().replace("+00:00", "Z")
    manifiesto = read_json(store, run_manifest_key(run_iso, model=FORECAST_MODEL))
    conocidos = set()
    for item in ((manifiesto or {}).get("catalog_products") or {}).values():
        for valid_iso in item.get("valid_times") or ():
            try:
                conocidos.add(step_of(run, valid_iso))
            except (EcmwfError, ValueError):
                continue
    catalogo = catalog_payload(run, known_steps=conocidos)
    # Un plazo publicado no desaparece, pero cada ciclo los vuelve a comprobar
    # con 49 HEAD y alguno falla (429, timeouts). Sustituir el catálogo por esa
    # respuesta le quitaba horas a una pasada ya calculada: el visor dejaba de
    # ofrecerlas y los mapas bajaban del 100 %.
    anteriores = (manifiesto or {}).get("catalog_products") or {}
    for product_id, item in catalogo["products"].items():
        previas = (anteriores.get(product_id) or {}).get("valid_times") or ()
        item["valid_times"] = sorted(set(item["valid_times"]) | set(previas))
    # Mismas horas en todos los dominios: el catálogo de cada uno es el
    # europeo con su clave. Así el manifiesto sigue siendo uno por pasada.
    catalogo_total = {
        manifest_product_key(product_id, domain): dict(item)
        for domain in DOMAINS
        for product_id, item in catalogo["products"].items()
    }
    if not manifiesto:
        manifiesto = new_manifest(
            run_iso,
            expected_times(run),
            catalog_products=catalogo_total,
            model=FORECAST_MODEL,
        )
    manifiesto["catalog_products"] = catalogo_total
    # Para que la poda borre también los frames de los otros dominios.
    manifiesto["extra_scopes"] = [frame_scope(domain) for domain in DOMAINS if domain != DEFAULT_DOMAIN_ID]
    esperados = {product_id: len(product_steps(product_id, candidate_steps())) for product_id in PRODUCTS}
    manifiesto["expected_totals"] = {
        manifest_product_key(product_id, domain): total
        for domain in DOMAINS
        for product_id, total in esperados.items()
    }

    # Un mapa revisado se vuelve a calcular una sola vez en cada dominio; el
    # marcador se conserva al publicar parcialmente una pasada.
    for product_id, revision in ECMWF_PRODUCT_REVISIONS.items():
        for domain in DOMAINS:
            state = manifiesto.setdefault("products", {}).setdefault(
                manifest_product_key(product_id, domain), {}
            )
            if state.get("frame_revision") != revision:
                state.update(available_times=[], errors={}, frame_revision=revision)

    def disponibles(clave: str) -> set[str]:
        return set((manifiesto.get("products", {}).get(clave) or {}).get("available_times", ()))

    # Horas que cada mapa tendrá al acabar la pasada, publicadas o no. El
    # estado y el progreso se miden contra ellas: contra las que ECMWF lleva
    # publicadas, una pasada a medias —o una comprobación con HEAD fallidos—
    # quedaba «complete».
    horas_finales = {
        manifest_product_key(product_id, domain): {
            (run + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
            for paso in product_steps(product_id, candidate_steps())
        }
        for domain in DOMAINS
        for product_id in PRODUCTS
    }

    def publicados_finales() -> int:
        return sum(len(disponibles(clave) & horas) for clave, horas in horas_finales.items())

    # Plazo a plazo y, dentro de cada plazo, todos los mapas y dominios: así
    # cada mensaje GRIB se descarga una vez y sirve para todo lo que lo usa.
    pasos = sorted({
        step_of(run, valid_iso)
        for item in catalogo["products"].values()
        for valid_iso in item["valid_times"]
    })
    publicados = 0
    fallos = 0
    memory_deferred = False
    def checkpoint():
        available = publicados_finales()
        total = sum(manifiesto["expected_totals"].values())
        manifiesto["status"] = "publishing"
        manifiesto["progress"] = {
            "frames_available": available, "frames_total": total,
            "percent": round(100 * available / total, 1) if total else 0.0,
            "error_count": fallos, "current_job": None, "active_jobs": [],
            "last_completed": None,
        }
        manifiesto["worker_heartbeat_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_json(store, run_manifest_key(run_iso, model=FORECAST_MODEL), manifiesto)
        write_json(store, latest_manifest_key(FORECAST_MODEL), manifiesto)

    def calculate(product_id, paso, domain, valid_iso):
        contenido, _ = frame_payload(product_id, run, paso, domain)
        write_grid(store, frame_key(run_iso, product_id, valid_iso,
                   model=FORECAST_MODEL, scope=frame_scope(domain)), contenido)

    workers = calculation_workers()
    checkpoint()
    # La pasada entra en la lista del visor desde el primer frame, como en
    # AROME. Registrarla solo al final del ciclo dejaba en cabeza la anterior,
    # ya terminada, y con ciclos largos el visor decía «Completa» mientras
    # calculaba la nueva.
    _retire_replaced_runs(store, run, register_run_slot(store, manifiesto))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ecmwf-frame") as pool:
        for paso in pasos:
            if memory_deferred or (max_frames and publicados >= max_frames):
                break
            valid_iso = (run + timedelta(hours=paso)).isoformat().replace("+00:00", "Z")
            pending = [
                (product, domain)
                for product in PRODUCTS
                if valid_iso in catalogo["products"][product]["valid_times"]
                for domain in DOMAINS
                if valid_iso not in disponibles(manifest_product_key(product, domain))
            ]
            # Batches acotados: respetar max_frames incluso con errores y no
            # acumular payloads ni tareas de todos los plazos en memoria.
            with shared_downloads() as shared:
                if pending:
                    shared.prefetch(pool, run, list({
                        _field_key(run, paso_campo, selector): (paso_campo, selector)
                        for product in {product for product, _ in pending}
                        for paso_campo, selector in required_fields(product, paso)
                    }.values()))
                while pending and (not max_frames or publicados < max_frames):
                    count = min(workers, max_frames - publicados) if max_frames else workers
                    cached_bytes = sum(values.nbytes for values, _ in shared.fields.values())
                    count = memory_worker_capacity(min(count, len(pending)), cached_bytes)
                    if count == 0:
                        memory_deferred = True
                        logger.info("ECMWF: lote aplazado por memoria; se libera la caché "
                                    "y se reintentará en el siguiente ciclo.")
                        break
                    batch, pending = pending[:count], pending[count:]
                    tramo = manifiesto.setdefault("tier_timing", {}).setdefault("0", {"jobs": 0})
                    tramo.setdefault("first_start", _now_iso())
                    tramo["last_start"] = _now_iso()
                    futures = [(product, domain, pool.submit(
                        copy_context().run, calculate, product, paso, domain, valid_iso
                    )) for product, domain in batch]
                    for product, domain, future in futures:
                        key = manifest_product_key(product, domain)
                        try:
                            future.result()
                        except (EcmwfError, OSError) as exc:
                            fallos += 1
                            mark_error(manifiesto, key, valid_iso, str(exc))
                            logger.warning("ECMWF %s %s %s: %s", product, domain, valid_iso, exc)
                        else:
                            mark_available(manifiesto, key, valid_iso)
                            publicados += 1
                    tramo["jobs"] = int(tramo.get("jobs", 0)) + len(batch)
                    tramo["last_end"] = _now_iso()
                    checkpoint()

    disponibles_total = publicados_finales()
    total = sum(manifiesto["expected_totals"].values())
    manifiesto["status"] = "complete" if total and disponibles_total >= total else "publishing"
    manifiesto["waiting_reason"] = "memory" if memory_deferred else None
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
    if manifiesto["status"] == "complete" and not manifiesto.get("report_emitted"):
        manifiesto["report_emitted"] = emit_run_report(store, manifiesto)
    write_json(store, run_manifest_key(run_iso, model=FORECAST_MODEL), manifiesto)
    write_json(store, latest_manifest_key(FORECAST_MODEL), manifiesto)
    _retire_replaced_runs(store, run, register_run_slot(store, manifiesto))
    prune_retained_runs(store, model=FORECAST_MODEL)
    try:
        release_completed_map_cache(store, model=FORECAST_MODEL,
                                    scopes=[frame_scope(domain) for domain in DOMAINS])
    except OSError:
        logger.warning("ECMWF: no se pudo liberar la caché de páginas de los mapas", exc_info=True)
    return {
        "model": FORECAST_MODEL,
        "run": run_iso,
        "frames_published": publicados,
        "failures": fallos,
        "waiting_reason": manifiesto["waiting_reason"],
        "status": manifiesto["status"],
        "progress": manifiesto["progress"],
    }

"""
Cliente WCS de AROME y cálculos de predicción, sin interfaz.

Esto vivía en ``tabs/arome_forecast.py``, que es una aplicación de Streamlit:
el backend y el worker importaban de allí los cálculos, y por esa vía todo el
servicio arrastraba Streamlit aunque no sirviera ninguna pantalla suya. El
paquete se quejaba en cada petición de no encontrar su contexto —3.768 avisos
en hora y media, el 71 % del log— y cargaba en memoria una dependencia de
interfaz que nadie usaba.

Aquí está solo lo que el backend necesita: el cliente WCS, la geometría, los
diagnósticos y el ámbito de cálculo. Lo que pinta la pantalla (figuras de
Plotly, llavero, controles) se queda en ``tabs/``.

La dependencia va en una sola dirección: ``tabs/arome_forecast.py`` importa de
aquí, nunca al revés.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
import requests
from dotenv import load_dotenv
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.warp import Resampling, reproject
from shapely import contains_xy, make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Caché con caducidad, en sustitución de ``@st.cache_data``.
#
# Las dos funciones que la usaban no llamaban a Streamlit en su cuerpo: solo
# llevaban el decorador encima. Pero la sustitución no es mecánica, porque
# ``st.cache_data`` SERIALIZA el resultado y devuelve una copia nueva cada vez,
# mientras que un ``lru_cache`` devolvería siempre el mismo objeto. Para
# ``bytes``/``str`` da igual (son inmutables); para un ``dict`` no, así que ahí
# se guarda el JSON serializado y se reconstruye al leer.
#
# Es síncrona a propósito: la cadena de llamadas lo es, y convertirla a async
# solo para reutilizar ``AsyncTTLCache`` añadiría complejidad sin ganar nada.
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[Tuple[str, Tuple], Tuple[float, Any]] = {}


def _cache_get(espacio: str, clave: Tuple) -> Optional[Any]:
    with _CACHE_LOCK:
        entrada = _CACHE.get((espacio, clave))
        if entrada is None:
            return None
        caduca, valor = entrada
        if caduca <= time.time():
            del _CACHE[(espacio, clave)]
            return None
        return valor


def _cache_put(
    espacio: str, clave: Tuple, valor: Any, *, ttl_s: float, max_entries: int
) -> None:
    with _CACHE_LOCK:
        _CACHE[(espacio, clave)] = (time.time() + ttl_s, valor)
        if len(_CACHE) > max_entries:
            # Sin orden de acceso: basta con no crecer sin límite. Se tira
            # primero lo que antes caduca.
            for clave_vieja, _ in sorted(_CACHE.items(), key=lambda par: par[1][0])[
                : len(_CACHE) - max_entries
            ]:
                _CACHE.pop(clave_vieja, None)


def cache_clear() -> None:
    """Vacía la caché (tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()


WCS_BASE = (
    "https://public-api.meteofrance.fr/public/arome/1.0/wcs/"
    "MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS"
)


FORECAST_CATALONIA_BBOX = (0.10, 40.45, 3.45, 42.95)


REGIONS_BOUNDARY_URL = (
    "https://mapas.fomento.gob.es/arcgis/rest/services/SIU/"
    "ENTIDADES_TERRITORIALES_EGRN/MapServer/1/query"
)


GRAVITY = 9.80665


SHIP_SCALE = 44_000_000.0


LOCAL_TZ = ZoneInfo("Europe/Madrid")


RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


API_MAX_ATTEMPTS = 4


def forecast_calculation_scope() -> str:
    """Alcance WCS: dominio completo en Railway y Cataluña en local."""
    configured = os.getenv("METEOLABX_FORECAST_CALCULATION_SCOPE", "").strip().lower()
    if configured in {"model", "catalonia"}:
        return configured
    is_railway = any(
        os.getenv(name)
        for name in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID")
    )
    return "model" if is_railway else "catalonia"


PALETTE = [
    "#3b4cc0",
    "#3288bd",
    "#66c2a5",
    "#abdda4",
    "#e6f598",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d73027",
    "#762a83",
]


PREFIX_CANDIDATES = {
    "height_u": [
        "U_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "WIND_U_COMPONENT__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "U__HEIGHT",
    ],
    "height_v": [
        "V_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "WIND_V_COMPONENT__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "V__HEIGHT",
    ],
    "height_wind": ["WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND"],
    "pressure_u": [
        "U_COMPONENT_OF_WIND__ISOBARIC_SURFACE",
        "WIND_U_COMPONENT__ISOBARIC_SURFACE",
        "U__ISOBARIC",
    ],
    "pressure_v": [
        "V_COMPONENT_OF_WIND__ISOBARIC_SURFACE",
        "WIND_V_COMPONENT__ISOBARIC_SURFACE",
        "V__ISOBARIC",
    ],
    "pressure_wind": ["WIND__ISOBARIC_SURFACE"],
    "geopotential": [
        "GEOPOTENTIAL__ISOBARIC_SURFACE",
        "GEOPOTENTIAL_HEIGHT__ISOBARIC_SURFACE",
        "Z__ISOBARIC",
    ],
    "terrain": ["GEOMETRIC_HEIGHT__GROUND_OR_WATER_SURFACE", "ALTITUDE__GROUND"],
    "cape_mu": [
        "CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY__GROUND_OR_WATER_SURFACE",
        "CAPE_INS__GROUND",
    ],
    # El WCS responde con catálogos distintos según el backend que atienda:
    # unas veces anuncia identificadores largos y otras abreviados, y ninguno
    # de los dos conjuntos es completo. Se listan todos los alias conocidos
    # para que el producto resuelva con cualquiera de ellos.
    "cape_ml": ["MEAN_LAYER_CAPE__GROUND_OR_WATER_SURFACE", "MLCAPE__GROUND"],
    "height_temperature": [
        "TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "T__HEIGHT",
    ],
    "pressure_temperature": ["TEMPERATURE__ISOBARIC_SURFACE", "T__ISOBARIC"],
    "pressure_dewpoint": [
        "DEW_POINT_TEMPERATURE__ISOBARIC_SURFACE",
        "TD__ISOBARIC",
    ],
    "height_dewpoint": [
        "DEW_POINT_TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "TD__HEIGHT",
    ],
    "surface_pressure": ["PRESSURE__GROUND_OR_WATER_SURFACE", "P__GROUND"],
    "mean_sea_level_pressure": ["PRESSURE__MEAN_SEA_LEVEL", "MSL__MEAN_SEA_LEVEL"],
    # En dBZ, que es la que se lee como un radar. La otra variante que publica
    # el WCS, REFLECTIVITY_MAX sin sufijo, viene en unidades lineales.
    "reflectivity_max": [
        "REFLECTIVITY_MAX_DBZ__GROUND_OR_WATER_SURFACE",
        "REFL_MAX_DBZ__GROUND",
    ],
    "precipitation_1h": [
        "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE",
        "PRECIP__GROUND",
    ],
    "precipitation_type_1h": [
        "PRECIPITATION_TYPE_60_MIN__GROUND_OR_WATER_SURFACE",
        "PRECIPITATION_TYPE__GROUND_OR_WATER_SURFACE",
        "TYPE_OF_PRECIPITATION__GROUND_OR_WATER_SURFACE",
        "PTYPE_60__GROUND_OR_WATER_SURFACE",
        "PTYPE_60__GROUND",
        "PTYPE_60",
        "PRECIPITATION_TYPE_60_MIN",
    ],
    "wind_gust_1h": [
        # Solo alias de ráfaga MÁXIMA: el producto se publica como tal, y
        # FF_RAF/WIND_SPEED_GUST son la ráfaga sin más.
        "WIND_SPEED_MAXIMUM_GUST__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "WIND_SPEED_GUST_MAX__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "FF_RAF_MAX__HEIGHT",
    ],
    "liquid_precipitation_1h": [
        "TOTAL_WATER_PRECIPITATION__GROUND_OR_WATER_SURFACE",
    ],
    "pressure_relative_humidity": [
        "RELATIVE_HUMIDITY__ISOBARIC_SURFACE",
        "HU__ISOBARIC",
    ],
    "shortwave_down_1h": [
        "DOWNWARD_SHORT_WAVE_RADIATION_FLUX__GROUND_OR_WATER_SURFACE",
        "FLSOLAIRE__GROUND",
    ],
    "total_cloud_cover": [
        "TOTAL_CLOUD_COVER__GROUND_OR_WATER_SURFACE",
        "NEBUL__GROUND",
    ],
}


class AromeError(RuntimeError):
    """Error legible para fallos de catálogo, autenticación o cobertura."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> Optional[str]:
    for child in element.iter():
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _parse_run(coverage_id: str) -> Optional[datetime]:
    match = re.search(r"___(\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z)", coverage_id)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H.%M.%SZ").replace(
        tzinfo=timezone.utc
    )


def _parse_coverage_period(coverage_id: str) -> Optional[str]:
    match = re.search(r"Z_(P(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?)$", coverage_id)
    return match.group(1) if match else None


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _credential_headers(token: str) -> Dict[str, str]:
    """Return the authentication headers expected by the Meteo-France portal.

    Long-lived API keys are JWTs whose payload contains
    ``"token_type": "apiKey"``.  Unlike OAuth2 access tokens, the AROME
    gateway expects those keys in the ``apikey`` header, without ``Bearer``.
    Decoding here is only used to select the header; it does not attempt to
    validate the JWT signature.
    """
    payload: Dict[str, object] = {}
    try:
        encoded_payload = token.split(".")[1]
        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        )
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    token_type = str(payload.get("token_type", "")).lower()
    if token_type == "apikey":
        return {"apikey": token, "Accept": "*/*"}
    return {"Authorization": f"Bearer {token}", "Accept": "*/*"}


def _retry_delay(response, attempt: int) -> float:
    """Espera antes de reintentar, respetando Retry-After si la API lo indica."""
    header = str(response.headers.get("Retry-After", "")).strip()
    if header:
        try:
            return max(0.5, min(60.0, float(header)))
        except ValueError:
            pass
    return 1.5 * (2**attempt)


def _wait_for_api_request_slot(interval: float | None = None) -> None:
    """Escalona GetCoverage entre todos los procesos del contenedor."""
    delay_between_requests = max(
        0.1,
        float(
            interval
            if interval is not None
            # La suscripcion AROME esta en el tier 50PerMin: una peticion cada
            # 1,2 s. El lock es global al contenedor, asi que este intervalo
            # gobierna el ritmo total. 1,25 s deja 48/min, con margen.
            else os.getenv("METEOLABX_AROME_REQUEST_INTERVAL_S", "1.25")
        ),
    )
    lock_path = Path(
        os.getenv(
            "METEOLABX_AROME_REQUEST_THROTTLE_FILE",
            "/tmp/meteolabx-arome-request-throttle",
        )
    )
    try:
        import fcntl
    except ImportError:  # pragma: no cover - producción y desarrollo son Unix
        time.sleep(delay_between_requests)
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="ascii") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw_next = handle.read().strip()
            next_request = float(raw_next) if raw_next else 0.0
            # Reloj de pared, no monotónico: el turno se comparte entre
            # procesos y el origen de `monotonic` no tiene por qué coincidir
            # entre ellos. En macOS arranca en cero con cada proceso, así que
            # el segundo leía el turno del primero como si faltaran horas: una
            # sola petición local llegó a quedarse dormida dos horas y media.
            delay = next_request - time.time()
            # Y por si el turno guardado no vale —reloj cambiado de hora, un
            # fichero de otra máquina—, nunca se espera más de un turno: como
            # mucho se pierde el ritmo una vez y se recupera solo.
            if delay > 0:
                time.sleep(min(delay, delay_between_requests))
            handle.seek(0)
            handle.truncate()
            handle.write(str(time.time() + delay_between_requests))
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


METADATA_CACHE_TTL_S = max(
    5, int(os.getenv("METEOLABX_AROME_METADATA_CACHE_TTL_S", "120"))
)


def _metadata_cache_path(url: str, params: Tuple[Tuple[str, str], ...]) -> Path:
    firma = hashlib.sha256(f"{url}|{params}".encode("utf-8")).hexdigest()[:32]
    return Path(tempfile.gettempdir()) / "meteolabx-wcs-metadata" / f"{firma}.xml"


def _cached_metadata(ruta: Path) -> Optional[bytes]:
    """Devuelve los metadatos guardados si no han caducado."""
    try:
        edad = time.time() - ruta.stat().st_mtime
        if edad > METADATA_CACHE_TTL_S:
            return None
        contenido = ruta.read_bytes()
    except OSError:
        return None
    return contenido or None


def _store_metadata(ruta: Path, contenido: bytes) -> None:
    """Guarda los metadatos con un temporal, para que nadie lea a medias."""
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        parcial = ruta.with_suffix(f".{os.getpid()}.part")
        parcial.write_bytes(contenido)
        parcial.replace(ruta)
    except OSError:
        pass


def _api_get_metadata(
    url: str, params: Tuple[Tuple[str, str], ...], token: str
) -> Tuple[bytes, str]:
    """GetCapabilities o DescribeCoverage, compartidos entre procesos."""
    ruta = _metadata_cache_path(url, params)
    guardado = _cached_metadata(ruta)
    if guardado is not None:
        return guardado, "application/xml"
    contenido, tipo = _api_get(url, params, token)
    _store_metadata(ruta, contenido)
    return contenido, tipo


# 900 s y 256 entradas, los mismos que tenía con ``st.cache_data``: cambiar los
# tiempos durante la extracción mezclaría la migración con un cambio funcional.
_API_GET_TTL_S = 900.0
_API_GET_MAX_ENTRIES = 256


def _api_get(
    url: str,
    params: Tuple[Tuple[str, str], ...],
    _token: str,
) -> Tuple[bytes, str]:
    """Descarga cacheada del WCS.

    ``_token`` NO forma parte de la clave, y es deliberado: Streamlit ignoraba
    los argumentos cuyo nombre empieza por guion bajo, y se conserva ese
    comportamiento para no cambiar nada por el camino. Si algún día dos
    credenciales devolvieran respuestas distintas para la misma petición, habría
    que meter en la clave una HUELLA del token, nunca el token en claro.
    """
    en_cache = _cache_get("api_get", (url, params))
    if en_cache is not None:
        return en_cache
    resultado = _api_get_sin_cache(url, params, _token)
    _cache_put(
        "api_get", (url, params), resultado,
        ttl_s=_API_GET_TTL_S, max_entries=_API_GET_MAX_ENTRIES,
    )
    return resultado


def _api_get_sin_cache(
    url: str,
    params: Tuple[Tuple[str, str], ...],
    _token: str,
) -> Tuple[bytes, str]:
    response = None
    last_connection_error: Optional[requests.RequestException] = None
    for attempt in range(API_MAX_ATTEMPTS):
        try:
            if url.rstrip("/").endswith("GetCoverage"):
                _wait_for_api_request_slot()
            response = requests.get(
                url,
                params=list(params),
                headers=_credential_headers(_token),
                timeout=90,
            )
            last_connection_error = None
        except requests.RequestException as exc:
            last_connection_error = exc
            if attempt == API_MAX_ATTEMPTS - 1:
                break
            time.sleep(1.5 * (2**attempt))
            continue

        if (
            response.status_code in RETRYABLE_HTTP_CODES
            and attempt < API_MAX_ATTEMPTS - 1
        ):
            time.sleep(_retry_delay(response, attempt))
            continue
        break

    if response is None:
        raise AromeError(
            "No se pudo conectar con Météo-France después de varios intentos: "
            f"{last_connection_error}"
        ) from last_connection_error

    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise AromeError(
                "Météo-France rechazó la credencial o la suscripción AROME "
                f"(HTTP {response.status_code})."
            )
        if response.status_code in RETRYABLE_HTTP_CODES:
            raise AromeError(
                "Météo-France está temporalmente fuera de servicio "
                f"(HTTP {response.status_code}) después de {API_MAX_ATTEMPTS} "
                "intentos. Espera un momento y vuelve a probar."
            )
        detail = response.text[:500].replace("\n", " ").strip()
        raise AromeError(
            f"La API de AROME devolvió HTTP {response.status_code}: {detail}"
        )

    content_type = response.headers.get("Content-Type", "").lower()
    if response.content.lstrip().startswith(b"<ExceptionReport"):
        raise AromeError(response.text[:700].replace("\n", " "))
    return response.content, content_type


_REGIONS_TTL_S = 86400.0


def _load_forecast_regions_geojson() -> dict:
    """Fronteras de las tres provincias, cacheadas un día.

    Se guarda el JSON SERIALIZADO, no el diccionario: ``st.cache_data`` devolvía
    una copia nueva en cada llamada y quien lo recibía podía modificarlo sin
    afectar a nadie. Con el objeto vivo, un solo consumidor descuidado
    envenenaría la caché para el resto del día.
    """
    en_cache = _cache_get("regions", ())
    if en_cache is not None:
        return json.loads(en_cache)
    datos = _load_forecast_regions_geojson_sin_cache()
    _cache_put("regions", (), json.dumps(datos), ttl_s=_REGIONS_TTL_S, max_entries=4)
    return datos


def _load_forecast_regions_geojson_sin_cache() -> dict:
    params = {
        "where": "CodINE IN ('02','09','10')",
        "outFields": "NAMEUNIT,CodINE",
        "returnGeometry": "true",
        "outSR": "4326",
        "maxAllowableOffset": "0.004",
        "geometryPrecision": "5",
        "f": "geojson",
    }
    response = requests.get(REGIONS_BOUNDARY_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not data.get("features"):
        raise AromeError("El servicio cartográfico no devolvió las tres comunidades.")
    return data


@dataclass(frozen=True)
class CoverageMetadata:
    axes: Dict[str, Tuple[float, ...]]
    units: Dict[str, str]
    begin: Optional[datetime]
    end: Optional[datetime]

    def vertical_axis(self) -> Optional[str]:
        for name in self.axes:
            if name.lower() not in {"long", "lon", "latitude", "lat", "time"}:
                return name
        return None

    def valid_times(self, run: datetime) -> List[datetime]:
        offsets = self.axes.get("time", ())
        if offsets:
            return [run + timedelta(seconds=float(value)) for value in offsets]
        if self.begin and self.end:
            result = []
            value = self.begin
            while value <= self.end:
                result.append(value)
                value += timedelta(hours=1)
            return result
        return [run]


@dataclass
class RasterField:
    data: np.ndarray
    transform: rasterio.Affine
    crs: CRS
    bounds: Tuple[float, float, float, float]
    units: str = ""
    vector_u: Optional[np.ndarray] = None
    vector_v: Optional[np.ndarray] = None
    overlay: Optional[np.ndarray] = None
    overlay_units: str = ""


class CoverageCatalog:
    def __init__(self, xml_bytes: bytes):
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise AromeError("La respuesta GetCapabilities no es XML válido.") from exc

        self.by_prefix: Dict[str, Dict[datetime, str]] = {}
        self.by_prefix_period: Dict[str, Dict[datetime, Dict[str, str]]] = {}
        self.titles: Dict[str, str] = {}
        for summary in root.iter():
            if _local_name(summary.tag) != "CoverageSummary":
                continue
            coverage_id = _child_text(summary, "CoverageId")
            if not coverage_id or "___" not in coverage_id:
                continue
            prefix = coverage_id.split("___", 1)[0]
            run = _parse_run(coverage_id)
            if run is None:
                continue
            self.by_prefix.setdefault(prefix, {})[run] = coverage_id
            period = _parse_coverage_period(coverage_id)
            if period:
                self.by_prefix_period.setdefault(prefix, {}).setdefault(run, {})[
                    period
                ] = coverage_id
            self.titles[prefix] = _child_text(summary, "Title") or prefix

        if not self.by_prefix:
            raise AromeError("GetCapabilities no contiene coberturas AROME utilizables.")

    @property
    def prefixes(self) -> Sequence[str]:
        return tuple(self.by_prefix)

    def resolve(self, kind: str) -> str:
        for candidate in PREFIX_CANDIDATES[kind]:
            if candidate in self.by_prefix:
                return candidate

        if kind == "precipitation_type_1h":
            # Algunas versiones del catálogo conservan PTYPE_60 y otras
            # publican un nombre CF. Limitarse al campo de superficie evita
            # confundir el diagnóstico con la precipitación acumulada.
            for prefix in self.prefixes:
                if (("PRECIPITATION_TYPE" in prefix or "TYPE_OF_PRECIPITATION" in prefix
                     or "PTYPE_60" in prefix) and (
                    "GROUND" in prefix or "PTYPE_60" in prefix
                )):
                    return prefix

        if kind in {"height_u", "height_v"}:
            combined = self.resolve_optional("height_wind")
            if combined:
                return combined
        if kind in {"pressure_u", "pressure_v"}:
            combined = self.resolve_optional("pressure_wind")
            if combined:
                return combined

        if kind == "geopotential":
            for prefix in self.prefixes:
                if "GEOPOTENTIAL" in prefix and "ISOBARIC_SURFACE" in prefix:
                    return prefix
        if kind == "terrain":
            for prefix in self.prefixes:
                if "GEOMETRIC_HEIGHT" in prefix and "GROUND_OR_WATER_SURFACE" in prefix:
                    return prefix

        desired_level = (
            "SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND"
            if kind.startswith("height")
            else "ISOBARIC_SURFACE"
        )
        component = "U_COMPONENT" if kind.endswith("_u") else "V_COMPONENT"
        for prefix in self.prefixes:
            if desired_level in prefix and component in prefix and "GUST" not in prefix:
                return prefix

        available = ", ".join(sorted(self.prefixes))
        raise AromeError(
            f"No se encontró la cobertura necesaria ({kind}). "
            f"Coberturas anunciadas: {available}"
        )

    def resolve_optional(self, kind: str) -> Optional[str]:
        for candidate in PREFIX_CANDIDATES[kind]:
            if candidate in self.by_prefix:
                return candidate
        return None

    def runs_for(self, prefix: str, period: Optional[str] = None) -> set[datetime]:
        if period is None:
            return set(self.by_prefix.get(prefix, {}))
        return {
            run
            for run, variants in self.by_prefix_period.get(prefix, {}).items()
            if period in variants
        }

    def coverage_id(
        self, prefix: str, run: datetime, period: Optional[str] = None
    ) -> str:
        try:
            if period is not None:
                return self.by_prefix_period[prefix][run][period]
            return self.by_prefix[prefix][run]
        except KeyError as exc:
            suffix = f" con acumulación {period}" if period else ""
            raise AromeError(
                f"No existe {prefix}{suffix} para el run {_iso_utc(run)}."
            ) from exc

    def latest_common_run(self, prefixes: Iterable[str]) -> datetime:
        unique = set(prefixes)
        runs: Optional[set] = None
        for prefix in unique:
            current = set(self.by_prefix.get(prefix, {}))
            runs = current if runs is None else runs & current
        if not runs:
            raise AromeError("No hay un run común para todas las variables requeridas.")
        return max(runs)


class AromeWCS:
    def __init__(self, token: str):
        self.token = token.strip()
        if not self.token:
            raise AromeError("Falta la clave de la API de Météo-France.")

    def capabilities(self) -> CoverageCatalog:
        params = (
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("language", "fre"),
        )
        content, _ = _api_get_metadata(
            f"{WCS_BASE}/GetCapabilities", params, self.token
        )
        return CoverageCatalog(content)

    def describe(self, coverage_id: str) -> CoverageMetadata:
        params = (
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageID", coverage_id),
        )
        content, _ = _api_get_metadata(
            f"{WCS_BASE}/DescribeCoverage", params, self.token
        )
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise AromeError("DescribeCoverage no devolvió XML válido.") from exc

        axes: Dict[str, Tuple[float, ...]] = {}
        units: Dict[str, str] = {}
        begin: Optional[datetime] = None
        end: Optional[datetime] = None

        for element in root.iter():
            if _local_name(element.tag) == "EnvelopeWithTimePeriod":
                labels = element.attrib.get("axisLabels", "").split()
                uoms = element.attrib.get("uomLabels", "").split()
                units.update(dict(zip(labels, uoms)))
                begin_text = _child_text(element, "beginPosition")
                end_text = _child_text(element, "endPosition")
                if begin_text:
                    begin = datetime.fromisoformat(begin_text.replace("Z", "+00:00"))
                if end_text:
                    end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))

            if _local_name(element.tag) != "GeneralGridAxis":
                continue
            axis_name = _child_text(element, "gridAxesSpanned")
            coefficient_text = _child_text(element, "coefficients") or ""
            values: List[float] = []
            for item in coefficient_text.split():
                try:
                    values.append(float(item))
                except ValueError:
                    pass
            if axis_name:
                axes[axis_name] = tuple(values)
                for child in element.iter():
                    if _local_name(child.tag) == "offsetVector":
                        labels = child.attrib.get("axisLabels", "").split()
                        uoms = child.attrib.get("uomLabels", "").split()
                        units.update(dict(zip(labels, uoms)))

        return CoverageMetadata(axes=axes, units=units, begin=begin, end=end)

    def get_field(
        self,
        catalog: CoverageCatalog,
        prefix: str,
        run: datetime,
        valid_time: Optional[datetime],
        vertical_target: Optional[float],
        vertical_kind: Optional[str],
        component: Optional[str] = None,
        period: Optional[str] = None,
    ) -> RasterField:
        coverage_id = catalog.coverage_id(prefix, run, period=period)
        metadata = self.describe(coverage_id)
        params: List[Tuple[str, str]] = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageid", coverage_id),
        ]

        if "time" in metadata.axes:
            selected_time = valid_time or metadata.valid_times(run)[0]
            params.append(("subset", f"time({_iso_utc(selected_time)})"))

        vertical_axis = metadata.vertical_axis()
        if vertical_axis and vertical_target is not None:
            actual_value = _nearest_vertical_value(
                metadata, vertical_axis, vertical_target, vertical_kind or "height"
            )
            params.append(("subset", f"{vertical_axis}({_format_number(actual_value)})"))

        if forecast_calculation_scope() == "catalonia":
            west, south, east, north = FORECAST_CATALONIA_BBOX
            params.extend(
                [
                    ("subset", f"lat({south},{north})"),
                    ("subset", f"long({west},{east})"),
                ]
            )
        params.append(("format", "application/wmo-grib"))
        content, content_type = _api_get(
            f"{WCS_BASE}/GetCoverage", tuple(params), self.token
        )
        if "xml" in content_type or content.lstrip().startswith(b"<"):
            detail = content[:800].decode("utf-8", errors="replace").replace("\n", " ")
            raise AromeError(f"GetCoverage devolvió XML en vez de GRIB2: {detail}")
        return _read_raster(content, component=component)

    def get_point_isobaric(
        self, catalog: CoverageCatalog, prefix: str, run: datetime,
        valid_time: datetime, latitude: float, longitude: float,
    ) -> dict[float, tuple[float, str]]:
        """Pide todos los niveles de una columna en una sola cobertura pequeña."""
        coverage_id = catalog.coverage_id(prefix, run)
        params: List[Tuple[str, str]] = [
            ("service", "WCS"), ("version", "2.0.1"), ("coverageid", coverage_id),
            ("subset", f"time({_iso_utc(valid_time)})"),
            ("subset", f"lat({latitude - 0.04:.5f},{latitude + 0.04:.5f})"),
            ("subset", f"long({longitude - 0.04:.5f},{longitude + 0.04:.5f})"),
            ("format", "application/wmo-grib"),
        ]
        content, content_type = _api_get(
            f"{WCS_BASE}/GetCoverage", tuple(params), self.token
        )
        if "xml" in content_type or content.lstrip().startswith(b"<"):
            raise AromeError("AROME no devolvió el perfil isobárico en GRIB2.")
        output: dict[float, tuple[float, str]] = {}
        try:
            with MemoryFile(content) as memory_file, memory_file.open() as dataset:
                row, col = dataset.index(longitude, latitude)
                if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                    raise AromeError("El punto está fuera del perfil solicitado.")
                for band in range(1, dataset.count + 1):
                    tags = dataset.tags(band)
                    short_name = tags.get("GRIB_SHORT_NAME", "")
                    if not short_name.endswith("-ISBL"):
                        continue
                    try:
                        encoded_level = int(short_name.split("-", 1)[0])
                        pressure_hpa = encoded_level / 100.0 if encoded_level > 2_000 else float(encoded_level)
                    except ValueError:
                        continue
                    value = float(dataset.read(band, window=((row, row + 1), (col, col + 1)))[0, 0])
                    if dataset.nodata is not None and np.isclose(value, dataset.nodata):
                        value = float("nan")
                    units = ((dataset.units[band - 1] if dataset.units else None)
                             or tags.get("GRIB_UNIT") or "")
                    output[pressure_hpa] = (value, str(units))
        except rasterio.errors.RasterioError as exc:
            raise AromeError("No se pudo leer el perfil GRIB2 de AROME.") from exc
        if not output:
            raise AromeError("La cobertura no contiene niveles isobáricos reconocibles.")
        return output


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _nearest_vertical_value(
    metadata: CoverageMetadata,
    axis_name: str,
    target: float,
    kind: str,
) -> float:
    values = metadata.axes.get(axis_name, ())
    unit = metadata.units.get(axis_name, "").lower()
    if not values:
        if kind == "pressure" and ("pa" in unit and "hpa" not in unit):
            return target * 100.0
        return target

    array = np.asarray(values, dtype=float)
    comparable = array.copy()
    pressure_unit_is_pa = "pa" in unit and "hpa" not in unit
    if kind == "pressure" and (
        np.nanmax(np.abs(array)) > 2000 or pressure_unit_is_pa
    ):
        comparable = array / 100.0
    index = int(np.nanargmin(np.abs(comparable - target)))
    if abs(comparable[index] - target) > (2 if kind == "pressure" else 5):
        raise AromeError(
            f"El nivel {target:g} ({kind}) no está disponible en {axis_name}."
        )
    return float(array[index])


def _choose_band(dataset: rasterio.DatasetReader, component: Optional[str]) -> int:
    if dataset.count == 1 or not component:
        return 1
    component = component.lower()
    patterns = {
        "u": ("ugrd", "u-component", "u component", "eastward", "zonal"),
        "v": ("vgrd", "v-component", "v component", "northward", "merid"),
    }[component]
    for index in range(1, dataset.count + 1):
        tags = " ".join(f"{key}={value}" for key, value in dataset.tags(index).items())
        description = dataset.descriptions[index - 1] or ""
        text = f"{description} {tags}".lower()
        if any(pattern in text for pattern in patterns):
            return index
    raise AromeError(f"El GRIB2 no permite identificar la componente {component.upper()}.")


def _read_raster(content: bytes, component: Optional[str] = None) -> RasterField:
    try:
        with MemoryFile(content) as memory_file:
            with memory_file.open() as dataset:
                band = _choose_band(dataset, component)
                data = dataset.read(band).astype(np.float64)
                nodata = dataset.nodata
                if nodata is not None:
                    data[np.isclose(data, nodata)] = np.nan
                data[~np.isfinite(data)] = np.nan
                tags = dataset.tags(band)
                units = (
                    (dataset.units[band - 1] if dataset.units else None)
                    or tags.get("GRIB_UNIT")
                    or tags.get("units")
                    or ""
                )
                crs = dataset.crs or CRS.from_epsg(4326)
                bounds = (
                    float(dataset.bounds.left),
                    float(dataset.bounds.bottom),
                    float(dataset.bounds.right),
                    float(dataset.bounds.top),
                )
                return RasterField(data, dataset.transform, crs, bounds, str(units))
    except rasterio.errors.RasterioError as exc:
        raise AromeError("No se pudo leer el GRIB2 devuelto por AROME.") from exc


def _same_grid(first: RasterField, second: RasterField) -> bool:
    return (
        first.data.shape == second.data.shape
        and first.crs == second.crs
        and np.allclose(tuple(first.transform), tuple(second.transform), atol=1e-9)
    )


def _align(reference: RasterField, field: RasterField) -> np.ndarray:
    if _same_grid(reference, field):
        return field.data
    destination = np.full(reference.data.shape, np.nan, dtype=np.float64)
    reproject(
        source=field.data,
        destination=destination,
        src_transform=field.transform,
        src_crs=field.crs,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return destination


def _grid_lon_lat(field: RasterField) -> Tuple[np.ndarray, np.ndarray]:
    if field.crs.to_epsg() not in (None, 4326) and not field.crs.is_geographic:
        raise AromeError("La cobertura no está en una rejilla geográfica EPSG:4326.")
    height, width = field.data.shape
    cols = np.arange(width, dtype=float) + 0.5
    rows = np.arange(height, dtype=float) + 0.5
    lon = field.transform.c + cols * field.transform.a
    lat = field.transform.f + rows * field.transform.e
    return np.meshgrid(lon, lat)


def _height_from_geopotential(values: np.ndarray, units: str) -> np.ndarray:
    unit_text = units.lower().replace(" ", "")
    median = float(np.nanmedian(np.abs(values)))
    if "m^2" in unit_text or "m2" in unit_text or median > 20000:
        return values / GRAVITY
    return values


def _interpolate_at_height(
    heights: np.ndarray,
    values: np.ndarray,
    target: np.ndarray,
) -> np.ndarray:
    """Interpola values[level,y,x] a target[y,x] usando heights[level,y,x]."""
    order = np.argsort(heights, axis=0)
    sorted_h = np.take_along_axis(heights, order, axis=0)
    sorted_v = np.take_along_axis(values, order, axis=0)
    upper = np.sum(sorted_h < target[None, :, :], axis=0)
    upper = np.clip(upper, 1, sorted_h.shape[0] - 1)
    lower = upper - 1
    h0 = np.take_along_axis(sorted_h, lower[None, :, :], axis=0)[0]
    h1 = np.take_along_axis(sorted_h, upper[None, :, :], axis=0)[0]
    v0 = np.take_along_axis(sorted_v, lower[None, :, :], axis=0)[0]
    v1 = np.take_along_axis(sorted_v, upper[None, :, :], axis=0)[0]
    fraction = np.divide(
        target - h0,
        h1 - h0,
        out=np.full_like(target, np.nan, dtype=float),
        where=np.abs(h1 - h0) > 1e-6,
    )
    result = v0 + fraction * (v1 - v0)
    valid = (target >= sorted_h[0]) & (target <= sorted_h[-1])
    result[~valid] = np.nan
    return result


def _resolved_prefixes(
    catalog: CoverageCatalog, depth_m: int, product_kind: str = "shear"
) -> Dict[str, str]:
    result = {
        "height_u": catalog.resolve("height_u"),
        "height_v": catalog.resolve("height_v"),
    }
    if depth_m == 6000:
        result.update(
            {
                "pressure_u": catalog.resolve("pressure_u"),
                "pressure_v": catalog.resolve("pressure_v"),
                "geopotential": catalog.resolve("geopotential"),
            }
        )
        terrain = catalog.resolve_optional("terrain")
        if terrain:
            result["terrain"] = terrain
    if product_kind == "ship":
        result.update(
            {
                "cape_mu": catalog.resolve("cape_mu"),
                "pressure_temperature": catalog.resolve("pressure_temperature"),
                "height_dewpoint": catalog.resolve("height_dewpoint"),
                "surface_pressure": catalog.resolve("surface_pressure"),
            }
        )
    return result


def _get_uv_height(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
    height_m: float,
) -> Tuple[RasterField, RasterField]:
    u = client.get_field(
        catalog,
        prefixes["height_u"],
        run,
        valid_time,
        height_m,
        "height",
        component="u",
    )
    v = client.get_field(
        catalog,
        prefixes["height_v"],
        run,
        valid_time,
        height_m,
        "height",
        component="v",
    )
    return u, v


def _compute_shear(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
    depth_m: int,
    base_uv: Optional[Tuple[RasterField, RasterField]] = None,
    isobaric_levels: Optional[Dict[float, Dict[str, RasterField]]] = None,
) -> RasterField:
    """Cizalladura entre 10 m y `depth_m`.

    `base_uv` permite compartir el viento de superficie entre las tres
    profundidades: es el mismo campo para las tres y descargarlo una vez por
    producto multiplicaba las peticiones al WCS.
    """
    base_u, base_v = base_uv or _get_uv_height(
        client, catalog, prefixes, run, valid_time, 10.0
    )
    u0 = base_u.data
    v0 = _align(base_u, base_v)

    if depth_m in (1000, 3000):
        top_u, top_v = _get_uv_height(
            client, catalog, prefixes, run, valid_time, float(depth_m)
        )
        ut = _align(base_u, top_u)
        vt = _align(base_u, top_v)
    else:
        terrain = np.zeros_like(u0)
        terrain_prefix = prefixes.get("terrain")
        if terrain_prefix:
            terrain_runs = catalog.by_prefix[terrain_prefix]
            terrain_run = run if run in terrain_runs else max(terrain_runs)
            terrain_field = client.get_field(
                catalog,
                terrain_prefix,
                terrain_run,
                None,
                None,
                None,
            )
            terrain = _align(base_u, terrain_field)
            terrain = np.where(np.isfinite(terrain), terrain, 0.0)

        pressure_levels_hpa = (500, 450, 400, 350, 300, 250)
        u_levels: List[np.ndarray] = []
        v_levels: List[np.ndarray] = []
        z_levels: List[np.ndarray] = []
        for pressure in pressure_levels_hpa:
            if isobaric_levels and float(pressure) in isobaric_levels:
                # Esos niveles ya vienen en el paquete GRIB que se descarga
                # para el perfil convectivo: son 18 peticiones menos por hora.
                nivel = isobaric_levels[float(pressure)]
                u_levels.append(_align(base_u, nivel["u"]))
                v_levels.append(_align(base_u, nivel["v"]))
                z_field = nivel["geopotential"]
                z_levels.append(
                    _height_from_geopotential(_align(base_u, z_field), z_field.units)
                )
                continue
            u_field = client.get_field(
                catalog,
                prefixes["pressure_u"],
                run,
                valid_time,
                pressure,
                "pressure",
                component="u",
            )
            v_field = client.get_field(
                catalog,
                prefixes["pressure_v"],
                run,
                valid_time,
                pressure,
                "pressure",
                component="v",
            )
            z_field = client.get_field(
                catalog,
                prefixes["geopotential"],
                run,
                valid_time,
                pressure,
                "pressure",
            )
            u_levels.append(_align(base_u, u_field))
            v_levels.append(_align(base_u, v_field))
            z_values = _align(base_u, z_field)
            z_levels.append(_height_from_geopotential(z_values, z_field.units))

        heights = np.stack(z_levels)
        target = terrain + 6000.0
        ut = _interpolate_at_height(heights, np.stack(u_levels), target)
        vt = _interpolate_at_height(heights, np.stack(v_levels), target)

    shear = np.hypot(ut - u0, vt - v0)
    shear[~np.isfinite(shear)] = np.nan
    return RasterField(
        shear,
        base_u.transform,
        base_u.crs,
        base_u.bounds,
        "m/s",
        vector_u=ut - u0,
        vector_v=vt - v0,
    )


def _to_celsius(values: np.ndarray, units: str) -> np.ndarray:
    text = units.lower().strip()
    if text in {"k", "kelvin"} or float(np.nanmedian(values)) > 150:
        return values - 273.15
    return values


def _to_hpa(values: np.ndarray, units: str) -> np.ndarray:
    text = units.lower().replace(" ", "")
    if ("pa" in text and "hpa" not in text) or float(np.nanmedian(values)) > 2000:
        return values / 100.0
    return values


def _mixing_ratio_from_dewpoint(
    dewpoint_c: np.ndarray, pressure_hpa: np.ndarray
) -> np.ndarray:
    vapor_pressure = 6.112 * np.exp(17.67 * dewpoint_c / (dewpoint_c + 243.5))
    return np.divide(
        621.97 * vapor_pressure,
        pressure_hpa - vapor_pressure,
        out=np.full_like(dewpoint_c, np.nan, dtype=float),
        where=pressure_hpa > vapor_pressure,
    )


def _ship_formula(
    mucape: np.ndarray,
    mixing_ratio_gkg: np.ndarray,
    temperature_700_c: np.ndarray,
    temperature_500_c: np.ndarray,
    height_700_m: np.ndarray,
    height_500_m: np.ndarray,
    shear_0_6_ms: np.ndarray,
) -> np.ndarray:
    lapse_rate = np.divide(
        (temperature_700_c - temperature_500_c) * 1000.0,
        height_500_m - height_700_m,
        out=np.full_like(mucape, np.nan, dtype=float),
        where=(height_500_m - height_700_m) > 0,
    )
    ship = (
        np.maximum(mucape, 0.0)
        * np.maximum(mixing_ratio_gkg, 0.0)
        * np.maximum(lapse_rate, 0.0)
        * np.maximum(-temperature_500_c, 0.0)
        * np.maximum(shear_0_6_ms, 0.0)
        / SHIP_SCALE
    )
    ship[~np.isfinite(ship)] = np.nan
    return ship


def _compute_ship(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
) -> RasterField:
    mucape_field = client.get_field(
        catalog, prefixes["cape_mu"], run, valid_time, None, None
    )
    dewpoint_field = client.get_field(
        catalog,
        prefixes["height_dewpoint"],
        run,
        valid_time,
        2.0,
        "height",
    )
    pressure_field = client.get_field(
        catalog, prefixes["surface_pressure"], run, valid_time, None, None
    )

    temperature_fields = {
        level: client.get_field(
            catalog,
            prefixes["pressure_temperature"],
            run,
            valid_time,
            float(level),
            "pressure",
        )
        for level in (700, 500)
    }
    geopotential_fields = {
        level: client.get_field(
            catalog,
            prefixes["geopotential"],
            run,
            valid_time,
            float(level),
            "pressure",
        )
        for level in (700, 500)
    }
    shear_field = _compute_shear(
        client, catalog, prefixes, run, valid_time, depth_m=6000
    )

    dewpoint_c = _to_celsius(
        _align(mucape_field, dewpoint_field), dewpoint_field.units
    )
    pressure_hpa = _to_hpa(
        _align(mucape_field, pressure_field), pressure_field.units
    )
    mixing_ratio = _mixing_ratio_from_dewpoint(dewpoint_c, pressure_hpa)
    temperature_700_c = _to_celsius(
        _align(mucape_field, temperature_fields[700]),
        temperature_fields[700].units,
    )
    temperature_500_c = _to_celsius(
        _align(mucape_field, temperature_fields[500]),
        temperature_fields[500].units,
    )
    height_700_m = _height_from_geopotential(
        _align(mucape_field, geopotential_fields[700]),
        geopotential_fields[700].units,
    )
    height_500_m = _height_from_geopotential(
        _align(mucape_field, geopotential_fields[500]),
        geopotential_fields[500].units,
    )
    shear = _align(mucape_field, shear_field)
    ship = _ship_formula(
        mucape_field.data,
        mixing_ratio,
        temperature_700_c,
        temperature_500_c,
        height_700_m,
        height_500_m,
        shear,
    )
    return RasterField(
        ship, mucape_field.transform, mucape_field.crs, mucape_field.bounds, ""
    )


def _catalonia_geometry(geojson: dict):
    geometries = [
        make_valid(shape(feature["geometry"])) for feature in geojson["features"]
    ]
    return unary_union(geometries)


def _mask_to_catalonia(field: RasterField, geometry) -> np.ndarray:
    lon, lat = _grid_lon_lat(field)
    inside = contains_xy(geometry, lon, lat)
    return np.where(inside, field.data, np.nan)

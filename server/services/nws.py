"""
Servicio puro de NWS (api.weather.gov).

Versión "limpia" del cliente legacy (``services/nws.py``): sin
``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **API pública**: solo exige un ``User-Agent`` identificativo
   (recomendación oficial de weather.gov). Sin credenciales.

2. **GeoJSON con unidades por campo**: cada propiedad llega como
   ``{"value": x, "unitCode": "wmoUnit:degC"}``; los conversores
   normalizan a °C / km/h / hPa / mm sea cual sea la unidad.

3. **Una sola fuente**: ``/stations/{sid}/observations`` con ventana
   ``start``/``end`` cubre tanto la observación actual (fila más
   reciente) como la serie del día. ``fetch_current`` añade
   ``/observations/latest`` en paralelo por frescura.

4. **Presiones**: NWS expone ``barometricPressure`` (absoluta) y
   ``seaLevelPressure`` (MSL); si falta una se deriva de la otra con
   la altitud del catálogo. NWS también reporta **dewpoint nativo** —
   la serie canónica lo incluye (único proveedor hasta ahora).

5. **Día local**: se usa la timezone de la estación (campo ``tz`` del
   catálogo; las estaciones NWS están en husos de EE. UU.), con UTC
   como fallback.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from data_files import NWS_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "NWS"
BASE_URL = "https://api.weather.gov"
USER_AGENT = "MeteoLabX/1.0 (contact: meteolabx@gmail.com)"


# =====================================================================
# Conversión de unidades (clonado del legacy)
# =====================================================================

def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _measure_value(raw: Any) -> Tuple[float, str]:
    if isinstance(raw, dict):
        return _safe_float(raw.get("value")), str(raw.get("unitCode", "")).strip()
    return _safe_float(raw), ""


def _unit_norm(unit_code: str) -> str:
    return str(unit_code or "").strip().lower()


def _to_celsius(value: float, unit_code: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit = _unit_norm(unit_code)
    if "degc" in unit:
        return float(value)
    if "degf" in unit:
        return (float(value) - 32.0) * 5.0 / 9.0
    if unit.endswith(":k") or unit.endswith("/k") or unit.endswith("kelvin"):
        return float(value) - 273.15
    return float(value)


def _to_kmh(value: float, unit_code: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit = _unit_norm(unit_code)
    if "m_s-1" in unit or "m/s" in unit:
        return float(value) * 3.6
    if "km_h-1" in unit or "km/h" in unit:
        return float(value)
    if "knot" in unit:
        return float(value) * 1.852
    if "mile_per_hour" in unit or "mph" in unit:
        return float(value) * 1.60934
    return float(value)


def _to_hpa(value: float, unit_code: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit = _unit_norm(unit_code)
    if unit.endswith(":pa") or unit.endswith("/pa") or "pascal" in unit:
        return float(value) / 100.0
    if "hpa" in unit:
        return float(value)
    if "kpa" in unit:
        return float(value) * 10.0
    if "bar" in unit:
        return float(value) * 1000.0
    return float(value)


def _to_mm(value: float, unit_code: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit = _unit_norm(unit_code)
    if unit.endswith(":mm") or unit.endswith("/mm"):
        return float(value)
    if unit.endswith(":m") or unit.endswith("/m"):
        return float(value) * 1000.0
    if unit.endswith(":cm") or unit.endswith("/cm"):
        return float(value) * 10.0
    if "in" in unit:
        return float(value) * 25.4
    return float(value)


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(NWS_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo NWS no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str, str]:
    """→ (lat, lon, elevation, nombre, tz)."""
    station = find_station_by_field(_load_stations(), field="id", target=station_id)
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        _safe_float(station.get("elev")),
        str(station.get("name", "") or "").strip(),
        str(station.get("tz", "") or "").strip(),
    )


def _station_tz(tz_name: str) -> timezone | ZoneInfo:
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone.utc


# =====================================================================
# HTTP
# =====================================================================

async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: float,
) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    try:
        response = await client.get(url, params=params or {}, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"NWS timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error",
            status_code=502,
        ) from exc

    status = response.status_code
    if status == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Station not found (HTTP 404)",
            status_code=404,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="NWS rate limit (HTTP 429)",
            status_code=429,
        )
    if status >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status}",
            status_code=502,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc


# =====================================================================
# Parsing de features GeoJSON
# =====================================================================

def _parse_feature(feature: Dict[str, Any], elevation_m: float) -> Dict[str, float]:
    """Feature GeoJSON → fila normalizada. Vacía si no hay timestamp."""
    if not isinstance(feature, dict):
        return {}
    props = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
    geometry = feature.get("geometry", {}) if isinstance(feature.get("geometry"), dict) else {}
    coords = geometry.get("coordinates", []) if isinstance(geometry.get("coordinates"), list) else []

    epoch = parse_epoch(props.get("timestamp"))
    if epoch is None:
        return {}

    t_v, t_u = _measure_value(props.get("temperature"))
    td_v, td_u = _measure_value(props.get("dewpoint"))
    rh_v, _ = _measure_value(props.get("relativeHumidity"))
    p_abs_v, p_abs_u = _measure_value(props.get("barometricPressure"))
    p_msl_v, p_msl_u = _measure_value(props.get("seaLevelPressure"))
    wind_v, wind_u = _measure_value(props.get("windSpeed"))
    gust_v, gust_u = _measure_value(props.get("windGust"))
    dir_v, _ = _measure_value(props.get("windDirection"))
    rain_v, rain_u = _measure_value(props.get("precipitationLastHour"))
    heat_v, heat_u = _measure_value(props.get("heatIndex"))
    chill_v, chill_u = _measure_value(props.get("windChill"))

    p_abs = _to_hpa(p_abs_v, p_abs_u)
    p_msl = _to_hpa(p_msl_v, p_msl_u)
    if _is_nan(p_abs) and not _is_nan(p_msl) and not _is_nan(elevation_m):
        p_abs = float(p_msl) / math.exp(float(elevation_m) / 8000.0)
    if _is_nan(p_msl) and not _is_nan(p_abs) and not _is_nan(elevation_m):
        p_msl = float(p_abs) * math.exp(float(elevation_m) / 8000.0)

    rain_mm = _to_mm(rain_v, rain_u)

    return {
        "epoch": int(epoch),
        "lat": _safe_float(coords[1]) if len(coords) >= 2 else float("nan"),
        "lon": _safe_float(coords[0]) if len(coords) >= 2 else float("nan"),
        "temp_c": _to_celsius(t_v, t_u),
        "dewpoint_c": _to_celsius(td_v, td_u),
        "rh": _safe_float(rh_v),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _to_kmh(wind_v, wind_u),
        "gust_kmh": _to_kmh(gust_v, gust_u),
        "wind_dir_deg": _safe_float(dir_v),
        "precip_last_mm": max(0.0, rain_mm) if not _is_nan(rain_mm) else float("nan"),
        "heat_index_c": _to_celsius(heat_v, heat_u),
        "wind_chill_c": _to_celsius(chill_v, chill_u),
    }


async def _fetch_observation_rows(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
    elevation_m: float,
    timeout_s: float,
    limit: int = 500,
) -> List[Dict[str, float]]:
    """Observaciones en la ventana → filas ordenadas por epoch asc."""
    payload = await _get_json(
        client,
        f"{BASE_URL}/stations/{quote(station_id)}/observations",
        {"start": _to_iso_z(start), "end": _to_iso_z(end), "limit": limit},
        timeout_s=timeout_s,
    )
    features = payload.get("features", []) if isinstance(payload, dict) else []
    rows: Dict[int, Dict[str, float]] = {}
    for feature in features if isinstance(features, list) else []:
        parsed = _parse_feature(feature, elevation_m)
        if parsed:
            rows[parsed["epoch"]] = parsed
    return [rows[ep] for ep in sorted(rows)]


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual: ``/observations/latest`` (preferente) + la
    ventana del día local (precipitación acumulada y fallback campo a
    campo). Devuelve el dict canónico + ``station_code``/``station_name``.
    """
    station_id = str(station_id).strip().upper()
    lat0, lon0, elevation, name, tz_name = _station_meta(station_id)

    tz = _station_tz(tz_name)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        latest_task = _get_json(
            client,
            f"{BASE_URL}/stations/{quote(station_id)}/observations/latest",
            timeout_s=timeout_s,
        )
        rows_task = _fetch_observation_rows(
            station_id, client,
            start=day_start, end=now_local,
            elevation_m=elevation, timeout_s=timeout_s,
        )
        latest_result, rows_result = await asyncio.gather(
            latest_task, rows_task, return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    if isinstance(latest_result, BaseException) and isinstance(rows_result, BaseException):
        raise latest_result if isinstance(latest_result, ProviderError) else rows_result

    latest_row: Dict[str, float] = {}
    if not isinstance(latest_result, BaseException):
        latest_row = _parse_feature(latest_result, elevation)
    rows = [] if isinstance(rows_result, BaseException) else rows_result

    if not latest_row and not rows:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"NWS sin observaciones para {station_id}",
            status_code=502,
        )

    # current = latest si está; si no, la fila más reciente del día.
    current = latest_row or rows[-1]

    def _value(key: str) -> float:
        value = _safe_float(current.get(key))
        if not _is_nan(value):
            return value
        for row in reversed(rows):
            row_value = _safe_float(row.get(key))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    # Precipitación del día: suma de precipitationLastHour de las filas.
    precip_vals = [
        max(0.0, _safe_float(row.get("precip_last_mm")))
        for row in rows
        if not _is_nan(_safe_float(row.get("precip_last_mm")))
    ]
    precip_total = float(sum(precip_vals)) if precip_vals else _value("precip_last_mm")

    epoch = int(current.get("epoch") or 0) or int(datetime.now(tz=timezone.utc).timestamp())
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    lat = _safe_float(current.get("lat"))
    lon = _safe_float(current.get("lon"))

    observation: Dict[str, Any] = {
        "Tc": _value("temp_c"),
        "RH": _value("rh"),
        "p_hpa": _value("p_msl_hpa"),
        "p_abs_hpa": _value("p_abs_hpa"),
        "wind": _value("wind_kmh"),
        "gust": _value("gust_kmh"),
        "wind_dir_deg": _value("wind_dir_deg"),
        # NWS reporta dewpoint/heat index/wind chill nativos; el
        # pipeline (add_basic_derived) solo rellena los que falten.
        "Td": _value("dewpoint_c"),
        "feels_like": _value("heat_index_c"),
        "heat_index": _value("heat_index_c"),
        "wind_chill": _value("wind_chill_c"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": float("nan"),  # NWS no expone radiación
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat if not _is_nan(lat) else lat0,
        "lon": lon if not _is_nan(lon) else lon0,
        "elevation": elevation,
        "station_name": name or station_id,
    }

    # add_basic_derived calcula Td/feels_like/heat_index, pero NWS los
    # reporta nativos; preservamos los del proveedor cuando existen
    # (mismo criterio que el legacy get_nws_data).
    native = {
        key: observation[key]
        for key in ("Td", "feels_like", "heat_index", "wind_chill")
        if not _is_nan(_safe_float(observation[key]))
    }
    from domain.observation_pipeline import add_basic_derived
    derived = add_basic_derived(observation)
    derived.update(native)
    return derived


def _empty_today_series() -> Dict[str, Any]:
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "lat": float("nan"),
        "lon": float("nan"),
        "has_data": False,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (temperatura/humedad/presión MSL) para tendencias.
    La ventana se trocea en días (la paginación de weather.gov limita
    ~500 features por respuesta) y se binnea a 1 punto/hora.
    """
    station_id = str(station_id).strip().upper()
    lat0, lon0, elevation, _name, tz_name = _station_meta(station_id)
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    days = max(1, int(days_back))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        async def _day(idx: int) -> List[Dict[str, float]]:
            end = now_utc - timedelta(days=idx)
            start = end - timedelta(days=1)
            try:
                return await _fetch_observation_rows(
                    station_id, client,
                    start=start, end=end,
                    elevation_m=elevation, timeout_s=timeout_s,
                )
            except ProviderError as exc:
                if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                    raise
                return []

        chunks = await asyncio.gather(*(_day(idx) for idx in range(days)))
    finally:
        if owns_client:
            await client.aclose()

    # Bin horario: nos quedamos con la última fila de cada hora.
    by_hour: Dict[int, Dict[str, float]] = {}
    for chunk in chunks:
        for row in chunk:
            bucket = (int(row["epoch"]) // 3600) * 3600
            current = by_hour.get(bucket)
            if current is None or row["epoch"] >= current["epoch"]:
                by_hour[bucket] = row

    epochs = sorted(by_hour)
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }
    return {
        "epochs": epochs,
        "temps": [_safe_float(by_hour[ep].get("temp_c")) for ep in epochs],
        "humidities": [_safe_float(by_hour[ep].get("rh")) for ep in epochs],
        "pressures": [_safe_float(by_hour[ep].get("p_msl_hpa")) for ep in epochs],
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie del día local (tz de la estación) en shape canónico.
    Incluye ``dewpts`` nativos (NWS los reporta).
    """
    station_id = str(station_id).strip().upper()
    lat0, lon0, elevation, _name, tz_name = _station_meta(station_id)

    tz = _station_tz(tz_name)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_observation_rows(
            station_id, client,
            start=day_start, end=now_local,
            elevation_m=elevation, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        return _empty_today_series()

    def _col(key: str) -> List[float]:
        return [_safe_float(row.get(key)) for row in rows]

    lats = [v for v in _col("lat") if not _is_nan(v)]
    lons = [v for v in _col("lon") if not _is_nan(v)]

    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": _col("temp_c"),
        "humidities": _col("rh"),
        "dewpts": _col("dewpoint_c"),
        "pressures": _col("p_msl_hpa"),
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": [float("nan")] * len(rows),
        "winds": _col("wind_kmh"),
        "gusts": _col("gust_kmh"),
        "wind_dirs": _col("wind_dir_deg"),
        "lat": lats[0] if lats else lat0,
        "lon": lons[0] if lons else lon0,
        "has_data": True,
    }

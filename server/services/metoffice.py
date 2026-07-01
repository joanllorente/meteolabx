"""
Servicio puro de Met Office (Weather DataHub, observation-land).

Versión "limpia" del cliente legacy (``services/metoffice.py``): sin
``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **Auth**: API key del servidor (``METEOLABX_METOFFICE_API_KEY``) en
   el header ``apikey``.

2. **Una sola petición**: ``/observation-land/1/{geohash}`` devuelve
   las últimas ~24 h de observaciones de la celda. ``fetch_current``
   y ``fetch_today_series`` salen del mismo payload.

3. **station_id = geohash en minúsculas**: el schema de la API
   normaliza ``station_id`` a mayúsculas, así que el servicio lo
   vuelve a minúsculas antes de llamar al DataHub y de buscar en el
   catálogo local.

4. **Solo MSL**: el feed trae ``mslp``; la absoluta se deriva con la
   altitud del catálogo. Viento en m/s; dirección a veces cardinal
   (``"SSW"``) → grados. Sin precipitación/radiación/UV.

5. **Día local**: timezone del catálogo (``tz``/``olson_time_zone``),
   con ``Europe/London`` como fallback.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from data_files import METOFFICE_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "METOFFICE"
BASE_URL = "https://data.hub.api.metoffice.gov.uk"
DEFAULT_TZ = "Europe/London"

_DIRECTION_DEGREES = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


# =====================================================================
# Helpers
# =====================================================================

def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ms_to_kmh(value: Any) -> float:
    raw = _safe_float(value)
    return raw * 3.6 if not _is_nan(raw) else float("nan")


def _wind_direction_degrees(value: Any) -> float:
    raw = str(value or "").strip().upper()
    if not raw:
        return float("nan")
    if raw in _DIRECTION_DEGREES:
        return _DIRECTION_DEGREES[raw]
    return _safe_float(raw)


# =====================================================================
# Catálogo local (lookup por geohash/id/source_id, case-insensitive)
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(METOFFICE_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo Met Office no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str, str]:
    """→ (lat, lon, elevation, nombre, tz)."""
    target = str(station_id or "").strip().lower()
    station: Dict[str, Any] = {}
    for item in _load_stations():
        if not isinstance(item, dict):
            continue
        for field in ("geohash", "id", "source_id"):
            if str(item.get(field, "") or "").strip().lower() == target:
                station = item
                break
        if station:
            break

    name = str(
        station.get("display_name")
        or station.get("station_name")
        or station.get("metoffice_station_name")
        or station.get("name")
        or station.get("area")
        or ""
    ).strip()
    tz_name = str(station.get("tz") or station.get("olson_time_zone") or DEFAULT_TZ).strip()
    elevation = _safe_float(station.get("elev", station.get("altitude")))
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        elevation,
        name,
        tz_name,
    )


def _station_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


# =====================================================================
# HTTP + parsing
# =====================================================================

def _require_api_key(api_key: str) -> None:
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing METOFFICE_API_KEY",
            status_code=401,
        )


async def _fetch_observations(
    station_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> List[Dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        "apikey": api_key,
    }
    url = f"{BASE_URL}/observation-land/1/{quote(station_id)}"
    try:
        response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Met Office timeout: {exc}",
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
    if status in (401, 403):
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail=f"Met Office auth rechazada (HTTP {status})",
            status_code=401,
        )
    if status == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Geohash not found (HTTP 404)",
            status_code=404,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Met Office rate limit (HTTP 429)",
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
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "observations", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
    return []


def _parse_observation(item: Dict[str, Any], elevation_m: float) -> Dict[str, float]:
    epoch = parse_epoch(item.get("datetime"))
    if epoch is None:
        return {}

    p_msl = _safe_float(item.get("mslp"))
    p_abs = (
        p_msl / math.exp(float(elevation_m) / 8000.0)
        if not _is_nan(p_msl) and not _is_nan(elevation_m)
        else float("nan")
    )

    return {
        "epoch": int(epoch),
        "temp_c": _safe_float(item.get("temperature")),
        "rh": _safe_float(item.get("humidity")),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _ms_to_kmh(item.get("wind_speed")),
        "gust_kmh": _ms_to_kmh(item.get("wind_gust")),
        "wind_dir_deg": _wind_direction_degrees(item.get("wind_direction")),
    }


def _rows_from_observations(
    observations: List[Dict[str, Any]],
    elevation_m: float,
) -> List[Dict[str, float]]:
    rows: Dict[int, Dict[str, float]] = {}
    for item in observations:
        parsed = _parse_observation(item, elevation_m)
        if parsed:
            rows[parsed["epoch"]] = parsed
    return [rows[ep] for ep in sorted(rows)]


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
) -> Dict[str, Any]:
    """
    Observación actual = fila más reciente del feed de la celda.
    Met Office no expone precipitación/radiación/UV en este producto.
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip().lower()
    lat, lon, elevation, name, tz_name = _station_meta(station_id)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        observations = await _fetch_observations(
            station_id, api_key, client, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = _rows_from_observations(observations, elevation)
    if not rows:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"Met Office sin observaciones para {station_id}",
            status_code=502,
        )

    current = rows[-1]

    def _value(key: str) -> float:
        value = _safe_float(current.get(key))
        if not _is_nan(value):
            return value
        for row in reversed(rows[:-1]):
            row_value = _safe_float(row.get(key))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    epoch = int(current["epoch"])
    from datetime import timezone as _tz
    dt_utc = datetime.fromtimestamp(epoch, tz=_tz.utc)
    tz = _station_tz(tz_name)

    observation: Dict[str, Any] = {
        "Tc": _value("temp_c"),
        "RH": _value("rh"),
        "p_hpa": _value("p_msl_hpa"),
        "p_abs_hpa": _value("p_abs_hpa"),
        "wind": _value("wind_kmh"),
        "gust": _value("gust_kmh"),
        "wind_dir_deg": _value("wind_dir_deg"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "station_name": name or station_id,
    }

    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


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


async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local (tz de la estación) en shape canónico."""
    _require_api_key(api_key)
    station_id = str(station_id).strip().lower()
    lat, lon, elevation, _name, tz_name = _station_meta(station_id)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        observations = await _fetch_observations(
            station_id, api_key, client, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    tz = _station_tz(tz_name)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_epoch = int(day_start.timestamp())
    end_epoch = int((day_start + timedelta(days=1)).timestamp())

    rows = [
        row for row in _rows_from_observations(observations, elevation)
        if start_epoch <= row["epoch"] < end_epoch
    ]
    if not rows:
        return _empty_today_series()

    def _col(key: str) -> List[float]:
        return [_safe_float(row.get(key)) for row in rows]

    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": _col("temp_c"),
        "humidities": _col("rh"),
        "dewpts": [float("nan")] * len(rows),
        "pressures": _col("p_msl_hpa"),
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": [float("nan")] * len(rows),
        "winds": _col("wind_kmh"),
        "gusts": _col("gust_kmh"),
        "wind_dirs": _col("wind_dir_deg"),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    api_key: str,
    *,
    days_back: int = 1,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente para el LOOKBACK de ``/series/today``.

    Met Office DataHub solo expone un feed rodante de ~24 h, sin endpoint
    sinóptico. Reutilizamos la serie del DÍA ANTERIOR filtrando ese mismo
    feed: aporta la cola de ayer (resolución horaria nativa) para sembrar la
    tendencia de presión 3h a las 00:00 local. Limitación: pasada la media
    tarde la cola de ayer cae fuera del feed de 24 h; como el lookback solo
    importa al inicio del día, el impacto es menor. Solo se usa con
    ``days_back=1``.
    """
    _, _, _, _, tz_name = _station_meta(str(station_id).strip().lower())
    tz = _station_tz(tz_name)
    yesterday = (now or datetime.now(tz=tz)).astimezone(tz) - timedelta(days=1)
    return await fetch_today_series(
        station_id, api_key, client=client, timeout_s=timeout_s, now=yesterday,
    )

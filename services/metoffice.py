"""
Servicio para integrar observaciones de Met Office Weather DataHub.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from data_files import METOFFICE_STATIONS_PATH
from services._common import load_stations_json, parse_epoch as _parse_epoch
from utils.provider_state import (
    clear_provider_runtime_error,
    get_connected_provider_station_id,
    is_provider_connection,
    resolve_state,
    set_provider_runtime_error,
)


METOFFICE_BASE_URL = os.getenv("METOFFICE_BASE_URL", "https://data.hub.api.metoffice.gov.uk").rstrip("/")
METOFFICE_TIMEOUT_SECONDS = int(os.getenv("METOFFICE_TIMEOUT_SECONDS", "16"))
METOFFICE_STATION_TZ = "Europe/London"


def _get_setting(env_key: str, default: str = "") -> str:
    try:
        secret_val = st.secrets.get(env_key, "")
        if secret_val not in (None, ""):
            return str(secret_val).strip()
    except Exception:
        pass
    return str(os.getenv(env_key, default)).strip()


METOFFICE_API_KEY = _get_setting("METOFFICE_API_KEY")

_DIRECTION_DEGREES = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
}


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


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


def _request_headers(api_key: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        "apikey": str(api_key or "").strip(),
    }


def _request_json(path: str, api_key: str) -> Any:
    response = requests.get(
        f"{METOFFICE_BASE_URL}/{path.lstrip('/')}",
        headers=_request_headers(api_key),
        timeout=METOFFICE_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        snippet = str(response.text or "").strip().replace("\n", " ")
        if len(snippet) > 220:
            snippet = snippet[:220] + "..."
        detail = f"{response.status_code} {response.reason}"
        if snippet:
            detail += f" | body: {snippet}"
        raise requests.HTTPError(detail, response=response)
    response.raise_for_status()
    return response.json()


def _observations_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "observations", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_metoffice_observations(geohash: str, api_key: str = METOFFICE_API_KEY) -> Dict[str, Any]:
    station_id = str(geohash or "").strip().lower()
    key = str(api_key or METOFFICE_API_KEY or "").strip()
    if not station_id:
        return {"ok": False, "error": "geohash vacio", "observations": []}
    if not key:
        return {"ok": False, "error": "Falta METOFFICE_API_KEY.", "observations": []}

    try:
        payload = _request_json(f"/observation-land/1/{quote(station_id)}", key)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "observations": []}

    observations = _observations_from_payload(payload)
    return {"ok": len(observations) > 0, "error": "" if observations else "Serie vacia", "observations": observations}


def _stations_mtime_ns(path: str = str(METOFFICE_STATIONS_PATH)) -> int:
    try:
        return Path(path).stat().st_mtime_ns
    except Exception:
        return 0


@lru_cache(maxsize=4)
def _load_stations(path: str = str(METOFFICE_STATIONS_PATH), mtime_ns: int = 0) -> List[Dict[str, Any]]:
    return load_stations_json(path)


def _find_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip().lower()
    if not target:
        return {}
    for station in _load_stations(str(METOFFICE_STATIONS_PATH), _stations_mtime_ns()):
        if not isinstance(station, dict):
            continue
        for field in ("geohash", "id", "source_id"):
            if str(station.get(field, "") or "").strip().lower() == target:
                return station
    return {}


def _parse_observation(item: Dict[str, Any], elevation_m: float, lat: float, lon: float) -> Dict[str, float]:
    epoch = _parse_epoch(item.get("datetime"))
    if epoch is None:
        return {}

    p_msl = _safe_float(item.get("mslp"))
    p_abs = p_msl / math.exp(float(elevation_m) / 8000.0) if not _is_nan(p_msl) else float("nan")

    return {
        "epoch": int(epoch),
        "lat": lat,
        "lon": lon,
        "temp_c": _safe_float(item.get("temperature")),
        "rh": _safe_float(item.get("humidity")),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _ms_to_kmh(item.get("wind_speed")),
        "gust_kmh": _ms_to_kmh(item.get("wind_gust")),
        "wind_dir_deg": _wind_direction_degrees(item.get("wind_direction")),
        "weather_code": _safe_float(item.get("weather_code")),
        "visibility_m": _safe_float(item.get("visibility")),
    }


def _series_from_observations(observations: List[Dict[str, Any]], elevation_m: float, lat: float, lon: float) -> Dict[str, Any]:
    rows: Dict[int, Dict[str, float]] = {}
    for item in observations:
        parsed = _parse_observation(item, elevation_m=elevation_m, lat=lat, lon=lon)
        if parsed:
            rows[int(parsed["epoch"])] = parsed

    epochs = sorted(rows.keys())
    temps: List[float] = []
    rhs: List[float] = []
    p_abs: List[float] = []
    p_msl: List[float] = []
    winds: List[float] = []
    gusts: List[float] = []
    dirs: List[float] = []
    visibility: List[float] = []
    weather_codes: List[float] = []

    for ep in epochs:
        row = rows[ep]
        temps.append(float(row.get("temp_c", float("nan"))))
        rhs.append(float(row.get("rh", float("nan"))))
        p_abs.append(float(row.get("p_abs_hpa", float("nan"))))
        p_msl.append(float(row.get("p_msl_hpa", float("nan"))))
        winds.append(float(row.get("wind_kmh", float("nan"))))
        gusts.append(float(row.get("gust_kmh", float("nan"))))
        dirs.append(float(row.get("wind_dir_deg", float("nan"))))
        visibility.append(float(row.get("visibility_m", float("nan"))))
        weather_codes.append(float(row.get("weather_code", float("nan"))))

    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": temps,
        "humidities": rhs,
        "pressures_abs": p_abs,
        "pressures_msl": p_msl,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "precips": [float("nan")] * len(epochs),
        "solar_radiations": [float("nan")] * len(epochs),
        "uv_indexes": [float("nan")] * len(epochs),
        "visibility_m": visibility,
        "weather_codes": weather_codes,
        "has_data": len(epochs) > 0,
    }


def _last_valid(values: List[float]) -> float:
    for value in reversed(values):
        v = _safe_float(value)
        if not _is_nan(v):
            return v
    return float("nan")


def _max_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return max(clean) if clean else float("nan")


def _min_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return min(clean) if clean else float("nan")


def _station_day_window_epoch(tz_name: str) -> Tuple[int, int]:
    try:
        tz = ZoneInfo(str(tz_name or METOFFICE_STATION_TZ))
    except Exception:
        tz = ZoneInfo(METOFFICE_STATION_TZ)
    now_local = datetime.now(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return int(day_start.timestamp()), int(day_end.timestamp())


def _today_values(values: List[float], epochs: List[int], day_start_epoch: int, day_end_epoch: int) -> List[float]:
    out: List[float] = []
    for ep, value in zip(epochs, values):
        epoch_i = int(ep)
        if epoch_i < day_start_epoch or epoch_i >= day_end_epoch:
            continue
        v = _safe_float(value)
        if not _is_nan(v):
            out.append(v)
    return out


def _pressure_3h_reference(epochs: List[int], pressures_msl: List[float]) -> Tuple[float, Optional[int], Optional[int]]:
    valid = [
        (int(ep), float(p))
        for ep, p in zip(epochs, pressures_msl)
        if not _is_nan(_safe_float(p))
    ]
    if len(valid) < 2:
        return float("nan"), None, None
    valid.sort(key=lambda item: item[0])
    ep_now, _p_now = valid[-1]
    target_ep = ep_now - (3 * 3600)
    ep_old, p_old = min(valid, key=lambda item: abs(item[0] - target_ep))
    return p_old, ep_old, ep_now


def is_metoffice_connection() -> bool:
    return is_provider_connection("METOFFICE", st.session_state)


def get_metoffice_data(state=None) -> Optional[Dict[str, Any]]:
    state = resolve_state(state)
    if not is_provider_connection("METOFFICE", state):
        return None

    station_id = get_connected_provider_station_id("METOFFICE", state)
    if not station_id:
        set_provider_runtime_error("METOFFICE", "Falta geohash de Met Office.", state)
        return None

    api_key = str(METOFFICE_API_KEY or "").strip()
    if not api_key:
        set_provider_runtime_error("METOFFICE", "Falta METOFFICE_API_KEY.", state)
        return None

    station_meta = _find_station(station_id)
    station_name = str(
        station_meta.get("display_name")
        or station_meta.get("station_name")
        or station_meta.get("metoffice_station_name")
        or station_meta.get("name")
        or station_meta.get("area")
        or station_id
    ).strip()
    station_tz = str(station_meta.get("tz") or station_meta.get("olson_time_zone") or METOFFICE_STATION_TZ).strip()
    station_lat = _safe_float(station_meta.get("lat"), default=_safe_float(getattr(state, "get", lambda *_: None)("provider_station_lat")))
    station_lon = _safe_float(station_meta.get("lon"), default=_safe_float(getattr(state, "get", lambda *_: None)("provider_station_lon")))
    elevation = _safe_float(station_meta.get("elev", station_meta.get("altitude", 0.0)), default=0.0)
    if _is_nan(elevation):
        elevation = 0.0

    payload = fetch_metoffice_observations(station_id, api_key=api_key)
    if not isinstance(payload, dict) or not payload.get("ok"):
        set_provider_runtime_error("METOFFICE", str((payload or {}).get("error", "Met Office no devolvió datos.")), state)
        return None

    observations = payload.get("observations", [])
    observations = observations if isinstance(observations, list) else []
    series = _series_from_observations(observations, elevation_m=elevation, lat=station_lat, lon=station_lon)
    if not series.get("has_data"):
        set_provider_runtime_error("METOFFICE", "Serie vacía para la estación.", state)
        return None

    idx = len(series["epochs"]) - 1
    epochs = series.get("epochs", [])
    temps = series.get("temps", [])
    rhs = series.get("humidities", [])
    p_abs_series = series.get("pressures_abs", [])
    p_msl_series = series.get("pressures_msl", [])
    winds = series.get("winds", [])
    gusts = series.get("gusts", [])
    dirs = series.get("wind_dirs", [])
    weather_codes = series.get("weather_codes", [])
    visibility = series.get("visibility_m", [])

    day_start, day_end = _station_day_window_epoch(station_tz)
    temp_today = _today_values(temps, epochs, day_start, day_end)
    rh_today = _today_values(rhs, epochs, day_start, day_end)
    gust_today = _today_values(gusts, epochs, day_start, day_end)
    pressure_3h_ago, epoch_3h_ago, epoch_now_ref = _pressure_3h_reference(epochs, p_msl_series)

    base_epoch = int(epochs[idx])
    if epoch_now_ref is not None:
        base_epoch = int(epoch_now_ref)

    clear_provider_runtime_error("METOFFICE", state)
    return {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_tz": station_tz,
        "lat": station_lat,
        "lon": station_lon,
        "elevation": float(elevation),
        "epoch": int(base_epoch),
        "Tc": _safe_float(temps[idx]) if idx < len(temps) else _last_valid(temps),
        "RH": _safe_float(rhs[idx]) if idx < len(rhs) else _last_valid(rhs),
        "Td": float("nan"),
        "p_hpa": _safe_float(p_msl_series[idx]) if idx < len(p_msl_series) else _last_valid(p_msl_series),
        "p_abs_hpa": _safe_float(p_abs_series[idx]) if idx < len(p_abs_series) else _last_valid(p_abs_series),
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": _safe_float(winds[idx]) if idx < len(winds) else _last_valid(winds),
        "gust": _safe_float(gusts[idx]) if idx < len(gusts) else _last_valid(gusts),
        "wind_dir_deg": _safe_float(dirs[idx]) if idx < len(dirs) else _last_valid(dirs),
        "precip_total": float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "temp_max": _max_valid(temp_today),
        "temp_min": _min_valid(temp_today),
        "rh_max": _max_valid(rh_today),
        "rh_min": _min_valid(rh_today),
        "gust_max": _max_valid(gust_today),
        "weather_code": _safe_float(weather_codes[idx]) if idx < len(weather_codes) else _last_valid(weather_codes),
        "visibility_m": _safe_float(visibility[idx]) if idx < len(visibility) else _last_valid(visibility),
        "_series": {
            "epochs": [int(ep) for ep in epochs],
            "temps": [float(v) for v in temps],
            "humidities": [float(v) for v in rhs],
            "pressures_abs": [float(v) for v in p_abs_series],
            "winds": [float(v) for v in winds],
            "gusts": [float(v) for v in gusts],
            "wind_dirs": [float(v) for v in dirs],
            "precips": [float("nan")] * len(epochs),
            "solar_radiations": [float("nan")] * len(epochs),
            "uv_indexes": [float("nan")] * len(epochs),
            "has_data": bool(series.get("has_data")),
        },
        "_series_7d": {
            "epochs": [int(ep) for ep in epochs],
            "temps": [float(v) for v in temps],
            "humidities": [float(v) for v in rhs],
            "pressures_abs": [float(v) for v in p_abs_series],
            "has_data": bool(series.get("has_data")),
        },
    }

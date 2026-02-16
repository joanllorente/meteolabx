"""
Servicio para interactuar con Meteocat (XEMA).
"""
import json
import math
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import streamlit as st


METEOCAT_API_KEY = os.getenv(
    "METEOCAT_API_KEY",
    "rZwBPl5kv05CS7NEgk9wcaqd0FFimA2f9y6ISDa2",
)

BASE_URL = "https://api.meteo.cat/xema/v1"
TIMEOUT_SECONDS = 14
CAT_TZ = ZoneInfo("Europe/Madrid")


# Variables de interés.
V_TEMP = 32
V_RH = 33
V_PRESSURE = 34
V_PRECIP = 35
V_SOLAR = 36
V_UV = 39
V_WIND = 30
V_WIND_DIR = 31
V_GUST = 50
V_GUST_DIR = 51
V_TEMP_MAX_DAY = 12
V_TEMP_MIN_DAY = 13
V_RH_MAX_DAY = 3
V_RH_MIN_DAY = 44
V_RAIN_1MIN_MAX = 72
V_PRECIP_ACC = 70

METEOCAT_LATEST_VARIABLES = {
    "temp": [V_TEMP],
    "rh": [V_RH],
    "pressure_abs": [V_PRESSURE],
    "precip_total": [V_PRECIP_ACC, V_PRECIP],
    "solar": [V_SOLAR],
    "uv": [V_UV],
    "wind": [V_WIND, 20],
    "wind_dir": [V_WIND_DIR, 21],
    "gust": [V_GUST],
    "gust_dir": [V_GUST_DIR],
}


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _parse_iso_epoch(value: Any) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _ms_to_kmh(value: float) -> float:
    return float("nan") if _is_nan(value) else value * 3.6


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_abs_hpa):
        return float("nan")
    try:
        return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)
    except Exception:
        return float("nan")


@lru_cache(maxsize=2)
def _load_stations(path: str = "data_estaciones_meteocat.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _find_station(station_code: str) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    for station in _load_stations():
        if str(station.get("codi", "")).strip().upper() == code:
            return station
    return {}


def _request_json(url: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Any:
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }
    response = requests.get(url, params=params or {}, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _request_latest_variable(station_code: str, variable_code: int, api_key: str) -> Tuple[float, Optional[int], Optional[str]]:
    endpoint = f"{BASE_URL}/variables/mesurades/{int(variable_code)}/ultimes"
    payload = _request_json(endpoint, api_key, params={"codiEstacio": station_code})
    if not isinstance(payload, dict):
        return float("nan"), None, None

    readings = payload.get("lectures", [])
    if not isinstance(readings, list) or not readings:
        return float("nan"), None, None

    best_value = float("nan")
    best_epoch = None
    best_ts = None
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        epoch = _parse_iso_epoch(reading.get("data"))
        if epoch is None:
            continue
        if best_epoch is None or epoch > best_epoch:
            best_epoch = epoch
            best_value = _safe_float(reading.get("valor"))
            best_ts = str(reading.get("data", "")).strip() or None
    return best_value, best_epoch, best_ts


@st.cache_data(ttl=600)
def fetch_meteocat_station_snapshot(station_code: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    if not code or not key:
        return {"ok": False, "error": "Falta station_code o API key"}

    values: Dict[str, float] = {}
    epochs: Dict[str, int] = {}
    iso_times: Dict[str, str] = {}

    for target_name, candidates in METEOCAT_LATEST_VARIABLES.items():
        values[target_name] = float("nan")
        for var_code in candidates:
            try:
                value, epoch, ts_iso = _request_latest_variable(code, int(var_code), key)
            except Exception:
                continue
            if not _is_nan(value):
                values[target_name] = float(value)
                if epoch is not None:
                    epochs[target_name] = int(epoch)
                if ts_iso:
                    iso_times[target_name] = ts_iso
                break

    all_epochs = list(epochs.values())
    latest_epoch = max(all_epochs) if all_epochs else int(time.time())
    latest_iso = None
    if epochs:
        latest_key = max(epochs, key=lambda k: epochs[k])
        latest_iso = iso_times.get(latest_key)

    return {
        "ok": True,
        "station_code": code,
        "values": values,
        "epochs": epochs,
        "latest_epoch": latest_epoch,
        "latest_iso": latest_iso,
    }


def _local_day_parts(day_local: Optional[datetime]) -> Tuple[int, int, int]:
    day = day_local.astimezone(CAT_TZ) if day_local else datetime.now(CAT_TZ)
    return day.year, day.month, day.day


@st.cache_data(ttl=600)
def fetch_meteocat_station_day(station_code: str, year: int, month: int, day: int, api_key: Optional[str] = None) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    if not code or not key:
        return {"ok": False, "error": "Falta station_code o API key", "variables": {}}

    endpoint = f"{BASE_URL}/estacions/mesurades/{code}/{int(year):04d}/{int(month):02d}/{int(day):02d}"
    try:
        payload = _request_json(endpoint, key)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "variables": {}}

    station_block = None
    if isinstance(payload, list) and payload:
        station_block = payload[0]
    elif isinstance(payload, dict):
        station_block = payload
    else:
        station_block = {}

    variables_map: Dict[int, List[Tuple[int, float]]] = {}
    variables = station_block.get("variables", []) if isinstance(station_block, dict) else []
    for variable in variables if isinstance(variables, list) else []:
        if not isinstance(variable, dict):
            continue
        code_var = variable.get("codi")
        try:
            code_var = int(code_var)
        except Exception:
            continue
        readings = variable.get("lectures", [])
        out: List[Tuple[int, float]] = []
        for reading in readings if isinstance(readings, list) else []:
            if not isinstance(reading, dict):
                continue
            epoch = _parse_iso_epoch(reading.get("data"))
            if epoch is None:
                continue
            value = _safe_float(reading.get("valor"))
            out.append((epoch, value))
        out.sort(key=lambda t: t[0])
        variables_map[code_var] = out

    return {
        "ok": True,
        "station_code": code,
        "year": int(year),
        "month": int(month),
        "day": int(day),
        "variables": variables_map,
    }


def _series_from_map(var_map: Dict[int, List[Tuple[int, float]]], code: int) -> List[Tuple[int, float]]:
    return list(var_map.get(int(code), []))


def _max_of_series(series: List[Tuple[int, float]]) -> float:
    vals = [v for _, v in series if not _is_nan(v)]
    return max(vals) if vals else float("nan")


def _min_of_series(series: List[Tuple[int, float]]) -> float:
    vals = [v for _, v in series if not _is_nan(v)]
    return min(vals) if vals else float("nan")


def _sum_series(series: List[Tuple[int, float]]) -> float:
    vals = [max(0.0, v) for _, v in series if not _is_nan(v)]
    return float(sum(vals)) if vals else float("nan")


def _precip_today_mm(var_map: Dict[int, List[Tuple[int, float]]]) -> float:
    # Preferir acumulada si existe.
    s_acc = _series_from_map(var_map, V_PRECIP_ACC)
    if s_acc:
        vals = [v for _, v in s_acc if not _is_nan(v)]
        if vals:
            return max(vals)

    # Fallback: precipitación por intervalo (sumar).
    s = _series_from_map(var_map, V_PRECIP)
    return _sum_series(s)


def _join_by_epoch(*series: List[Tuple[int, float]]) -> Dict[int, List[float]]:
    joined: Dict[int, List[float]] = {}
    for idx, ser in enumerate(series):
        for ep, val in ser:
            if ep not in joined:
                joined[ep] = [float("nan")] * len(series)
            joined[ep][idx] = val
    return joined


def extract_meteocat_daily_timeseries(var_map: Dict[int, List[Tuple[int, float]]]) -> Dict[str, List[float]]:
    s_temp = _series_from_map(var_map, V_TEMP)
    s_rh = _series_from_map(var_map, V_RH)
    s_p_abs = _series_from_map(var_map, V_PRESSURE)
    s_wind = _series_from_map(var_map, V_WIND)
    s_gust = _series_from_map(var_map, V_GUST)
    s_dir = _series_from_map(var_map, V_WIND_DIR)
    s_solar = _series_from_map(var_map, V_SOLAR)

    joined = _join_by_epoch(s_temp, s_rh, s_p_abs, s_wind, s_gust, s_dir, s_solar)
    epochs = sorted(joined.keys())

    temps = []
    humidities = []
    pressures_abs = []
    winds = []
    gusts = []
    dirs = []
    solar = []
    for ep in epochs:
        row = joined[ep]
        temps.append(row[0] if len(row) > 0 else float("nan"))
        humidities.append(row[1] if len(row) > 1 else float("nan"))
        pressures_abs.append(row[2] if len(row) > 2 else float("nan"))
        winds.append(_ms_to_kmh(row[3]) if len(row) > 3 else float("nan"))
        gusts.append(_ms_to_kmh(row[4]) if len(row) > 4 else float("nan"))
        dirs.append(row[5] if len(row) > 5 else float("nan"))
        solar.append(row[6] if len(row) > 6 else float("nan"))

    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": humidities,
        "pressures_abs": pressures_abs,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "solar_radiations": solar,
        "has_data": len(epochs) > 0,
    }


def is_meteocat_connection() -> bool:
    return st.session_state.get("connection_type") == "METEOCAT"


def get_meteocat_data(api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not is_meteocat_connection():
        return None

    station_code = (
        st.session_state.get("meteocat_station_id")
        or st.session_state.get("provider_station_id")
        or ""
    )
    station_code = str(station_code).strip().upper()
    if not station_code:
        return None

    snapshot = fetch_meteocat_station_snapshot(station_code, api_key=api_key)
    if not snapshot.get("ok"):
        return None

    year, month, day = _local_day_parts(None)
    day_payload = fetch_meteocat_station_day(station_code, year, month, day, api_key=api_key)
    day_vars = day_payload.get("variables", {}) if day_payload.get("ok") else {}

    station_meta = _find_station(station_code)
    coords = station_meta.get("coordenades", {}) if isinstance(station_meta, dict) else {}
    lat = _safe_float(coords.get("latitud"))
    lon = _safe_float(coords.get("longitud"))
    elevation = _safe_float(station_meta.get("altitud"), default=0.0)

    values = snapshot.get("values", {}) or {}
    latest_epoch = int(snapshot.get("latest_epoch") or time.time())

    wind_ms = _safe_float(values.get("wind"))
    gust_ms = _safe_float(values.get("gust"))
    wind_kmh = _ms_to_kmh(wind_ms)
    gust_kmh = _ms_to_kmh(gust_ms)

    wind_dir = _safe_float(values.get("wind_dir"))
    if _is_nan(wind_dir):
        wind_dir = _safe_float(values.get("gust_dir"))

    p_abs = _safe_float(values.get("pressure_abs"))
    p_msl = _absolute_to_msl(p_abs, elevation)

    # Extremos diarios desde endpoint /estacions/mesurades.
    temp_max = _safe_float(values.get("temp"))
    temp_min = _safe_float(values.get("temp"))
    rh_max = _safe_float(values.get("rh"))
    rh_min = _safe_float(values.get("rh"))
    gust_max = gust_kmh

    s_tmax = _series_from_map(day_vars, V_TEMP_MAX_DAY)
    s_tmin = _series_from_map(day_vars, V_TEMP_MIN_DAY)
    s_rhmax = _series_from_map(day_vars, V_RH_MAX_DAY)
    s_rhmin = _series_from_map(day_vars, V_RH_MIN_DAY)
    s_gmax = _series_from_map(day_vars, V_GUST)

    if s_tmax:
        tmax = _max_of_series(s_tmax)
        if not _is_nan(tmax):
            temp_max = tmax
    else:
        tmax = _max_of_series(_series_from_map(day_vars, V_TEMP))
        if not _is_nan(tmax):
            temp_max = tmax

    if s_tmin:
        tmin = _min_of_series(s_tmin)
        if not _is_nan(tmin):
            temp_min = tmin
    else:
        tmin = _min_of_series(_series_from_map(day_vars, V_TEMP))
        if not _is_nan(tmin):
            temp_min = tmin

    rhmax = _max_of_series(s_rhmax)
    if not _is_nan(rhmax):
        rh_max = rhmax
    rhmin = _min_of_series(s_rhmin)
    if not _is_nan(rhmin):
        rh_min = rhmin

    gmax = _max_of_series(s_gmax)
    if not _is_nan(gmax):
        gust_max = _ms_to_kmh(gmax)

    rain_today = _precip_today_mm(day_vars)
    rain_1min = _max_of_series(_series_from_map(day_vars, V_RAIN_1MIN_MAX))
    if _is_nan(rain_1min):
        rain_1min = float("nan")

    return {
        "Tc": _safe_float(values.get("temp")),
        "RH": _safe_float(values.get("rh")),
        "p_hpa": p_msl,      # Relativa estimada desde absoluta.
        "p_abs_hpa": p_abs,  # Absoluta reportada por Meteocat.
        "Td": float("nan"),
        "wind": wind_kmh,
        "gust": gust_kmh,
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "wind_dir_deg": wind_dir,
        "precip_total": rain_today,
        "rain_1min_mm": rain_1min,
        "solar_radiation": _safe_float(values.get("solar")),
        "uv": _safe_float(values.get("uv")),
        "epoch": latest_epoch,
        "time_local": snapshot.get("latest_iso", ""),
        "time_utc": snapshot.get("latest_iso", ""),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "idema": station_code,
        "station_code": station_code,
        "temp_max": temp_max,
        "temp_min": temp_min,
        "rh_max": rh_max,
        "rh_min": rh_min,
        "gust_max": gust_max,
        "pressure_3h_ago": float("nan"),
        "epoch_3h_ago": float("nan"),
    }

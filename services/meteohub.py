"""
Servicio para integrar observaciones de MeteoHub Italia.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from data_files import METEOHUB_IT_STATIONS_PATH
from services._common import load_stations_json, parse_epoch as _parse_epoch
from utils.provider_state import (
    clear_provider_runtime_error,
    get_connected_provider_station_id,
    is_provider_connection,
    resolve_state,
    set_provider_runtime_error,
)


METEOHUB_BASE_URL = os.getenv("METEOHUB_BASE_URL", "https://meteohub.agenziaitaliameteo.it").rstrip("/")
METEOHUB_TIMEOUT_SECONDS = int(os.getenv("METEOHUB_TIMEOUT_SECONDS", "18"))
METEOHUB_LICENSE_GROUP = os.getenv("METEOHUB_LICENSE_GROUP", "CCBY_COMPLIANT")
METEOHUB_STATION_TZ = "Europe/Rome"

P_TEMP = "B12101"
P_WIND_SPEED = "B11002"
P_WIND_DIR = "B11001"
P_PRESSURE = "B10004"
P_RH = "B13003"
P_PRECIP = "B13011"

QUERY_PRODUCTS = (P_TEMP, P_RH, P_PRESSURE, P_WIND_SPEED, P_WIND_DIR, P_PRECIP)
QUERY_LEVELS = (
    "103,2000,0,0",
    "103,6000,0,0",
    "103,10000,0,0",
    "1,0,0,0",
)
QUERY_TIMERANGES = ("254,0,0", "1,0,3600")


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _kelvin_to_c(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    return raw - 273.15 if raw > 170.0 else raw


def _pa_to_hpa(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    hpa = raw / 100.0 if abs(raw) > 2000.0 else raw
    # MeteoHub puede exponer B10004 en estaciones hidrologicas/no atmosfericas.
    # Fuera de este rango no se usa como barometro.
    if hpa < 300.0 or hpa > 1100.0:
        return float("nan")
    return hpa


def _ms_to_kmh(value: Any) -> float:
    raw = _safe_float(value)
    return raw * 3.6 if not _is_nan(raw) else float("nan")


def _non_negative(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    return max(0.0, raw)


def _request_json(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{METEOHUB_BASE_URL}/{path.lstrip('/')}"
    response = requests.get(
        url,
        params=params or {},
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        },
        timeout=METEOHUB_TIMEOUT_SECONDS,
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


@lru_cache(maxsize=2)
def _load_stations(path: str = str(METEOHUB_IT_STATIONS_PATH)) -> List[Dict[str, Any]]:
    return load_stations_json(path)


def _find_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip()
    if not target:
        return {}
    for station in _load_stations():
        if not isinstance(station, dict):
            continue
        if str(station.get("id") or station.get("source_id") or "").strip() == target:
            return station
    return {}


def _station_from_encoded_id(station_id: str) -> Dict[str, Any]:
    parts = str(station_id or "").split("|")
    if len(parts) < 4:
        return {}
    try:
        lat = float(parts[1])
        lon = float(parts[2])
    except Exception:
        return {}
    return {
        "id": station_id,
        "source_id": station_id,
        "network": parts[0],
        "lat": lat,
        "lon": lon,
        "name": parts[3].replace("-", " ").strip() or station_id,
        "tz": METEOHUB_STATION_TZ,
    }


def _station_detail_value(details: Any, code: str) -> Any:
    if not isinstance(details, list):
        return None
    for item in details:
        if not isinstance(item, dict):
            continue
        if str(item.get("var") or "").strip() == code:
            return item.get("val")
    return None


def _station_details(station_block: Dict[str, Any]) -> List[Dict[str, Any]]:
    stat = station_block.get("stat") if isinstance(station_block, dict) else {}
    details = stat.get("details") if isinstance(stat, dict) else []
    return details if isinstance(details, list) else []


def _observations_data(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _product_rank(product: Dict[str, Any]) -> int:
    level = str(product.get("lev") or "")
    if level == "103,10000,0,0":
        return 0
    if level == "103,6000,0,0":
        return 1
    if level == "103,2000,0,0":
        return 2
    return 3


def _products_by_code(station_block: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    products = station_block.get("prod") if isinstance(station_block, dict) else []
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        code = str(product.get("var") or "").strip()
        if not code:
            continue
        by_code.setdefault(code, []).append(product)
    for items in by_code.values():
        items.sort(key=_product_rank)
    return by_code


def _iter_product_values(products: Iterable[Dict[str, Any]]) -> Iterable[Tuple[int, float]]:
    rows: Dict[int, float] = {}
    for product in products:
        values = product.get("val") if isinstance(product, dict) else []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            epoch = _parse_epoch(item.get("ref"))
            if epoch is None:
                continue
            value = _safe_float(item.get("val"))
            rows[int(epoch)] = value
    for epoch in sorted(rows):
        yield epoch, rows[epoch]


def _series_for_code(by_code: Dict[str, List[Dict[str, Any]]], code: str) -> List[Tuple[int, float]]:
    return list(_iter_product_values(by_code.get(code, [])))


def _build_observations_query(start_utc: datetime, end_utc: datetime) -> str:
    products = " or ".join(QUERY_PRODUCTS)
    levels = " or ".join(QUERY_LEVELS)
    timeranges = " or ".join(QUERY_TIMERANGES)
    start_text = start_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_text = end_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"reftime: >={start_text},<={end_text};"
        f"timerange:{timeranges};"
        f"level:{levels};"
        f"license:{METEOHUB_LICENSE_GROUP};"
        f"product:{products}"
    )


def _utc_window_for_local_day(days_back: int = 0, tz_name: str = METEOHUB_STATION_TZ) -> Tuple[datetime, datetime]:
    try:
        station_tz = ZoneInfo(str(tz_name or METEOHUB_STATION_TZ))
    except Exception:
        station_tz = ZoneInfo(METEOHUB_STATION_TZ)
    now_local = datetime.now(station_tz)
    start_local = (now_local - timedelta(days=max(0, int(days_back)))).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return start_local.astimezone(timezone.utc), now_local.astimezone(timezone.utc)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_meteohub_observations(
    network: str,
    lat: float,
    lon: float,
    *,
    days_back: int = 0,
) -> Dict[str, Any]:
    network_id = str(network or "").strip()
    if not network_id:
        return {"ok": False, "error": "Falta red MeteoHub.", "data": []}
    start_utc, end_utc = _utc_window_for_local_day(days_back=days_back)
    params = {
        "q": _build_observations_query(start_utc, end_utc),
        "networks": network_id,
        "lat": float(lat),
        "lon": float(lon),
        "stationDetails": "true",
        "allStationProducts": "false",
    }
    try:
        payload = _request_json("/api/observations", params=params)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": []}
    data = _observations_data(payload)
    return {
        "ok": bool(data),
        "error": "" if data else "MeteoHub no devolvio datos para la estacion.",
        "data": data,
        "raw": payload,
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
    }


def _align_series(
    *,
    temp_rows: List[Tuple[int, float]],
    rh_rows: List[Tuple[int, float]],
    pressure_rows: List[Tuple[int, float]],
    wind_rows: List[Tuple[int, float]],
    direction_rows: List[Tuple[int, float]],
    precip_rows: List[Tuple[int, float]],
) -> Dict[str, Any]:
    rows: Dict[int, Dict[str, float]] = {}

    def add(source_rows: List[Tuple[int, float]], key: str, converter) -> None:
        for epoch, value in source_rows:
            rows.setdefault(int(epoch), {})[key] = converter(value)

    add(temp_rows, "temp", _kelvin_to_c)
    add(rh_rows, "rh", _safe_float)
    add(pressure_rows, "p_abs", _pa_to_hpa)
    add(wind_rows, "wind", _ms_to_kmh)
    add(direction_rows, "dir", _safe_float)
    add(precip_rows, "precip", _non_negative)

    epochs = sorted(rows.keys())
    return {
        "epochs": epochs,
        "temps": [rows[ep].get("temp", float("nan")) for ep in epochs],
        "humidities": [rows[ep].get("rh", float("nan")) for ep in epochs],
        "pressures_abs": [rows[ep].get("p_abs", float("nan")) for ep in epochs],
        "winds": [rows[ep].get("wind", float("nan")) for ep in epochs],
        "gusts": [float("nan")] * len(epochs),
        "wind_dirs": [rows[ep].get("dir", float("nan")) for ep in epochs],
        "precips": [rows[ep].get("precip", float("nan")) for ep in epochs],
        "solar_radiations": [float("nan")] * len(epochs),
        "uv_indexes": [float("nan")] * len(epochs),
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


def _sum_valid_non_negative(values: List[float]) -> float:
    clean = [max(0.0, float(v)) for v in values if not _is_nan(_safe_float(v))]
    return float(sum(clean)) if clean else float("nan")


def _pressure_3h_reference(epochs: List[int], pressures_abs: List[float], elevation_m: float) -> Tuple[float, Optional[int], Optional[int]]:
    valid = [
        (int(ep), float(p))
        for ep, p in zip(epochs, pressures_abs)
        if not _is_nan(_safe_float(p))
    ]
    if len(valid) < 2:
        return float("nan"), None, None
    valid.sort(key=lambda item: item[0])
    ep_now, _p_now = valid[-1]
    target_ep = ep_now - (3 * 3600)
    ep_old, p_abs_old = min(valid, key=lambda item: abs(item[0] - target_ep))
    p_msl_old = p_abs_old * math.exp(float(elevation_m) / 8000.0)
    return p_msl_old, ep_old, ep_now


def _first_station_block(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else []
    if isinstance(data, list) and data:
        first = data[0]
        return first if isinstance(first, dict) else {}
    return {}


def is_meteohub_connection() -> bool:
    return is_provider_connection("METEOHUB_IT", st.session_state)


def get_meteohub_data(state=None) -> Optional[Dict[str, Any]]:
    state = resolve_state(state)
    if not is_provider_connection("METEOHUB_IT", state):
        return None

    station_id = get_connected_provider_station_id("METEOHUB_IT", state)
    if not station_id:
        set_provider_runtime_error("METEOHUB_IT", "Falta id de estacion MeteoHub.", state)
        return None

    station_meta = _find_station(station_id) or _station_from_encoded_id(station_id)
    network = str(station_meta.get("network") or "").strip()
    lat = _safe_float(station_meta.get("lat"), default=_safe_float(getattr(state, "get", lambda *_: None)("provider_station_lat")))
    lon = _safe_float(station_meta.get("lon"), default=_safe_float(getattr(state, "get", lambda *_: None)("provider_station_lon")))
    if not network or _is_nan(lat) or _is_nan(lon):
        set_provider_runtime_error("METEOHUB_IT", "La estacion MeteoHub no tiene red o coordenadas validas.", state)
        return None

    payload = fetch_meteohub_observations(network, lat, lon, days_back=0)
    if not isinstance(payload, dict) or not payload.get("ok"):
        set_provider_runtime_error("METEOHUB_IT", str((payload or {}).get("error", "MeteoHub no devolvio datos.")), state)
        return None

    station_block = _first_station_block(payload)
    details = _station_details(station_block)
    by_code = _products_by_code(station_block)

    station_name = str(_station_detail_value(details, "B01019") or station_meta.get("name") or station_id).strip()
    detail_lat = _safe_float(_station_detail_value(details, "B05001"), default=lat)
    detail_lon = _safe_float(_station_detail_value(details, "B06001"), default=lon)
    elevation = _safe_float(
        _station_detail_value(details, "B07030"),
        default=_safe_float(_station_detail_value(details, "B07031"), default=_safe_float(station_meta.get("elev"), default=0.0)),
    )
    if _is_nan(elevation):
        elevation = 0.0

    for key, value in (
        ("provider_station_name", station_name),
        ("provider_station_lat", detail_lat),
        ("provider_station_lon", detail_lon),
        ("provider_station_alt", float(elevation)),
        ("provider_station_tz", METEOHUB_STATION_TZ),
        ("meteohub_it_station_name", station_name),
        ("meteohub_it_station_lat", detail_lat),
        ("meteohub_it_station_lon", detail_lon),
        ("meteohub_it_station_alt", float(elevation)),
        ("meteohub_it_station_tz", METEOHUB_STATION_TZ),
    ):
        try:
            state[key] = value
        except Exception:
            pass

    series = _align_series(
        temp_rows=_series_for_code(by_code, P_TEMP),
        rh_rows=_series_for_code(by_code, P_RH),
        pressure_rows=_series_for_code(by_code, P_PRESSURE),
        wind_rows=_series_for_code(by_code, P_WIND_SPEED),
        direction_rows=_series_for_code(by_code, P_WIND_DIR),
        precip_rows=_series_for_code(by_code, P_PRECIP),
    )
    if not series.get("has_data"):
        set_provider_runtime_error("METEOHUB_IT", "Serie vacia para la estacion.", state)
        return None

    epochs = series.get("epochs", [])
    temps = series.get("temps", [])
    rhs = series.get("humidities", [])
    p_abs_series = series.get("pressures_abs", [])
    winds = series.get("winds", [])
    gusts = series.get("gusts", [])
    dirs = series.get("wind_dirs", [])
    precips = series.get("precips", [])

    idx = len(epochs) - 1
    base_epoch = int(epochs[idx])
    p_abs_now = _last_valid(p_abs_series)
    p_msl_now = p_abs_now * math.exp(float(elevation) / 8000.0) if not _is_nan(p_abs_now) else float("nan")
    pressure_3h_ago, epoch_3h_ago, epoch_now_ref = _pressure_3h_reference(epochs, p_abs_series, elevation)
    if epoch_now_ref is not None:
        base_epoch = int(epoch_now_ref)

    clear_provider_runtime_error("METEOHUB_IT", state)
    return {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_tz": METEOHUB_STATION_TZ,
        "network": network,
        "lat": detail_lat,
        "lon": detail_lon,
        "elevation": float(elevation),
        "epoch": int(base_epoch),
        "Tc": _last_valid(temps),
        "RH": _last_valid(rhs),
        "Td": float("nan"),
        "p_hpa": p_msl_now,
        "p_abs_hpa": p_abs_now,
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": _last_valid(winds),
        "gust": _last_valid(gusts),
        "wind_dir_deg": _last_valid(dirs),
        "precip_total": _sum_valid_non_negative(precips),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "temp_max": _max_valid(temps),
        "temp_min": _min_valid(temps),
        "rh_max": _max_valid(rhs),
        "rh_min": _min_valid(rhs),
        "gust_max": _max_valid(gusts),
        "_series": {
            "epochs": [int(ep) for ep in epochs],
            "temps": [float(v) for v in temps],
            "humidities": [float(v) for v in rhs],
            "pressures_abs": [float(v) for v in p_abs_series],
            "winds": [float(v) for v in winds],
            "gusts": [float(v) for v in gusts],
            "wind_dirs": [float(v) for v in dirs],
            "precips": [float(v) for v in precips],
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
        "raw_details": details,
    }

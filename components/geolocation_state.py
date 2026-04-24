"""Helpers compartidos para estado y normalización de geolocalización."""

from __future__ import annotations

import math
from typing import Any, Iterable

import streamlit as st

from providers import search_nearby_stations


def safe_float(value: Any, default: Any = None):
    try:
        number = float(value)
        if math.isnan(number):
            return default
        return number
    except Exception:
        return default


def first_valid_float(*values: Any, default: float) -> float:
    for value in values:
        parsed = safe_float(value, default=None)
        if parsed is not None:
            return float(parsed)
    return float(default)


def default_search_coords(
    *,
    search_lat_key: str,
    search_lon_key: str,
    fallback_lat_values: Iterable[Any],
    fallback_lon_values: Iterable[Any],
    default_lat: float,
    default_lon: float,
) -> tuple[float, float]:
    lat = first_valid_float(
        st.session_state.get(search_lat_key),
        *fallback_lat_values,
        default=default_lat,
    )
    lon = first_valid_float(
        st.session_state.get(search_lon_key),
        *fallback_lon_values,
        default=default_lon,
    )
    return float(lat), float(lon)


def _in_lat_lon_range(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def normalize_coords_order(lat: float, lon: float):
    """Corrige lat/lon invertidas usando rango y distancia a estaciones reales."""
    lat = float(lat)
    lon = float(lon)

    if (lat < -90.0 or lat > 90.0) and (-90.0 <= lon <= 90.0) and (-180.0 <= lat <= 180.0):
        return lon, lat, True
    if (lon < -180.0 or lon > 180.0) and (-180.0 <= lat <= 180.0) and (-90.0 <= lon <= 90.0):
        return lon, lat, True
    if abs(lon) <= 90.0 and abs(lat) > 90.0 and abs(lat) <= 180.0:
        return lon, lat, True

    normal_ok = _in_lat_lon_range(lat, lon)
    swapped_ok = _in_lat_lon_range(lon, lat)

    if normal_ok and not swapped_ok:
        return lat, lon, False
    if swapped_ok and not normal_ok:
        return lon, lat, True
    if not normal_ok and not swapped_ok:
        return lat, lon, False

    best_normal = search_nearby_stations(lat, lon, max_results=1)
    d_normal = best_normal[0].distance_km if best_normal else float("inf")

    if d_normal > 500.0 and swapped_ok:
        best_swapped = search_nearby_stations(lon, lat, max_results=1)
        d_swapped = best_swapped[0].distance_km if best_swapped else float("inf")
        if d_swapped < (d_normal * 0.5):
            return lon, lat, True

    return lat, lon, False


def ensure_geo_state(prefix: str, *, request_id_start: int = 0) -> None:
    if f"{prefix}_request_id" not in st.session_state:
        st.session_state[f"{prefix}_request_id"] = int(request_id_start)
    if f"{prefix}_pending" not in st.session_state:
        st.session_state[f"{prefix}_pending"] = False
    if f"{prefix}_last_error" not in st.session_state:
        st.session_state[f"{prefix}_last_error"] = ""
    if f"{prefix}_debug_msg" not in st.session_state:
        st.session_state[f"{prefix}_debug_msg"] = ""


def consume_browser_geolocation(
    prefix: str,
    *,
    get_browser_geolocation,
    timeout_ms: int = 12000,
    high_accuracy: bool = True,
) -> dict[str, Any] | None:
    pending_key = f"{prefix}_pending"
    if not st.session_state.get(pending_key):
        return None

    request_id = int(st.session_state.get(f"{prefix}_request_id", 0))
    result = get_browser_geolocation(
        request_id=request_id,
        timeout_ms=timeout_ms,
        high_accuracy=high_accuracy,
    )
    if not isinstance(result, dict):
        return None

    st.session_state[pending_key] = False
    if result.get("ok"):
        lat = result.get("lat")
        lon = result.get("lon")
        if lat is not None and lon is not None:
            lat, lon, swapped = normalize_coords_order(lat, lon)
            return {
                "ok": True,
                "lat": float(lat),
                "lon": float(lon),
                "swapped": bool(swapped),
                "accuracy_m": result.get("accuracy_m"),
            }

    return {
        "ok": False,
        "error_message": result.get("error_message"),
    }


def start_browser_geolocation_request(prefix: str, *, message: str = "") -> None:
    st.session_state[f"{prefix}_request_id"] = int(st.session_state.get(f"{prefix}_request_id", 0)) + 1
    st.session_state[f"{prefix}_pending"] = True
    st.session_state[f"{prefix}_last_error"] = ""
    st.session_state[f"{prefix}_debug_msg"] = str(message or "")

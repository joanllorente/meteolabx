"""Session caches for Weather Underground data served by FastAPI.

This module intentionally contains no provider URL, HTTP client, payload
parser, or direct fallback. Weather Underground transport is owned entirely by
``server.services.wu``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Dict, Mapping, Optional

import streamlit as st

from config import MAX_CACHE_SIZE


def _wu_cache_key(station_id: str, api_key: str) -> tuple[str, str]:
    station_norm = str(station_id or "").strip().upper()
    key_norm = str(api_key or "").strip()
    key_hash = hashlib.sha1(key_norm.encode("utf-8")).hexdigest()[:12] if key_norm else ""
    return station_norm, key_hash


def fetch_wu_dashboard_session_cached(
    station_id: str,
    api_key: str,
    ttl_s: int,
    *,
    calibration: Optional[Mapping[str, Any]] = None,
    station_elevation: Optional[float] = None,
    sun_tz_name: str = "",
) -> Dict[str, Any]:
    """Return canonical ``/current/processed`` data with a session cache."""
    cache = st.session_state.setdefault("wu_cache_dashboard", OrderedDict())
    calibration_key = json.dumps(calibration or {}, sort_keys=True, separators=(",", ":"))
    cache_key = (
        *_wu_cache_key(station_id, api_key),
        calibration_key,
        round(float(station_elevation or 0.0), 2),
        str(sun_tz_name or ""),
    )
    now = time.time()
    cached = cache.get(cache_key)
    if cached and now - cached["t"] < float(ttl_s):
        cache.move_to_end(cache_key)
        return cached["data"]

    from utils.api_client import fetch_provider_current_processed_via_api

    dashboard = fetch_provider_current_processed_via_api(
        "WU",
        station_id,
        api_key=api_key,
        sun_tz_name=sun_tz_name,
        station_elevation=station_elevation,
        calibration=calibration,
    )
    cache[cache_key] = {"t": now, "data": dashboard}
    cache.move_to_end(cache_key)
    while len(cache) > MAX_CACHE_SIZE:
        cache.popitem(last=False)
    return dashboard


def fetch_hourly_7day_session_cached(
    station_id: str,
    api_key: str,
    *,
    calibration: Optional[Mapping[str, Any]] = None,
    station_elevation: float | None = None,
) -> Dict[str, Any]:
    """Return canonical recent WU series with a one-hour session cache.

    ``calibration`` (offsets WU) se envía al backend, que la aplica a la
    serie antes de derivar. Entra en la cache key para que un cambio de
    offsets no devuelva la versión anterior.
    """
    cache = st.session_state.setdefault("wu_cache_hourly7d", {})
    calibration_key = json.dumps(calibration or {}, sort_keys=True, separators=(",", ":"))
    elevation_key = "" if station_elevation is None else str(float(station_elevation))
    cache_key = (*_wu_cache_key(station_id, api_key), calibration_key, elevation_key)
    now = time.time()
    cached = cache.get(cache_key)
    if cached and now - cached["t"] < 3600.0:
        return cached["data"]

    from utils.api_client import fetch_provider_recent_series_via_api_strict

    result = fetch_provider_recent_series_via_api_strict(
        "WU",
        station_id,
        api_key=api_key,
        days_back=7,
        calibration=calibration,
        station_elevation=station_elevation,
    )
    cache[cache_key] = {"t": now, "data": result}
    return result

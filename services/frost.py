"""
Servicio para integrar observaciones de MET Norway Frost.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from data_files import FROST_STATIONS_PATH
from utils.provider_state import (
    clear_provider_runtime_error,
    get_connected_provider_station_id,
    get_provider_station_id,
    is_provider_connection,
    resolve_state,
    set_provider_runtime_error,
)


FROST_BASE_URL = os.getenv("FROST_BASE_URL", "https://frost.met.no").rstrip("/")
FROST_TIMEOUT_SECONDS = int(os.getenv("FROST_TIMEOUT_SECONDS", "18"))
FROST_STATION_TZ = "Europe/Oslo"

_DEFAULT_FROST_CLIENT_ID = "5a7edecc-64b2-46f2-bd31-4470c2f4e1ee"
_DEFAULT_FROST_CLIENT_SECRET = "eba446fe-e8fc-4b42-b2fc-1523d02ea26f"

FROST_LATEST_ELEMENTS = [
    "accumulated(precipitation_amount)",
    "sum(precipitation_amount PT1M)",
    "sum(precipitation_amount PT1H)",
    "precipitation_amount",
    "surface_air_pressure",
    "wind_speed",
    "wind_speed_of_gust",
    "wind_from_direction",
    "relative_humidity",
    "air_temperature",
]

FROST_TODAY_ELEMENTS = [
    "accumulated(precipitation_amount)",
    "sum(precipitation_amount PT1M)",
    "sum(precipitation_amount PT1H)",
    "precipitation_amount",
    "surface_air_pressure",
    "wind_speed",
    "wind_speed_of_gust",
    "wind_from_direction",
    "relative_humidity",
    "air_temperature",
]

FROST_TREND_ELEMENTS = [
    "surface_air_pressure",
    "relative_humidity",
    "air_temperature",
]

FROST_REQUIRED_ANY_ELEMENTS = [
    "accumulated(precipitation_amount)",
    "sum(precipitation_amount PT1M)",
    "sum(precipitation_amount PT1H)",
    "precipitation_amount",
    "surface_air_pressure",
    "relative_humidity",
    "air_temperature",
    "wind_speed",
    "wind_from_direction",
]

FROST_CLIMO_MONTHLY_ELEMENT_MAP: Dict[str, str] = {
    "temp_mean": "mean(air_temperature P1M)",
    "temp_max": "mean(max(air_temperature P1D) P1M)",
    "temp_min": "mean(min(air_temperature P1D) P1M)",
    "precip_total": "sum(precipitation_amount P1M)",
    "rain_days": "number_of_days_gte(sum(precipitation_amount P1D) P1M 1.0)",
    "solar_hours": "sum(duration_of_sunshine P1M)",
}

FROST_CLIMO_YEARLY_ELEMENT_MAP: Dict[str, str] = {
    "temp_mean": "mean(air_temperature P1Y)",
    "temp_max": "mean(max(air_temperature P1D) P1Y)",
    "temp_min": "mean(min(air_temperature P1D) P1Y)",
    "precip_total": "sum(precipitation_amount P1Y)",
    "rain_days": "number_of_days_gte(sum(precipitation_amount P1D) P1Y 1.0)",
    "solar_hours": "sum(duration_of_sunshine P1Y)",
}


def _get_setting(env_key: str, default: str = "") -> str:
    try:
        secret_val = st.secrets.get(env_key, "")
        if secret_val not in (None, ""):
            return str(secret_val).strip()
    except Exception:
        pass
    return str(os.getenv(env_key, default)).strip()


FROST_CLIENT_ID = _get_setting("FROST_CLIENT_ID", _DEFAULT_FROST_CLIENT_ID)
FROST_CLIENT_SECRET = _get_setting(
    "FROST_CLIENT_SECRET",
    _DEFAULT_FROST_CLIENT_SECRET,
)


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _parse_epoch(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _request_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
    }


@st.cache_data(ttl=300, show_spinner=False)
def _request_json_cached(endpoint: str, params_json: str, client_id: str, client_secret: str) -> Any:
    params = json.loads(params_json) if params_json else {}
    response = requests.get(
        f"{FROST_BASE_URL}{endpoint}",
        params=params,
        headers=_request_headers(),
        auth=(client_id, client_secret),
        timeout=FROST_TIMEOUT_SECONDS,
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


def _request_json(endpoint: str, params: Dict[str, Any], client_id: str, client_secret: str) -> Any:
    return _request_json_cached(
        endpoint=endpoint,
        params_json=json.dumps(params, sort_keys=True, ensure_ascii=False),
        client_id=client_id,
        client_secret=client_secret,
    )


def _merge_observation_payloads(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_items: Dict[Tuple[str, str], Dict[str, Any]] = {}
    base_payload: Dict[str, Any] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if not base_payload:
            base_payload = {k: v for k, v in payload.items() if k != "data"}
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("sourceId", "")).strip(),
                str(item.get("referenceTime", "")).strip(),
            )
            target = merged_items.setdefault(
                key,
                {
                    "sourceId": item.get("sourceId"),
                    "referenceTime": item.get("referenceTime"),
                    "observations": [],
                },
            )
            observations = item.get("observations", [])
            if isinstance(observations, list):
                target["observations"].extend(
                    obs for obs in observations if isinstance(obs, dict)
                )
    merged_data = sorted(
        merged_items.values(),
        key=lambda item: _parse_epoch(item.get("referenceTime")) or 0,
    )
    merged = dict(base_payload) if base_payload else {}
    merged["data"] = merged_data
    merged["currentItemCount"] = len(merged_data)
    merged["itemsPerPage"] = len(merged_data)
    merged["offset"] = 0
    merged["totalItemCount"] = len(merged_data)
    return merged


def _request_observations_resilient(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    referencetime: str,
    elements: Tuple[str, ...],
    maxage: str = "",
) -> Dict[str, Any]:
    element_list = [str(element).strip() for element in elements if str(element).strip()]
    if not element_list:
        return {"data": []}

    params: Dict[str, Any] = {
        "sources": str(station_id).strip().upper(),
        "referencetime": referencetime,
        "elements": ",".join(element_list),
    }
    if maxage:
        params["maxage"] = maxage

    try:
        return _request_json(
            "/observations/v0.jsonld",
            params,
            client_id=client_id,
            client_secret=client_secret,
        )
    except Exception:
        payloads: List[Dict[str, Any]] = []
        for element_id in element_list:
            single_params = {
                "sources": str(station_id).strip().upper(),
                "referencetime": referencetime,
                "elements": element_id,
            }
            if maxage:
                single_params["maxage"] = maxage
            try:
                payload = _request_json(
                    "/observations/v0.jsonld",
                    single_params,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            except Exception:
                continue
            if isinstance(payload, dict) and payload.get("data"):
                payloads.append(payload)
        if payloads:
            return _merge_observation_payloads(payloads)
        return {"data": []}


def _request_climatenormals(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    period: str,
    elements: Sequence[str],
) -> Dict[str, Any]:
    element_list = [str(element).strip() for element in elements if str(element).strip()]
    if not period or not element_list:
        return {"data": []}
    return _request_json(
        "/climatenormals/v0.jsonld",
        {
            "sources": str(station_id).strip().upper(),
            "period": str(period).strip(),
            "elements": ",".join(element_list),
        },
        client_id=client_id,
        client_secret=client_secret,
    )


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_frost_available_time_series(station_id: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    return _request_json(
        "/observations/availableTimeSeries/v0.jsonld",
        {
            "sources": str(station_id).strip().upper(),
        },
        client_id=client_id,
        client_secret=client_secret,
    )


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_frost_climatenormals_available(
    station_id: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, Any]:
    return _request_json(
        "/climatenormals/available/v0.jsonld",
        {
            "sources": str(station_id).strip().upper(),
        },
        client_id=client_id,
        client_secret=client_secret,
    )


@lru_cache(maxsize=2)
def _load_stations(path: str = str(FROST_STATIONS_PATH)) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _find_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip().upper()
    if not target:
        return {}
    for station in _load_stations():
        if str(station.get("id", "")).strip().upper() == target:
            return station
    return {}


def _station_day_window(tz_name: str = FROST_STATION_TZ) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _recent_window(days: int, tz_name: str = FROST_STATION_TZ) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    start_local = now_local - timedelta(days=max(1, int(days)))
    return start_local.astimezone(timezone.utc), now_local.astimezone(timezone.utc)


def _canonical_element(element_id: str) -> Optional[str]:
    eid = str(element_id or "").strip()
    mapping = {
        "air_temperature": "temp_c",
        "relative_humidity": "rh",
        "surface_air_pressure": "p_abs_hpa",
        "wind_speed": "wind_ms",
        "wind_speed_of_gust": "gust_ms",
        "wind_from_direction": "wind_dir_deg",
        "accumulated(precipitation_amount)": "precip_accum_mm",
        "sum(precipitation_amount PT1M)": "precip_step_mm",
        "sum(precipitation_amount PT1H)": "precip_step_mm",
        "precipitation_amount": "precip_step_mm",
    }
    return mapping.get(eid)


def _timeseries_overlap(
    item: Dict[str, Any],
    *,
    start_utc: Optional[datetime] = None,
    end_utc: Optional[datetime] = None,
    latest_only: bool = False,
) -> bool:
    valid_from = _parse_iso_datetime(item.get("validFrom")) or datetime.min.replace(tzinfo=timezone.utc)
    valid_to = _parse_iso_datetime(item.get("validTo")) or datetime.max.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)

    if latest_only:
        return valid_from <= now_utc <= valid_to

    window_start = start_utc or now_utc
    window_end = end_utc or now_utc
    return valid_from <= window_end and valid_to >= window_start


def _available_elements(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    requested_elements: List[str],
    start_utc: Optional[datetime] = None,
    end_utc: Optional[datetime] = None,
    latest_only: bool = False,
) -> List[str]:
    payload = fetch_frost_available_time_series(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    available: Dict[str, int] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("elementId", "")).strip()
        if not element_id or element_id not in requested_elements:
            continue
        if not _timeseries_overlap(
            item,
            start_utc=start_utc,
            end_utc=end_utc,
            latest_only=latest_only,
        ):
            continue
        available[element_id] = 1
    return [element_id for element_id in requested_elements if element_id in available]


def _available_climo_elements_by_period(
    station_id: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, List[str]]:
    payload = fetch_frost_climatenormals_available(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    out: Dict[str, List[str]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period", "")).strip()
        element_id = str(item.get("elementId", "")).strip()
        if not period or not element_id:
            continue
        bucket = out.setdefault(period, [])
        if element_id not in bucket:
            bucket.append(element_id)
    return out


def get_frost_climo_period_options(
    station_id: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, List[str]]:
    available = _available_climo_elements_by_period(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    monthly_required = set(FROST_CLIMO_MONTHLY_ELEMENT_MAP.values())
    yearly_required = set(FROST_CLIMO_YEARLY_ELEMENT_MAP.values())
    monthly_periods: List[str] = []
    yearly_periods: List[str] = []
    for period, elements in available.items():
        element_set = set(elements)
        if element_set & monthly_required:
            monthly_periods.append(period)
        if element_set & yearly_required:
            yearly_periods.append(period)

    def _period_sort_key(period_text: str) -> Tuple[int, int]:
        parts = str(period_text).split("/")
        try:
            start_year = int(parts[0])
        except Exception:
            start_year = -1
        try:
            end_year = int(parts[1])
        except Exception:
            end_year = -1
        return (start_year, end_year)

    monthly_periods.sort(key=_period_sort_key)
    yearly_periods.sort(key=_period_sort_key)
    return {
        "monthly": monthly_periods,
        "annual": yearly_periods,
    }


def _level_value(obs: Dict[str, Any]) -> Optional[float]:
    level = obs.get("level")
    if not isinstance(level, dict):
        return None
    value = _safe_float(level.get("value"))
    if _is_nan(value):
        return None
    return float(value)


def _resolution_rank(resolution: str, canonical: str) -> int:
    res = str(resolution or "").strip().upper()
    if canonical in {"temp_c", "rh", "p_abs_hpa"}:
        order = {"PT1M": 4, "PT10M": 3, "PT1H": 2, "PT6H": 1}
    elif canonical in {"wind_ms", "gust_ms", "wind_dir_deg"}:
        order = {"PT1M": 4, "PT10M": 3, "PT1H": 2}
    elif canonical == "precip_accum_mm":
        order = {"PT10M": 4, "PT1H": 3, "PT1M": 2, "PT12H": 1}
    else:
        order = {}
    return int(order.get(res, 0))


def _level_rank(level_value: Optional[float], canonical: str) -> int:
    if level_value is None:
        return 0
    if canonical in {"temp_c", "rh", "p_abs_hpa"}:
        if abs(level_value - 2.0) < 0.01:
            return 3
        if abs(level_value - 10.0) < 0.01:
            return 2
    if canonical in {"wind_ms", "gust_ms", "wind_dir_deg"}:
        if abs(level_value - 10.0) < 0.01:
            return 3
        if abs(level_value - 2.0) < 0.01:
            return 2
    return 1


def _quality_rank(obs: Dict[str, Any]) -> int:
    try:
        quality = int(obs.get("qualityCode"))
    except Exception:
        return 0
    return max(0, 10 - quality)


def _candidate_score(obs: Dict[str, Any], canonical: str) -> Tuple[int, int, int]:
    level_value = _level_value(obs)
    return (
        _resolution_rank(str(obs.get("timeResolution", "")), canonical),
        _level_rank(level_value, canonical),
        _quality_rank(obs),
    )


def _choose_observation(observations: List[Dict[str, Any]], canonical: str) -> Optional[Dict[str, Any]]:
    candidates = [
        obs for obs in observations
        if _canonical_element(str(obs.get("elementId", ""))) == canonical
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda obs: (
            int(obs.get("_reference_epoch", 0)),
            *_candidate_score(obs, canonical),
        ),
        reverse=True,
    )
    return candidates[0]


def _ms_to_kmh(value: float) -> float:
    return float(value) * 3.6 if not _is_nan(value) else float("nan")


def _build_row(reference_epoch: int, observations: List[Dict[str, Any]]) -> Dict[str, float]:
    temp_obs = _choose_observation(observations, "temp_c")
    rh_obs = _choose_observation(observations, "rh")
    pressure_obs = _choose_observation(observations, "p_abs_hpa")
    wind_obs = _choose_observation(observations, "wind_ms")
    gust_obs = _choose_observation(observations, "gust_ms")
    dir_obs = _choose_observation(observations, "wind_dir_deg")
    precip_obs = _choose_observation(observations, "precip_accum_mm")
    precip_step_obs = _choose_observation(observations, "precip_step_mm")

    temp_c = _safe_float(temp_obs.get("value")) if isinstance(temp_obs, dict) else float("nan")
    rh = _safe_float(rh_obs.get("value")) if isinstance(rh_obs, dict) else float("nan")
    p_abs = _safe_float(pressure_obs.get("value")) if isinstance(pressure_obs, dict) else float("nan")
    wind = _ms_to_kmh(_safe_float(wind_obs.get("value"))) if isinstance(wind_obs, dict) else float("nan")
    gust = _ms_to_kmh(_safe_float(gust_obs.get("value"))) if isinstance(gust_obs, dict) else float("nan")
    wind_dir = _safe_float(dir_obs.get("value")) if isinstance(dir_obs, dict) else float("nan")
    precip_accum = _safe_float(precip_obs.get("value")) if isinstance(precip_obs, dict) else float("nan")
    precip_step = _safe_float(precip_step_obs.get("value")) if isinstance(precip_step_obs, dict) else float("nan")

    return {
        "epoch": int(reference_epoch),
        "temp_c": temp_c,
        "rh": rh,
        "p_abs_hpa": p_abs,
        "wind_kmh": wind,
        "gust_kmh": gust,
        "wind_dir_deg": wind_dir,
        "precip_accum_mm": precip_accum,
        "precip_step_mm": precip_step,
    }


def _better_candidate(candidate: Dict[str, Any], current: Optional[Dict[str, Any]], canonical: str) -> bool:
    if current is None:
        return True
    left = (
        int(candidate.get("epoch", 0)),
        *_candidate_score(candidate.get("obs", {}), canonical),
    )
    right = (
        int(current.get("epoch", 0)),
        *_candidate_score(current.get("obs", {}), canonical),
    )
    return left > right


def _latest_selected_rows(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        epoch = _parse_epoch(item.get("referenceTime"))
        if epoch is None:
            continue
        observations = item.get("observations", [])
        if not isinstance(observations, list):
            continue
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            canonical = _canonical_element(obs.get("elementId", ""))
            if not canonical:
                continue
            candidate = {"epoch": int(epoch), "obs": obs}
            if _better_candidate(candidate, selected.get(canonical), canonical):
                selected[canonical] = candidate
    return selected


def _bin_series(payload: Dict[str, Any], *, bin_seconds: int) -> Dict[str, Any]:
    bins: Dict[int, List[Dict[str, Any]]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        epoch = _parse_epoch(item.get("referenceTime"))
        if epoch is None:
            continue
        observations = item.get("observations", [])
        if not isinstance(observations, list):
            continue
        bucket_epoch = (int(epoch) // bin_seconds) * bin_seconds
        bucket = bins.setdefault(bucket_epoch, [])
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            obs_copy = dict(obs)
            obs_copy["_reference_epoch"] = int(epoch)
            bucket.append(obs_copy)

    epochs = sorted(bins.keys())
    temps: List[float] = []
    rhs: List[float] = []
    p_abs: List[float] = []
    winds: List[float] = []
    gusts: List[float] = []
    dirs: List[float] = []
    precips: List[float] = []
    precip_steps: List[float] = []

    for epoch in epochs:
        row = _build_row(epoch, bins[epoch])
        temps.append(float(row.get("temp_c", float("nan"))))
        rhs.append(float(row.get("rh", float("nan"))))
        p_abs.append(float(row.get("p_abs_hpa", float("nan"))))
        winds.append(float(row.get("wind_kmh", float("nan"))))
        gusts.append(float(row.get("gust_kmh", float("nan"))))
        dirs.append(float(row.get("wind_dir_deg", float("nan"))))
        precips.append(float(row.get("precip_accum_mm", float("nan"))))
        precip_steps.append(float(row.get("precip_step_mm", float("nan"))))

    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": temps,
        "humidities": rhs,
        "pressures_abs": p_abs,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "precip_accum_mm": precips,
        "precip_step_mm": precip_steps,
        "has_data": bool(epochs),
    }


def _last_valid(values: List[float]) -> float:
    for value in reversed(values):
        v = _safe_float(value)
        if not _is_nan(v):
            return float(v)
    return float("nan")


def _max_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return max(clean) if clean else float("nan")


def _min_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return min(clean) if clean else float("nan")


def _precip_total_from_accum(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    if len(clean) < 2:
        return 0.0 if clean else float("nan")
    diffs = [clean[i] - clean[i - 1] for i in range(1, len(clean))]
    non_negative_ratio = (
        sum(1 for diff in diffs if diff >= -0.1) / len(diffs)
        if diffs else 1.0
    )
    if non_negative_ratio >= 0.65:
        return max(0.0, clean[-1] - clean[0])
    total = 0.0
    for prev, current in zip(clean, clean[1:]):
        diff = current - prev
        if diff >= 0:
            total += diff
        else:
            total += max(0.0, current)
    return max(0.0, total)


def _precip_total_from_steps(values: List[float]) -> float:
    clean = [max(0.0, float(v)) for v in values if not _is_nan(_safe_float(v))]
    if not clean:
        return float("nan")
    return max(0.0, sum(clean))


def _precip_total(values_accum: List[float], values_step: List[float]) -> float:
    accum_total = _precip_total_from_accum(values_accum)
    if not _is_nan(accum_total) and accum_total > 0:
        return accum_total
    step_total = _precip_total_from_steps(values_step)
    if not _is_nan(step_total):
        return step_total
    if not _is_nan(accum_total):
        return accum_total
    return float("nan")


def _pressure_3h_reference(epochs: List[int], pressures_abs: List[float]) -> Tuple[float, Optional[int], Optional[int]]:
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
    ep_old, p_old = min(valid, key=lambda item: abs(item[0] - target_ep))
    return p_old, ep_old, ep_now


def _empty_frost_climo_df() -> pd.DataFrame:
    cols = [
        "date", "epoch", "temp_mean", "temp_max", "temp_min",
        "wind_mean", "gust_max", "precip_total",
        "solar_hours", "rain_days", "period_label",
    ]
    return pd.DataFrame(columns=cols)


def _climo_value_map(payload: Dict[str, Any]) -> Dict[Tuple[str, Optional[int]], float]:
    values: Dict[Tuple[str, Optional[int]], float] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("elementId", "")).strip()
        month_raw = item.get("month")
        try:
            month = int(month_raw) if month_raw is not None else None
        except Exception:
            month = None
        normal = _safe_float(item.get("normal"))
        if _is_nan(normal):
            continue
        values[(element_id, month)] = float(normal)
    return values


def _period_anchor_year(period_label: str) -> int:
    parts = str(period_label or "").split("/")
    for part in reversed(parts):
        try:
            return int(part)
        except Exception:
            continue
    return 2000


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_frost_climo_monthly_for_period(
    station_id: str,
    period: str,
    months: Sequence[int],
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    period = str(period or "").strip()
    selected_months = sorted({int(month) for month in months if 1 <= int(month) <= 12})
    if not period or not selected_months:
        return _empty_frost_climo_df()

    available = _available_climo_elements_by_period(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    available_set = set(available.get(period, []))
    elements = [
        element_id
        for element_id in FROST_CLIMO_MONTHLY_ELEMENT_MAP.values()
        if element_id in available_set
    ]
    if not elements:
        return _empty_frost_climo_df()

    payload = _request_climatenormals(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
        period=period,
        elements=elements,
    )
    value_map = _climo_value_map(payload)
    anchor_year = _period_anchor_year(period)

    rows: List[Dict[str, Any]] = []
    for month in selected_months:
        row: Dict[str, Any] = {
            "date": pd.Timestamp(year=anchor_year, month=int(month), day=1),
            "epoch": 0.0,
            "temp_mean": float("nan"),
            "temp_max": float("nan"),
            "temp_min": float("nan"),
            "wind_mean": float("nan"),
            "gust_max": float("nan"),
            "precip_total": float("nan"),
            "solar_hours": float("nan"),
            "rain_days": float("nan"),
            "period_label": period,
        }
        for field, element_id in FROST_CLIMO_MONTHLY_ELEMENT_MAP.items():
            if element_id not in available_set:
                continue
            value = value_map.get((element_id, int(month)))
            if value is None:
                continue
            row[field] = float(value)
        rows.append(row)

    if not rows:
        return _empty_frost_climo_df()
    frame = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return frame


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_frost_climo_yearly_for_periods(
    station_id: str,
    periods: Sequence[str],
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    selected_periods = [str(period).strip() for period in periods if str(period).strip()]
    if not selected_periods:
        return _empty_frost_climo_df()

    available = _available_climo_elements_by_period(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    rows: List[Dict[str, Any]] = []
    for period in selected_periods:
        available_set = set(available.get(period, []))
        elements = [
            element_id
            for element_id in FROST_CLIMO_YEARLY_ELEMENT_MAP.values()
            if element_id in available_set
        ]
        if not elements:
            continue
        payload = _request_climatenormals(
            station_id,
            client_id=client_id,
            client_secret=client_secret,
            period=period,
            elements=elements,
        )
        value_map = _climo_value_map(payload)
        anchor_year = _period_anchor_year(period)
        row: Dict[str, Any] = {
            "date": pd.Timestamp(year=anchor_year, month=1, day=1),
            "epoch": 0.0,
            "temp_mean": float("nan"),
            "temp_max": float("nan"),
            "temp_min": float("nan"),
            "wind_mean": float("nan"),
            "gust_max": float("nan"),
            "precip_total": float("nan"),
            "solar_hours": float("nan"),
            "rain_days": float("nan"),
            "period_label": period,
        }
        for field, element_id in FROST_CLIMO_YEARLY_ELEMENT_MAP.items():
            if element_id not in available_set:
                continue
            value = value_map.get((element_id, None))
            if value is None:
                continue
            row[field] = float(value)
        rows.append(row)

    if not rows:
        return _empty_frost_climo_df()
    frame = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return frame


@st.cache_data(ttl=300, show_spinner=False)
def fetch_frost_latest(
    station_id: str,
    client_id: str,
    client_secret: str,
    elements: Tuple[str, ...],
) -> Dict[str, Any]:
    return _request_observations_resilient(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
        referencetime="latest",
        elements=elements,
        maxage="PT12H",
    )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_frost_today_series(
    station_id: str,
    client_id: str,
    client_secret: str,
    station_tz: str,
    elements: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    start_utc, end_utc = _station_day_window(station_tz)
    return _request_observations_resilient(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
        referencetime=f"{_to_iso_z(start_utc)}/{_to_iso_z(end_utc)}",
        elements=elements,
    )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_frost_recent_series(
    station_id: str,
    client_id: str,
    client_secret: str,
    station_tz: str,
    days: int = 7,
    elements: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    start_utc, end_utc = _recent_window(days=days, tz_name=station_tz)
    return _request_observations_resilient(
        station_id,
        client_id=client_id,
        client_secret=client_secret,
        referencetime=f"{_to_iso_z(start_utc)}/{_to_iso_z(end_utc)}",
        elements=elements,
    )


def is_frost_connection() -> bool:
    return is_provider_connection("FROST", st.session_state)


def get_frost_data(state=None) -> Optional[Dict[str, Any]]:
    state = resolve_state(state)
    if not is_provider_connection("FROST", state):
        return None

    client_id = str(FROST_CLIENT_ID or "").strip()
    client_secret = str(FROST_CLIENT_SECRET or "").strip()
    if not client_id or not client_secret:
        set_provider_runtime_error("FROST", "Faltan FROST_CLIENT_ID / FROST_CLIENT_SECRET.", state)
        return None

    station_id = get_connected_provider_station_id("FROST", state)
    if not station_id:
        set_provider_runtime_error("FROST", "Falta station_id de Frost.", state)
        return None

    station_meta = _find_station(station_id)
    station_name = str(station_meta.get("name", "")).strip() or station_id
    station_lat = _safe_float(station_meta.get("lat"))
    station_lon = _safe_float(station_meta.get("lon"))
    elevation = _safe_float(station_meta.get("elev"), default=0.0)
    station_tz = FROST_STATION_TZ
    today_start_utc, today_end_utc = _station_day_window(station_tz)
    recent_start_utc, recent_end_utc = _recent_window(days=7, tz_name=station_tz)

    try:
        latest_elements = tuple(
            _available_elements(
                station_id,
                client_id=client_id,
                client_secret=client_secret,
                requested_elements=FROST_LATEST_ELEMENTS,
                latest_only=True,
            )
        )
        today_elements = tuple(
            _available_elements(
                station_id,
                client_id=client_id,
                client_secret=client_secret,
                requested_elements=FROST_TODAY_ELEMENTS,
                start_utc=today_start_utc,
                end_utc=today_end_utc,
            )
        )
        recent_elements = tuple(
            _available_elements(
                station_id,
                client_id=client_id,
                client_secret=client_secret,
                requested_elements=FROST_TREND_ELEMENTS,
                start_utc=recent_start_utc,
                end_utc=recent_end_utc,
            )
        )
    except Exception as exc:
        set_provider_runtime_error("FROST", str(exc), state)
        return None

    if not any(element in latest_elements for element in FROST_REQUIRED_ANY_ELEMENTS) and not today_elements:
        set_provider_runtime_error("FROST", (
            f"Frost no ofrece series vigentes compatibles para {station_id}."
        ), state)
        return None

    try:
        latest_payload = fetch_frost_latest(
            station_id,
            client_id=client_id,
            client_secret=client_secret,
            elements=latest_elements,
        )
        today_payload = fetch_frost_today_series(
            station_id,
            client_id=client_id,
            client_secret=client_secret,
            station_tz=station_tz,
            elements=today_elements,
        )
        recent_payload = fetch_frost_recent_series(
            station_id,
            client_id=client_id,
            client_secret=client_secret,
            station_tz=station_tz,
            days=7,
            elements=recent_elements,
        )
    except Exception as exc:
        set_provider_runtime_error("FROST", str(exc), state)
        return None

    latest_selected = _latest_selected_rows(latest_payload)
    today_series = _bin_series(today_payload, bin_seconds=600)
    recent_series = _bin_series(recent_payload, bin_seconds=3600)

    if not latest_selected and not today_series.get("has_data"):
        set_provider_runtime_error("FROST", f"Sin datos de observación para {station_id}.", state)
        return None

    current_epoch_candidates = [
        int(item.get("epoch", 0))
        for key, item in latest_selected.items()
        if key in {"temp_c", "rh", "p_abs_hpa", "wind_ms", "wind_dir_deg"}
    ]
    base_epoch = max(current_epoch_candidates) if current_epoch_candidates else 0
    if base_epoch <= 0 and today_series.get("epochs"):
        base_epoch = int(today_series["epochs"][-1])
    if base_epoch <= 0:
        base_epoch = int(datetime.now(timezone.utc).timestamp())

    temp_current = _safe_float(latest_selected.get("temp_c", {}).get("obs", {}).get("value"))
    rh_current = _safe_float(latest_selected.get("rh", {}).get("obs", {}).get("value"))
    p_abs = _safe_float(latest_selected.get("p_abs_hpa", {}).get("obs", {}).get("value"))
    wind_current = _ms_to_kmh(_safe_float(latest_selected.get("wind_ms", {}).get("obs", {}).get("value")))
    gust_current = _ms_to_kmh(_safe_float(latest_selected.get("gust_ms", {}).get("obs", {}).get("value")))
    wind_dir = _safe_float(latest_selected.get("wind_dir_deg", {}).get("obs", {}).get("value"))

    if _is_nan(temp_current):
        temp_current = _last_valid(today_series.get("temps", []))
    if _is_nan(rh_current):
        rh_current = _last_valid(today_series.get("humidities", []))
    if _is_nan(p_abs):
        p_abs = _last_valid(today_series.get("pressures_abs", []))
    if _is_nan(wind_current):
        wind_current = _last_valid(today_series.get("winds", []))
    if _is_nan(gust_current):
        gust_current = _last_valid(today_series.get("gusts", []))
    if _is_nan(wind_dir):
        wind_dir = _last_valid(today_series.get("wind_dirs", []))

    p_msl = float(p_abs) * math.exp(float(elevation or 0.0) / 8000.0) if not _is_nan(p_abs) else float("nan")
    precip_total = _precip_total(
        today_series.get("precip_accum_mm", []),
        today_series.get("precip_step_mm", []),
    )

    pressure_3h_ago, epoch_3h_ago, epoch_now_ref = _pressure_3h_reference(
        today_series.get("epochs", []),
        today_series.get("pressures_abs", []),
    )
    if epoch_now_ref is not None:
        base_epoch = int(epoch_now_ref)

    clear_provider_runtime_error("FROST", state)

    return {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_tz": station_tz,
        "lat": station_lat,
        "lon": station_lon,
        "elevation": float(elevation),
        "epoch": int(base_epoch),
        "Tc": temp_current,
        "RH": rh_current,
        "Td": float("nan"),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "pressure_3h_ago": pressure_3h_ago * math.exp(float(elevation or 0.0) / 8000.0)
        if not _is_nan(pressure_3h_ago) else float("nan"),
        "epoch_3h_ago": epoch_3h_ago,
        "wind": wind_current,
        "gust": gust_current,
        "wind_dir_deg": wind_dir,
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "temp_max": _max_valid(today_series.get("temps", [])),
        "temp_min": _min_valid(today_series.get("temps", [])),
        "rh_max": _max_valid(today_series.get("humidities", [])),
        "rh_min": _min_valid(today_series.get("humidities", [])),
        "gust_max": _max_valid(today_series.get("gusts", [])),
        "_series": {
            "epochs": list(today_series.get("epochs", [])),
            "temps": list(today_series.get("temps", [])),
            "humidities": list(today_series.get("humidities", [])),
            "pressures_abs": list(today_series.get("pressures_abs", [])),
            "winds": list(today_series.get("winds", [])),
            "gusts": list(today_series.get("gusts", [])),
            "wind_dirs": list(today_series.get("wind_dirs", [])),
            "precip_accum_mm": list(today_series.get("precip_accum_mm", [])),
            "precip_step_mm": list(today_series.get("precip_step_mm", [])),
            "solar_radiations": [float("nan")] * len(today_series.get("epochs", [])),
            "has_data": bool(today_series.get("has_data", False)),
        },
        "_series_7d": {
            "epochs": list(recent_series.get("epochs", [])),
            "temps": list(recent_series.get("temps", [])),
            "humidities": list(recent_series.get("humidities", [])),
            "pressures_abs": list(recent_series.get("pressures_abs", [])),
            "has_data": bool(recent_series.get("has_data", False)),
        },
    }

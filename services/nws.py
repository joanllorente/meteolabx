"""
Servicio para integrar observaciones de NWS (api.weather.gov).
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
import streamlit as st


BASE_URL = "https://api.weather.gov"
TIMEOUT_SECONDS = 16
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "MeteoLabX/1.0 (contact: meteolabx@gmail.com)")


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _measure_value(raw: Any) -> Tuple[float, str]:
    if isinstance(raw, dict):
        return _safe_float(raw.get("value")), str(raw.get("unitCode", "")).strip()
    return _safe_float(raw), ""


def _parse_epoch(value: Any) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
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


def _request_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }
    response = requests.get(url, params=params or {}, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _request_json_with_headers(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }
    response = requests.get(url, params=params or {}, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


@lru_cache(maxsize=2)
def _load_stations(path: str = "data_estaciones_nws.json") -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _find_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id).strip().upper()
    if not target:
        return {}
    for station in _load_stations():
        if str(station.get("id", "")).strip().upper() == target:
            return station
    return {}


def _parse_observation_feature(feature: Dict[str, Any], elevation_m: float) -> Dict[str, float]:
    if not isinstance(feature, dict):
        return {}

    props = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
    geometry = feature.get("geometry", {}) if isinstance(feature.get("geometry"), dict) else {}
    coords = geometry.get("coordinates", []) if isinstance(geometry.get("coordinates"), list) else []

    epoch = _parse_epoch(props.get("timestamp"))
    if epoch is None:
        return {}

    t_v, t_u = _measure_value(props.get("temperature"))
    td_v, td_u = _measure_value(props.get("dewpoint"))
    rh_v, _rh_u = _measure_value(props.get("relativeHumidity"))
    p_abs_v, p_abs_u = _measure_value(props.get("barometricPressure"))
    p_msl_v, p_msl_u = _measure_value(props.get("seaLevelPressure"))
    wind_v, wind_u = _measure_value(props.get("windSpeed"))
    gust_v, gust_u = _measure_value(props.get("windGust"))
    dir_v, _dir_u = _measure_value(props.get("windDirection"))
    rain_v, rain_u = _measure_value(props.get("precipitationLastHour"))
    heat_v, heat_u = _measure_value(props.get("heatIndex"))
    chill_v, chill_u = _measure_value(props.get("windChill"))

    temp_c = _to_celsius(t_v, t_u)
    dew_c = _to_celsius(td_v, td_u)
    heat_c = _to_celsius(heat_v, heat_u)
    chill_c = _to_celsius(chill_v, chill_u)
    p_abs = _to_hpa(p_abs_v, p_abs_u)
    p_msl = _to_hpa(p_msl_v, p_msl_u)

    if _is_nan(p_abs) and not _is_nan(p_msl):
        p_abs = float(p_msl) / math.exp(float(elevation_m) / 8000.0)
    if _is_nan(p_msl) and not _is_nan(p_abs):
        p_msl = float(p_abs) * math.exp(float(elevation_m) / 8000.0)

    lat = _safe_float(coords[1]) if len(coords) >= 2 else float("nan")
    lon = _safe_float(coords[0]) if len(coords) >= 2 else float("nan")

    rain_mm = _to_mm(rain_v, rain_u)

    return {
        "epoch": int(epoch),
        "lat": lat,
        "lon": lon,
        "temp_c": temp_c,
        "dewpoint_c": dew_c,
        "rh": _safe_float(rh_v),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _to_kmh(wind_v, wind_u),
        "gust_kmh": _to_kmh(gust_v, gust_u),
        "wind_dir_deg": _safe_float(dir_v),
        "precip_last_mm": max(0.0, rain_mm) if not _is_nan(rain_mm) else float("nan"),
        "heat_index_c": heat_c,
        "wind_chill_c": chill_c,
    }


def _series_from_features(features: List[Dict[str, Any]], elevation_m: float) -> Dict[str, List[float]]:
    rows: Dict[int, Dict[str, float]] = {}
    for feature in features:
        parsed = _parse_observation_feature(feature, elevation_m=elevation_m)
        if not parsed:
            continue
        rows[int(parsed["epoch"])] = parsed

    epochs = sorted(rows.keys())
    temps: List[float] = []
    rhs: List[float] = []
    p_abs: List[float] = []
    p_msl: List[float] = []
    winds: List[float] = []
    gusts: List[float] = []
    dirs: List[float] = []
    precs: List[float] = []
    lats: List[float] = []
    lons: List[float] = []

    for ep in epochs:
        row = rows[ep]
        temps.append(float(row.get("temp_c", float("nan"))))
        rhs.append(float(row.get("rh", float("nan"))))
        p_abs.append(float(row.get("p_abs_hpa", float("nan"))))
        p_msl.append(float(row.get("p_msl_hpa", float("nan"))))
        winds.append(float(row.get("wind_kmh", float("nan"))))
        gusts.append(float(row.get("gust_kmh", float("nan"))))
        dirs.append(float(row.get("wind_dir_deg", float("nan"))))
        precs.append(float(row.get("precip_last_mm", float("nan"))))
        lats.append(float(row.get("lat", float("nan"))))
        lons.append(float(row.get("lon", float("nan"))))

    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": temps,
        "humidities": rhs,
        "pressures_abs": p_abs,
        "pressures_msl": p_msl,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "precips": precs,
        "lats": lats,
        "lons": lons,
        "has_data": len(epochs) > 0,
    }


@st.cache_data(ttl=300)
def fetch_nws_latest(station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip().upper()
    if not sid:
        return {"ok": False, "error": "station_id vacio", "feature": {}}

    try:
        payload = _request_json(f"{BASE_URL}/stations/{quote(sid)}/observations/latest")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "feature": {}}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "Respuesta invalida", "feature": {}}
    return {"ok": True, "error": "", "feature": payload}


@st.cache_data(ttl=300)
def fetch_nws_observations(station_id: str, hours: int = 24, limit: int = 1200) -> Dict[str, Any]:
    sid = str(station_id).strip().upper()
    if not sid:
        return {"ok": False, "error": "station_id vacio", "features": []}

    hours_i = max(1, min(int(hours), 24 * 8))
    limit_i = max(50, min(int(limit), 3000))
    per_page = min(500, limit_i)  # weather.gov pagina y puede limitar registros por respuesta
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(hours=hours_i)
    base_url = f"{BASE_URL}/stations/{quote(sid)}/observations"
    params = {"start": _to_iso_z(start_dt), "end": _to_iso_z(end_dt), "limit": per_page}

    features: List[Dict[str, Any]] = []
    seen_ids = set()
    next_url: Optional[str] = base_url
    next_params: Optional[Dict[str, Any]] = params
    max_pages = max(2, (limit_i // per_page) + 2)

    for _ in range(max_pages):
        if not next_url or len(features) >= limit_i:
            break
        try:
            response = _request_json_with_headers(next_url, params=next_params)
            payload = response.json()
        except Exception as exc:
            # fallback sin ventana temporal si el backend rechaza start/end
            if next_url == base_url and next_params is params:
                try:
                    response = _request_json_with_headers(base_url, params={"limit": per_page})
                    payload = response.json()
                except Exception:
                    return {"ok": False, "error": str(exc), "features": features}
            else:
                return {"ok": False, "error": str(exc), "features": features}

        page_features = payload.get("features", []) if isinstance(payload, dict) else []
        if isinstance(page_features, list):
            for item in page_features:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("id", "") or item.get("@id", "")).strip()
                if fid and fid in seen_ids:
                    continue
                if fid:
                    seen_ids.add(fid)
                features.append(item)
                if len(features) >= limit_i:
                    break

        pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
        next_link = str(pagination.get("next", "")).strip() if isinstance(pagination, dict) else ""
        if not next_link:
            break

        next_url = next_link
        next_params = None

    return {"ok": len(features) > 0, "error": "" if features else "Serie vacia", "features": features[:limit_i]}


def _last_valid(values: List[float]) -> float:
    for value in reversed(values):
        v = _safe_float(value)
        if not _is_nan(v):
            return v
    return float("nan")


def _local_day_window_epoch() -> Tuple[int, int]:
    # Debe coincidir con la ventana "Hoy" usada por los gráficos en la app.
    now_local = datetime.now()
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return int(day_start.timestamp()), int(day_end.timestamp())


def _today_values(values: List[float], epochs: List[int], day_start_epoch: int, day_end_epoch: int) -> List[float]:
    out: List[float] = []
    for ep, value in zip(epochs, values):
        epoch_i = int(ep)
        if epoch_i < int(day_start_epoch) or epoch_i >= int(day_end_epoch):
            continue
        v = _safe_float(value)
        if not _is_nan(v):
            out.append(v)
    return out


def _max_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return max(clean) if clean else float("nan")


def _min_valid(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return min(clean) if clean else float("nan")


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


def is_nws_connection() -> bool:
    return str(st.session_state.get("connection_type", "")).strip().upper() == "NWS"


def get_nws_data() -> Optional[Dict[str, Any]]:
    if not is_nws_connection():
        return None

    station_id = (
        st.session_state.get("nws_station_id")
        or st.session_state.get("provider_station_id")
        or ""
    )
    station_id = str(station_id).strip().upper()
    if not station_id:
        return None

    station_meta = _find_station(station_id)
    station_name = str(station_meta.get("name", "")).strip() or station_id
    station_lat = _safe_float(station_meta.get("lat"))
    station_lon = _safe_float(station_meta.get("lon"))
    elevation = _safe_float(station_meta.get("elev"), default=0.0)
    station_tz = str(station_meta.get("tz", "")).strip()
    latest_payload = fetch_nws_latest(station_id)
    day_payload = fetch_nws_observations(station_id, hours=36, limit=1400)
    week_payload = fetch_nws_observations(station_id, hours=24 * 7, limit=3000)

    day_series = _series_from_features(day_payload.get("features", []), elevation_m=elevation)
    week_series = _series_from_features(week_payload.get("features", []), elevation_m=elevation)

    current = {}
    latest_feature = latest_payload.get("feature", {}) if isinstance(latest_payload, dict) else {}
    if isinstance(latest_feature, dict) and latest_feature:
        current = _parse_observation_feature(latest_feature, elevation_m=elevation)

    if not current and day_series.get("has_data"):
        idx = len(day_series["epochs"]) - 1
        if idx >= 0:
            current = {
                "epoch": day_series["epochs"][idx],
                "lat": day_series["lats"][idx] if idx < len(day_series["lats"]) else float("nan"),
                "lon": day_series["lons"][idx] if idx < len(day_series["lons"]) else float("nan"),
                "temp_c": day_series["temps"][idx] if idx < len(day_series["temps"]) else float("nan"),
                "rh": day_series["humidities"][idx] if idx < len(day_series["humidities"]) else float("nan"),
                "dewpoint_c": float("nan"),
                "p_abs_hpa": day_series["pressures_abs"][idx] if idx < len(day_series["pressures_abs"]) else float("nan"),
                "p_msl_hpa": day_series["pressures_msl"][idx] if idx < len(day_series["pressures_msl"]) else float("nan"),
                "wind_kmh": day_series["winds"][idx] if idx < len(day_series["winds"]) else float("nan"),
                "gust_kmh": day_series["gusts"][idx] if idx < len(day_series["gusts"]) else float("nan"),
                "wind_dir_deg": day_series["wind_dirs"][idx] if idx < len(day_series["wind_dirs"]) else float("nan"),
                "precip_last_mm": day_series["precips"][idx] if idx < len(day_series["precips"]) else float("nan"),
                "heat_index_c": float("nan"),
                "wind_chill_c": float("nan"),
            }

    if not current:
        return None

    base_lat = current.get("lat", float("nan"))
    base_lon = current.get("lon", float("nan"))
    if _is_nan(_safe_float(base_lat)):
        base_lat = station_lat
    if _is_nan(_safe_float(base_lon)):
        base_lon = station_lon

    epochs = day_series.get("epochs", [])
    temps = day_series.get("temps", [])
    rhs = day_series.get("humidities", [])
    p_abs_series = day_series.get("pressures_abs", [])
    p_msl_series = day_series.get("pressures_msl", [])
    winds = day_series.get("winds", [])
    gusts = day_series.get("gusts", [])
    dirs = day_series.get("wind_dirs", [])
    precs = day_series.get("precips", [])

    temp_current = _safe_float(current.get("temp_c"))
    if _is_nan(temp_current):
        temp_current = _last_valid(temps)
    rh_current = _safe_float(current.get("rh"))
    if _is_nan(rh_current):
        rh_current = _last_valid(rhs)
    p_abs = _safe_float(current.get("p_abs_hpa"))
    if _is_nan(p_abs):
        p_abs = _last_valid(p_abs_series)
    p_msl = _safe_float(current.get("p_msl_hpa"))
    if _is_nan(p_msl):
        p_msl = _last_valid(p_msl_series)
    if _is_nan(p_abs) and not _is_nan(p_msl):
        p_abs = float(p_msl) / math.exp(float(elevation) / 8000.0)
    if _is_nan(p_msl) and not _is_nan(p_abs):
        p_msl = float(p_abs) * math.exp(float(elevation) / 8000.0)

    day_start, day_end = _local_day_window_epoch()
    temp_today = _today_values(temps, epochs, day_start, day_end)
    rh_today = _today_values(rhs, epochs, day_start, day_end)
    gust_today = _today_values(gusts, epochs, day_start, day_end)

    precip_total = float(
        sum(
            max(0.0, _safe_float(p))
            for ep, p in zip(epochs, precs)
            if day_start <= int(ep) < day_end and not _is_nan(_safe_float(p))
        )
    )
    if precip_total <= 0.0 and not _is_nan(_safe_float(current.get("precip_last_mm"))):
        precip_total = max(0.0, _safe_float(current.get("precip_last_mm")))

    pressure_3h_ago, epoch_3h_ago, epoch_now_ref = _pressure_3h_reference(epochs, p_msl_series)
    base_epoch = int(current.get("epoch") or datetime.now(timezone.utc).timestamp())
    if epoch_now_ref is not None:
        base_epoch = int(epoch_now_ref)

    week_epochs = week_series.get("epochs", [])
    week_temps = week_series.get("temps", [])
    week_rhs = week_series.get("humidities", [])
    week_p_abs = week_series.get("pressures_abs", [])

    return {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_tz": station_tz,
        "lat": _safe_float(base_lat),
        "lon": _safe_float(base_lon),
        "elevation": float(elevation),
        "epoch": int(base_epoch),
        "Tc": temp_current,
        "RH": rh_current,
        "Td": _safe_float(current.get("dewpoint_c")),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": _safe_float(current.get("wind_kmh"), default=_last_valid(winds)),
        "gust": _safe_float(current.get("gust_kmh"), default=_last_valid(gusts)),
        "wind_dir_deg": _safe_float(current.get("wind_dir_deg"), default=_last_valid(dirs)),
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": _safe_float(current.get("heat_index_c")),
        "heat_index": _safe_float(current.get("heat_index_c")),
        "wind_chill": _safe_float(current.get("wind_chill_c")),
        "temp_max": _max_valid(temp_today),
        "temp_min": _min_valid(temp_today),
        "rh_max": _max_valid(rh_today),
        "rh_min": _min_valid(rh_today),
        "gust_max": _max_valid(gust_today),
        "_series": {
            "epochs": [int(ep) for ep in epochs],
            "temps": [float(v) for v in temps],
            "humidities": [float(v) for v in rhs],
            "pressures_abs": [float(v) for v in p_abs_series],
            "winds": [float(v) for v in winds],
            "gusts": [float(v) for v in gusts],
            "wind_dirs": [float(v) for v in dirs],
            "solar_radiations": [float("nan")] * len(epochs),
            "has_data": bool(day_series.get("has_data", False)),
        },
        "_series_7d": {
            "epochs": [int(ep) for ep in week_epochs],
            "temps": [float(v) for v in week_temps],
            "humidities": [float(v) for v in week_rhs],
            "pressures_abs": [float(v) for v in week_p_abs],
            "has_data": bool(week_series.get("has_data", False)),
        },
    }

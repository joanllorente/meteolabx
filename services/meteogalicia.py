"""
Servicio para integrar observaciones de MeteoGalicia.
"""

import json
import math
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st


BASE_URL = "https://servizos.meteogalicia.gal/mgrss/observacion"
TENMIN_ENDPOINT = f"{BASE_URL}/ultimos10minEstacionsMeteo.action"
HOURLY_ENDPOINT = f"{BASE_URL}/ultimosHorariosEstacions.action"
TIMEOUT_SECONDS = 14


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _normalize_text(value: Any) -> str:
    txt = str(value or "").strip().lower()
    txt = "".join(c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn")
    return txt


def _parse_epoch(value: Any) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            iv = int(value)
            return iv if iv > 0 else None
        except Exception:
            return None

    raw = str(value).strip()
    if not raw:
        return None

    iso_raw = raw.replace(" ", "T")
    if iso_raw.endswith("Z"):
        iso_raw = iso_raw[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue
    return None


def _request_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    headers = {"Accept": "application/json"}
    response = requests.get(url, params=params or {}, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=2)
def _load_stations(path: str = "data_estaciones_meteogalicia.json") -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []

    if isinstance(payload, dict):
        stations = payload.get("listaEstacionsMeteo", [])
    elif isinstance(payload, list):
        stations = payload
    else:
        stations = []

    return stations if isinstance(stations, list) else []


def _find_station(station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip()
    for station in _load_stations():
        if str(station.get("idEstacion", "")).strip() == sid:
            return station
    return {}


def _extract_items(payload: Any, keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _pick_station_item(items: List[Dict[str, Any]], station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip()
    if sid:
        for item in items:
            if str(item.get("idEstacion", "")).strip() == sid:
                return item
    return items[0] if items else {}


def _measure_kind_score(code_raw: str, name_raw: str) -> Tuple[str, int]:
    code = str(code_raw or "").strip().upper().replace(" ", "")
    name = _normalize_text(name_raw)

    # Wind gust first to avoid matching generic wind rules.
    if (
        (code.startswith("VV_") and "_MAX_" in code)
        or "racha" in name
        or "refacho" in name
    ):
        return "gust", 80

    if code.startswith("DV_") or ("direccion" in name and ("vento" in name or "viento" in name)):
        score = 70
        if "media" in name or "avg" in code:
            score += 5
        return "wind_dir", score

    if code.startswith("VV_") or (
        ("velocidade" in name or "velocidad" in name)
        and ("vento" in name or "viento" in name)
    ):
        score = 60
        if "_AVG_" in code or "media" in name:
            score += 8
        if "10M" in code:
            score += 2
        return "wind", score

    if code.startswith("TA_") or "temperatura" in name:
        score = 50
        if "_AVG_" in code or "media" in name or "instant" in name:
            score += 10
        if "1.5M" in code:
            score += 2
        return "temp", score

    if code.startswith("HR_") or "humidade relativa" in name or "humedad relativa" in name:
        score = 45
        if "_AVG_" in code or "media" in name:
            score += 8
        if "1.5M" in code:
            score += 2
        return "rh", score

    if code.startswith("PA_") or "presion" in name:
        score = 40
        if "_AVG_" in code or "media" in name:
            score += 8
        return "pressure", score

    if code.startswith("PP_") or "precipit" in name:
        score = 35
        if "_SUM_" in code or "acum" in name:
            score += 8
        return "precip", score

    if code.startswith("RS_") or code.startswith("SR_") or code.startswith("RG_") or "radiacion" in name:
        score = 30
        return "solar", score

    return "", -1


def _wind_to_kmh(value: float, unit: str) -> float:
    if _is_nan(value):
        return float("nan")

    unit_norm = _normalize_text(unit)
    if "m/s" in unit_norm or "m.s" in unit_norm or "m s" in unit_norm:
        return value * 3.6
    return value


def _extract_measures(lista_medidas: Any) -> Dict[str, Tuple[float, str]]:
    best: Dict[str, Tuple[int, float, str]] = {}
    if not isinstance(lista_medidas, list):
        return {}

    for measure in lista_medidas:
        if not isinstance(measure, dict):
            continue

        validation = measure.get("lnCodigoValidacion")
        try:
            validation_int = int(validation)
            if validation_int in (3, 9):
                continue
        except Exception:
            validation_int = None

        value = _safe_float(measure.get("valor"))
        if _is_nan(value) or value <= -9999:
            continue

        code = str(measure.get("codigoParametro", ""))
        name = str(measure.get("nomeParametro", ""))
        unit = str(measure.get("unidade", ""))

        kind, score = _measure_kind_score(code, name)
        if not kind:
            continue

        # Small quality bonus for validated/interpolated values.
        if validation_int in (1, 5):
            score += 1

        current = best.get(kind)
        if current is None or score >= current[0]:
            best[kind] = (score, value, unit)

    out: Dict[str, Tuple[float, str]] = {}
    for kind, (_score, value, unit) in best.items():
        if kind in ("wind", "gust"):
            out[kind] = (_wind_to_kmh(value, unit), unit)
        else:
            out[kind] = (value, unit)
    return out


@st.cache_data(ttl=300)
def fetch_meteogalicia_current(station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip()
    params = {"idEst": sid} if sid else {}
    try:
        payload = _request_json(TENMIN_ENDPOINT, params=params)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "item": {}}

    items = _extract_items(
        payload,
        keys=["listUltimos10min", "listaUltimos10min", "ultimos10min"],
    )
    item_raw = _pick_station_item(items, sid)
    if not item_raw:
        return {"ok": False, "error": "Respuesta vacia", "item": {}}

    measures = _extract_measures(item_raw.get("listaMedidas", []))
    item = dict(item_raw)
    item["_measures"] = measures
    item["_epoch"] = _parse_epoch(
        item_raw.get("instanteLecturaUTC")
        or item_raw.get("instanteUTC")
        or item_raw.get("instanteLectura")
    )
    return {"ok": True, "error": "", "item": item}


@st.cache_data(ttl=600)
def fetch_meteogalicia_hourly(station_id: str, num_hours: int = 24) -> Dict[str, Any]:
    sid = str(station_id).strip()
    nh = max(1, min(72, int(num_hours)))
    params = {"idEst": sid, "numHoras": nh} if sid else {"numHoras": nh}

    try:
        payload = _request_json(HOURLY_ENDPOINT, params=params)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "series": {}}

    station_blocks = _extract_items(payload, keys=["listHorarios", "listaHorarios", "horarios"])
    block = _pick_station_item(station_blocks, sid)
    instants = block.get("listaInstantes", []) if isinstance(block, dict) else []

    rows_by_epoch: Dict[int, Dict[str, float]] = {}
    for instant in instants if isinstance(instants, list) else []:
        if not isinstance(instant, dict):
            continue

        epoch = _parse_epoch(
            instant.get("instanteLecturaUTC")
            or instant.get("instanteUTC")
            or instant.get("instanteLectura")
        )
        if epoch is None:
            continue

        extracted = _extract_measures(instant.get("listaMedidas", []))
        if not extracted:
            continue

        row = rows_by_epoch.get(epoch)
        if row is None:
            row = {
                "temp": float("nan"),
                "rh": float("nan"),
                "pressure": float("nan"),
                "wind": float("nan"),
                "gust": float("nan"),
                "wind_dir": float("nan"),
                "precip": float("nan"),
                "solar": float("nan"),
            }

        for kind in row.keys():
            if kind in extracted:
                row[kind] = extracted[kind][0]

        rows_by_epoch[epoch] = row

    epochs = sorted(rows_by_epoch.keys())
    temps: List[float] = []
    rhs: List[float] = []
    pressures: List[float] = []
    winds: List[float] = []
    gusts: List[float] = []
    dirs: List[float] = []
    precips: List[float] = []
    solars: List[float] = []

    for ep in epochs:
        row = rows_by_epoch[ep]
        temps.append(float(row["temp"]))
        rhs.append(float(row["rh"]))
        pressures.append(float(row["pressure"]))
        winds.append(float(row["wind"]))
        gusts.append(float(row["gust"]))
        dirs.append(float(row["wind_dir"]))
        precips.append(float(row["precip"]))
        solars.append(float(row["solar"]))

    return {
        "ok": len(epochs) > 0,
        "error": "" if len(epochs) > 0 else "Serie horaria vacia",
        "series": {
            "epochs": epochs,
            "temps": temps,
            "humidities": rhs,
            "pressures": pressures,
            "winds": winds,
            "gusts": gusts,
            "wind_dirs": dirs,
            "precips": precips,
            "solar_radiations": solars,
            "has_data": len(epochs) > 0,
        },
    }


def _last_valid(values: List[float]) -> float:
    for value in reversed(values):
        fv = _safe_float(value)
        if not _is_nan(fv):
            return fv
    return float("nan")


def _max_valid(values: List[float]) -> float:
    cleaned = [_safe_float(v) for v in values]
    cleaned = [v for v in cleaned if not _is_nan(v)]
    return max(cleaned) if cleaned else float("nan")


def _min_valid(values: List[float]) -> float:
    cleaned = [_safe_float(v) for v in values]
    cleaned = [v for v in cleaned if not _is_nan(v)]
    return min(cleaned) if cleaned else float("nan")


def _today_start_epoch_local() -> int:
    now_local = datetime.now()
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _precip_today_mm(epochs: List[int], precips: List[float]) -> float:
    day_start = _today_start_epoch_local()
    values = []
    for ep, p in zip(epochs, precips):
        if int(ep) < day_start:
            continue
        fp = _safe_float(p)
        if _is_nan(fp):
            continue
        values.append(max(0.0, fp))
    return float(sum(values)) if values else float("nan")


def _series_today(values: List[float], epochs: List[int]) -> List[float]:
    day_start = _today_start_epoch_local()
    out: List[float] = []
    for ep, value in zip(epochs, values):
        if int(ep) < day_start:
            continue
        fv = _safe_float(value)
        if not _is_nan(fv):
            out.append(fv)
    return out


def is_meteogalicia_connection() -> bool:
    return str(st.session_state.get("connection_type", "")).strip().upper() == "METEOGALICIA"


def get_meteogalicia_data() -> Optional[Dict[str, Any]]:
    if not is_meteogalicia_connection():
        return None

    station_id = (
        st.session_state.get("meteogalicia_station_id")
        or st.session_state.get("provider_station_id")
        or ""
    )
    station_id = str(station_id).strip()
    if not station_id:
        return None

    current_payload = fetch_meteogalicia_current(station_id)
    hourly_payload = fetch_meteogalicia_hourly(station_id, num_hours=24)

    current_item = current_payload.get("item", {}) if isinstance(current_payload, dict) else {}
    hourly_series = hourly_payload.get("series", {}) if isinstance(hourly_payload, dict) else {}

    epochs = hourly_series.get("epochs", []) if isinstance(hourly_series, dict) else []
    temps = hourly_series.get("temps", []) if isinstance(hourly_series, dict) else []
    rhs = hourly_series.get("humidities", []) if isinstance(hourly_series, dict) else []
    pressures = hourly_series.get("pressures", []) if isinstance(hourly_series, dict) else []
    winds = hourly_series.get("winds", []) if isinstance(hourly_series, dict) else []
    gusts = hourly_series.get("gusts", []) if isinstance(hourly_series, dict) else []
    dirs = hourly_series.get("wind_dirs", []) if isinstance(hourly_series, dict) else []
    precips = hourly_series.get("precips", []) if isinstance(hourly_series, dict) else []
    solars = hourly_series.get("solar_radiations", []) if isinstance(hourly_series, dict) else []

    if not current_item and len(epochs) == 0:
        return None

    station_meta = _find_station(station_id)

    lat = _safe_float(station_meta.get("lat"), default=float("nan"))
    lon = _safe_float(station_meta.get("lon"), default=float("nan"))
    elevation = _safe_float(station_meta.get("altitude"), default=0.0)

    epoch = _parse_epoch(current_item.get("_epoch"))
    if epoch is None and epochs:
        epoch = int(epochs[-1])
    if epoch is None:
        epoch = int(datetime.now(timezone.utc).timestamp())

    current_measures = current_item.get("_measures", {}) if isinstance(current_item, dict) else {}
    temp_current = _safe_float(
        current_measures.get("temp", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(temp_current) and temps:
        temp_current = _last_valid(temps)

    rh_current = _safe_float(
        current_measures.get("rh", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(rh_current):
        rh_current = _last_valid(rhs) if rhs else float("nan")

    p_abs = _safe_float(
        current_measures.get("pressure", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(p_abs):
        p_abs = _last_valid(pressures) if pressures else float("nan")
    if not _is_nan(p_abs):
        p_msl = p_abs * math.exp(float(elevation) / 8000.0)
    else:
        p_msl = float("nan")

    wind_now = _safe_float(
        current_measures.get("wind", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(wind_now):
        wind_now = _last_valid(winds) if winds else float("nan")

    gust_now = _safe_float(
        current_measures.get("gust", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(gust_now):
        gust_now = _last_valid(gusts) if gusts else float("nan")

    wind_dir_now = _safe_float(
        current_measures.get("wind_dir", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(wind_dir_now):
        wind_dir_now = _last_valid(dirs) if dirs else float("nan")

    solar_now = _safe_float(
        current_measures.get("solar", (float("nan"), ""))[0],
        default=float("nan"),
    )
    if _is_nan(solar_now):
        solar_now = _last_valid(solars) if solars else float("nan")

    precip_total = _precip_today_mm(epochs, precips)
    if _is_nan(precip_total):
        precip_10min = _safe_float(
            current_measures.get("precip", (float("nan"), ""))[0],
            default=float("nan"),
        )
        precip_total = max(0.0, precip_10min) if not _is_nan(precip_10min) else float("nan")

    temp_today = _series_today(temps, epochs)
    rh_today = _series_today(rhs, epochs)
    gust_today = _series_today(gusts, epochs)

    temp_max = _max_valid(temp_today)
    temp_min = _min_valid(temp_today)
    rh_max = _max_valid(rh_today)
    rh_min = _min_valid(rh_today)
    gust_max = _max_valid(gust_today)

    # Pressure trend reference around T-3h in MSL units.
    pressure_3h_ago = float("nan")
    epoch_3h_ago = None
    valid_press = [
        (int(ep), float(p))
        for ep, p in zip(epochs, pressures)
        if not _is_nan(_safe_float(p))
    ]
    if len(valid_press) >= 2:
        valid_press.sort(key=lambda item: item[0])
        ep_now, p_abs_now = valid_press[-1]
        target_ep = ep_now - (3 * 3600)
        ep_old, p_abs_old = min(valid_press, key=lambda item: abs(item[0] - target_ep))
        epoch = ep_now
        p_abs = p_abs_now
        p_msl = p_abs_now * math.exp(float(elevation) / 8000.0)
        pressure_3h_ago = p_abs_old * math.exp(float(elevation) / 8000.0)
        epoch_3h_ago = ep_old

    feels_like = float("nan")

    return {
        "idema": station_id,
        "station_code": station_id,
        "station_name": current_item.get("estacion") or station_meta.get("estacion") or station_id,
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "epoch": int(epoch),
        "Tc": temp_current,
        "RH": rh_current,
        "Td": float("nan"),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": wind_now,
        "gust": gust_now,
        "wind_dir_deg": wind_dir_now,
        "precip_total": precip_total,
        "solar_radiation": solar_now,
        "uv": float("nan"),
        "feels_like": feels_like,
        "heat_index": float("nan"),
        "temp_max": temp_max,
        "temp_min": temp_min,
        "rh_max": rh_max,
        "rh_min": rh_min,
        "gust_max": gust_max,
        "_series": {
            "epochs": [int(ep) for ep in epochs],
            "temps": [float(v) for v in temps],
            "humidities": [float(v) for v in rhs],
            "pressures_abs": [float(v) for v in pressures],
            "winds": [float(v) for v in winds],
            "gusts": [float(v) for v in gusts],
            "wind_dirs": [float(v) for v in dirs],
            "solar_radiations": [float(v) for v in solars],
            "has_data": bool(hourly_series.get("has_data", False)),
        },
    }

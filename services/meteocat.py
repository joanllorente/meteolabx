"""
Servicio para interactuar con Meteocat (XEMA).
"""
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from data_files import METEOCAT_STATIONS_PATH
from utils.provider_state import get_connected_provider_station_id, get_provider_station_id, is_provider_connection, resolve_state


METEOCAT_API_KEY = os.getenv(
    "METEOCAT_API_KEY",
    "rZwBPl5kv05CS7NEgk9wcaqd0FFimA2f9y6ISDa2",
)

BASE_URL = "https://api.meteo.cat/xema/v1"
TIMEOUT_SECONDS = 14
CAT_TZ = ZoneInfo("Europe/Madrid")
METEOCAT_SERIES_CACHE_VERSION = 6


# Variables de interés.
V_TEMP = 32
V_RH = 33
V_PRESSURE = 34
V_PRECIP = 35
V_SOLAR = 36
V_UV = 39
V_TEMP_MAX_AIR = 40
V_TEMP_MIN_AIR = 42
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
    "precip_total": [V_PRECIP],
    "solar": [V_SOLAR],
    "uv": [V_UV],
    "wind": [V_WIND, 20],
    "wind_dir": [V_WIND_DIR, 21],
    "gust": [V_GUST],
    "gust_dir": [V_GUST_DIR],
}

# Códigos para el endpoint /variables/estadistics/diaris/{codiVariable}
# (distintos de los códigos de medición en tiempo real).
# Fuente: GET .../variables/estadistics/diaris/metadades
STAT_TEMP_MEAN     = 1000   # Temperatura mitjana diària
STAT_TEMP_MAX      = 1001   # Temperatura màxima diària + hora
STAT_TEMP_MIN      = 1002   # Temperatura mínima diària + hora
STAT_PRECIP        = 1300   # Precipitació acumulada diària
STAT_WIND_MEAN_10  = 1503   # Vel. mitjana diària vent 10 m (esc.)
STAT_WIND_MEAN_6   = 1504   # Vel. mitjana diària vent 6 m (esc.)
STAT_WIND_MEAN_2   = 1505   # Vel. mitjana diària vent 2 m (esc.)
STAT_GUST_MAX_10   = 1512   # Ratxa màxima diària vent 10 m + hora
STAT_GUST_MAX_6    = 1513   # Ratxa màxima diària vent 6 m + hora
STAT_GUST_MAX_2    = 1514   # Ratxa màxima diària vent 2 m + hora

METEOCAT_CLIMO_STAT_CODES = {
    "temp_mean": [STAT_TEMP_MEAN],
    "temp_max": [STAT_TEMP_MAX],
    "temp_min": [STAT_TEMP_MIN],
    "wind_mean": [STAT_WIND_MEAN_2, STAT_WIND_MEAN_6, STAT_WIND_MEAN_10],
    "gust_max": [STAT_GUST_MAX_2, STAT_GUST_MAX_6, STAT_GUST_MAX_10],
    "precip_total": [STAT_PRECIP],
}

# Códigos para el endpoint /variables/estadistics/anuals/{codiVariable}
# Fuente: GET .../variables/estadistics/anuals/metadades
STAT_AN_TEMP_MEAN      = 3000   # Temperatura mitjana anual
STAT_AN_TEMP_ABS_MAX   = 3001   # Temperatura màxima abs. anual + data
STAT_AN_TEMP_ABS_MIN   = 3002   # Temperatura mínima abs. anual + data
STAT_AN_TEMP_MAX_MEAN  = 3003   # Temperatura màxima mitjana anual
STAT_AN_TEMP_MIN_MEAN  = 3004   # Temperatura mínima mitjana anual
STAT_AN_PRECIP_TOTAL   = 3300   # Precipitació acumulada anual
STAT_AN_PRECIP_MAX_24H = 3303   # Precipitació màx. en 24 h (anual) + data
STAT_AN_RAIN_DAYS      = 3305   # Núm. anual de dies de precipitació > 0 mm
STAT_AN_SOLAR_MEAN     = 3400   # Mitj. anual d'irradiació solar global diària
STAT_AN_WIND_MEAN_10   = 3503   # Vel. mitjana anual del vent 10 m
STAT_AN_WIND_MEAN_6    = 3504   # Vel. mitjana anual del vent 6 m
STAT_AN_WIND_MEAN_2    = 3505   # Vel. mitjana anual del vent 2 m
STAT_AN_GUST_MAX_10    = 3512   # Ratxa màxima abs. anual vent 10 m + data
STAT_AN_GUST_MAX_6     = 3513   # Ratxa màxima abs. anual vent 6 m + data
STAT_AN_GUST_MAX_2     = 3514   # Ratxa màxima abs. anual vent 2 m + data

METEOCAT_ANNUAL_CLIMO_CODES = {
    "temp_mean": [STAT_AN_TEMP_MEAN],
    "temp_max": [STAT_AN_TEMP_MAX_MEAN],
    "temp_min": [STAT_AN_TEMP_MIN_MEAN],
    "wind_mean": [STAT_AN_WIND_MEAN_2, STAT_AN_WIND_MEAN_6, STAT_AN_WIND_MEAN_10],
    "gust_max": [STAT_AN_GUST_MAX_2, STAT_AN_GUST_MAX_6, STAT_AN_GUST_MAX_10],
    "precip_total": [STAT_AN_PRECIP_TOTAL],
    "solar_mean": [STAT_AN_SOLAR_MEAN],
    "precip_max_24h": [STAT_AN_PRECIP_MAX_24H],
    "rain_days": [STAT_AN_RAIN_DAYS],
    "temp_abs_max": [STAT_AN_TEMP_ABS_MAX],
    "temp_abs_min": [STAT_AN_TEMP_ABS_MIN],
}

# Códigos para el endpoint /variables/estadistics/mensuals/{codiVariable}
# Fuente: GET .../variables/estadistics/mensuals/metadades
STAT_MO_TEMP_MEAN      = 2000   # Temperatura mitjana mensual
STAT_MO_TEMP_ABS_MAX   = 2001   # Temperatura màxima absoluta mensual + data
STAT_MO_TEMP_ABS_MIN   = 2002   # Temperatura mínima absoluta mensual + data
STAT_MO_TEMP_MAX_MEAN  = 2003   # Temperatura màxima mitjana mensual
STAT_MO_TEMP_MIN_MEAN  = 2004   # Temperatura mínima mitjana mensual
STAT_MO_PRECIP_TOTAL   = 2300   # Precipitació acumulada mensual
STAT_MO_PRECIP_MAX_24H = 2303   # Precipitació màxima en 24 h (mensual) + data
STAT_MO_RAIN_DAYS      = 2305   # Núm. mensual de dies de precipitació > 0 mm
STAT_MO_SOLAR_MEAN     = 2400   # Mitj. mensual irradiació solar global diària
STAT_MO_WIND_MEAN_10   = 2503   # Velocitat mitjana mensual de vent 10 m
STAT_MO_WIND_MEAN_6    = 2504   # Velocitat mitjana mensual de vent 6 m
STAT_MO_WIND_MEAN_2    = 2505   # Velocitat mitjana mensual de vent 2 m
STAT_MO_GUST_MAX_10    = 2512   # Ratxa màxima abs. mensual de vent 10 m + data
STAT_MO_GUST_MAX_6     = 2513   # Ratxa màxima abs. mensual de vent 6 m + data
STAT_MO_GUST_MAX_2     = 2514   # Ratxa màxima abs. mensual de vent 2 m + data

METEOCAT_MONTHLY_CLIMO_CODES = {
    "temp_mean": [STAT_MO_TEMP_MEAN],
    "temp_max": [STAT_MO_TEMP_MAX_MEAN],
    "temp_min": [STAT_MO_TEMP_MIN_MEAN],
    "wind_mean": [STAT_MO_WIND_MEAN_2, STAT_MO_WIND_MEAN_6, STAT_MO_WIND_MEAN_10],
    "gust_max": [STAT_MO_GUST_MAX_2, STAT_MO_GUST_MAX_6, STAT_MO_GUST_MAX_10],
    "precip_total": [STAT_MO_PRECIP_TOTAL],
    "solar_mean": [STAT_MO_SOLAR_MEAN],
    "precip_max_24h": [STAT_MO_PRECIP_MAX_24H],
    "rain_days": [STAT_MO_RAIN_DAYS],
    "temp_abs_max": [STAT_MO_TEMP_ABS_MAX],
    "temp_abs_min": [STAT_MO_TEMP_ABS_MIN],
}

CLIMO_DAILY_SCHEMA = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "gust_max",
    "precip_total",
]

CLIMO_ANNUAL_EXTRA_SCHEMA = [
    "solar_mean",
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_max_date",
    "temp_abs_min",
    "temp_abs_min_date",
    "gust_abs_max_date",
    "precip_max_24h_date",
]

CLIMO_WIND_METRICS = {"wind_mean", "gust_max"}
CLIMO_DATE_COLUMN_BY_METRIC = {
    "temp_abs_max": "temp_abs_max_date",
    "temp_abs_min": "temp_abs_min_date",
    "gust_max": "gust_abs_max_date",
    "precip_max_24h": "precip_max_24h_date",
}
CLIMO_NUMERIC_COLUMNS = [
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "gust_max",
    "precip_total",
    "solar_mean",
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_min",
]


def _empty_climo_row(date_label: str, epoch: float) -> Dict[str, Any]:
    return {
        "date": date_label,
        "epoch": epoch,
        "temp_mean": float("nan"),
        "temp_max": float("nan"),
        "temp_min": float("nan"),
        "wind_mean": float("nan"),
        "gust_max": float("nan"),
        "precip_total": float("nan"),
        "solar_mean": float("nan"),
        "precip_max_24h": float("nan"),
        "rain_days": float("nan"),
        "temp_abs_max": float("nan"),
        "temp_abs_max_date": None,
        "temp_abs_min": float("nan"),
        "temp_abs_min_date": None,
        "gust_abs_max_date": None,
        "precip_max_24h_date": None,
    }


def _finalize_climo_frame(frame: pd.DataFrame, *, fill_temp_mean: bool = True) -> pd.DataFrame:
    for col in CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")

    for col in CLIMO_NUMERIC_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    if fill_temp_mean:
        missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
        if missing_mean.any():
            frame.loc[missing_mean, "temp_mean"] = (
                frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]
            ) / 2.0

    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA]


def _climo_epoch_from_label(date_label: str) -> float:
    try:
        return float(datetime.fromisoformat(str(date_label)).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return float("nan")


def _build_climo_rows(date_labels: List[str]) -> Dict[Any, Dict[str, Any]]:
    return {
        str(date_label): _empty_climo_row(str(date_label), _climo_epoch_from_label(str(date_label)))
        for date_label in date_labels
    }


def _metric_value_available(item: Any) -> bool:
    return isinstance(item, dict) and (not _is_nan(_safe_float(item.get("value"))))


def _select_metric_candidate_code(candidate_codes: List[int], has_data_for_code) -> Optional[int]:
    normalized = [int(code) for code in candidate_codes]
    if not normalized:
        return None
    chosen_code = int(normalized[0])
    for candidate_code in normalized:
        try:
            if has_data_for_code(int(candidate_code)):
                chosen_code = int(candidate_code)
                break
        except Exception:
            continue
    return chosen_code


def _apply_climo_metric_value(row: Dict[str, Any], metric_name: str, payload: Dict[str, Any]) -> None:
    value = _safe_float(payload.get("value"))
    if _is_nan(value):
        return
    if metric_name in CLIMO_WIND_METRICS:
        value = _ms_to_kmh(value)
    row[metric_name] = float(value)
    date_col = CLIMO_DATE_COLUMN_BY_METRIC.get(metric_name)
    if date_col:
        row[date_col] = _parse_stats_date(payload.get("date"))


def _finalize_climo_rows(rows_by_key: Dict[Any, Dict[str, Any]], *, fill_temp_mean: bool = True) -> pd.DataFrame:
    if not rows_by_key:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)
    frame = pd.DataFrame(rows_by_key.values())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    return _finalize_climo_frame(frame, fill_temp_mean=fill_temp_mean)


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


def _parse_measurement_epoch(value: Any) -> Optional[int]:
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
            dt = dt.replace(tzinfo=CAT_TZ)
        return int(dt.timestamp())
    except Exception:
        return None


def _ms_to_kmh(value: float) -> float:
    return float("nan") if _is_nan(value) else value * 3.6


def _non_negative(value: float) -> float:
    if _is_nan(value):
        return float("nan")
    return max(0.0, float(value))


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_abs_hpa):
        return float("nan")
    try:
        return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)
    except Exception:
        return float("nan")


@lru_cache(maxsize=2)
def _load_stations(path: str = str(METEOCAT_STATIONS_PATH)):
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


@lru_cache(maxsize=512)
def get_meteocat_station_series_start_date(station_code: str) -> Optional[str]:
    """
    Devuelve la fecha de inicio de serie (YYYY-MM-DD) desde estats.dataInici.
    """
    station = _find_station(station_code)
    if not isinstance(station, dict) or not station:
        return None

    statuses = station.get("estats", [])
    earliest_epoch: Optional[int] = None
    fallback_dates: List[str] = []

    for status in statuses if isinstance(statuses, list) else []:
        if not isinstance(status, dict):
            continue
        raw = str(status.get("dataInici", "")).strip()
        if not raw:
            continue
        epoch = _parse_iso_epoch(raw)
        if epoch is None:
            candidate = raw.split("T", 1)[0]
            if candidate:
                fallback_dates.append(candidate)
            continue
        if earliest_epoch is None or epoch < earliest_epoch:
            earliest_epoch = int(epoch)

    if earliest_epoch is not None:
        return datetime.fromtimestamp(earliest_epoch, tz=timezone.utc).strftime("%Y-%m-%d")

    if fallback_dates:
        return min(fallback_dates)

    return None


def _iter_months(start_date: date, end_date: date):
    cursor = date(int(start_date.year), int(start_date.month), 1)
    limit = date(int(end_date.year), int(end_date.month), 1)
    while cursor <= limit:
        yield cursor.year, cursor.month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _parse_stats_date(raw_value: Any) -> Optional[str]:
    if isinstance(raw_value, dict):
        for key in ("data", "date", "valorData", "dataValor"):
            if key in raw_value:
                nested = _parse_stats_date(raw_value.get(key))
                if nested:
                    return nested
        return None
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if len(raw) == 4 and raw.isdigit():
        return f"{raw}-01-01"
    if raw.endswith("Z"):
        raw = raw[:-1]
    base = raw.split("T", 1)[0]
    try:
        return datetime.fromisoformat(base).strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_stats_year(raw_value: Any) -> Optional[int]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    if raw.endswith("Z"):
        raw = raw.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(raw).year)
    except Exception:
        return None


def _parse_stats_month(raw_value: Any) -> Optional[str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    if len(raw) >= 7 and raw[4] == "-" and raw[:4].isdigit() and raw[5:7].isdigit():
        return f"{raw[:4]}-{raw[5:7]}-01"
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-01")
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_meteocat_daily_stats_month(
    station_code: str,
    variable_code: int,
    year: int,
    month: int,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    if not code or not key:
        return {"ok": False, "error": "Falta station_code o API key", "data": {}}

    endpoint = f"{BASE_URL}/variables/estadistics/diaris/{int(variable_code)}"
    try:
        payload = _request_json(
            endpoint,
            key,
            params={
                "codiEstacio": code,
                "any": f"{int(year):04d}",
                "mes": f"{int(month):02d}",
            },
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    # Respuesta: {"codiEstacio": "XX", "codiVariable": N, "valors": [{data, valor, percentatge}, ...]}
    valors: List[Any] = []
    if isinstance(payload, dict):
        v = payload.get("valors", [])
        if isinstance(v, list):
            valors = v

    result: Dict[str, float] = {}
    for item in valors:
        if not isinstance(item, dict):
            continue
        day_txt = _parse_stats_date(item.get("data"))
        if not day_txt:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        result[day_txt] = float(value)

    return {"ok": True, "error": "", "data": result}


def _fetch_stats_candidates_month(
    station_code: str,
    variable_codes: List[int],
    year: int,
    month: int,
    api_key: Optional[str] = None,
) -> Tuple[Dict[str, float], Optional[int]]:
    for var_code in variable_codes:
        payload = fetch_meteocat_daily_stats_month(
            station_code=station_code,
            variable_code=int(var_code),
            year=int(year),
            month=int(month),
            api_key=api_key,
        )
        if payload.get("ok") and isinstance(payload.get("data"), dict) and payload.get("data"):
            return payload["data"], int(var_code)
    return {}, None


def fetch_meteocat_daily_history_for_periods(
    station_code: str,
    periods: List[Any],
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA)

    start = min(getattr(period, "start") for period in periods)
    end = max(getattr(period, "end") for period in periods)

    rows_by_day: Dict[str, Dict[str, float]] = {}
    wind_metrics = {"wind_mean", "gust_max"}

    for yy, mm in _iter_months(start, end):
        month_data: Dict[str, Dict[str, float]] = {}
        for metric_name, candidates in METEOCAT_CLIMO_STAT_CODES.items():
            values, _ = _fetch_stats_candidates_month(
                station_code=code,
                variable_codes=[int(c) for c in candidates],
                year=int(yy),
                month=int(mm),
                api_key=api_key,
            )
            month_data[metric_name] = values

        for metric_name, day_values in month_data.items():
            for day_txt, raw_value in day_values.items():
                if day_txt not in rows_by_day:
                    rows_by_day[day_txt] = {
                        "date": day_txt,
                        "epoch": float("nan"),
                        "temp_mean": float("nan"),
                        "temp_max": float("nan"),
                        "temp_min": float("nan"),
                        "wind_mean": float("nan"),
                        "gust_max": float("nan"),
                        "precip_total": float("nan"),
                    }

                value = float(raw_value)
                if metric_name in wind_metrics and not _is_nan(value):
                    value = _ms_to_kmh(value)
                rows_by_day[day_txt][metric_name] = value

                try:
                    epoch = int(datetime.fromisoformat(day_txt).replace(tzinfo=timezone.utc).timestamp())
                    rows_by_day[day_txt]["epoch"] = float(epoch)
                except Exception:
                    pass

    if not rows_by_day:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA)

    frame = pd.DataFrame(rows_by_day.values())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()

    for col in CLIMO_DAILY_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")

    numeric_cols = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total"]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
    if missing_mean.any():
        frame.loc[missing_mean, "temp_mean"] = (frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]) / 2.0

    frame["precip_total"] = frame["precip_total"].clip(lower=0)

    frame = frame.sort_values("date").reset_index(drop=True)
    mask = frame["date"].between(pd.to_datetime(start), pd.to_datetime(end))
    frame = frame.loc[mask].copy()

    return frame[CLIMO_DAILY_SCHEMA]


def _collect_annual_stat_entries(payload: Any, variable_code: int) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            entries.extend(_collect_annual_stat_entries(item, variable_code))
        return entries
    if not isinstance(payload, dict):
        return entries

    direct_vals = payload.get("valors")
    if isinstance(direct_vals, list):
        entries.extend(item for item in direct_vals if isinstance(item, dict))

    direct_stats = payload.get("estadistics")
    if isinstance(direct_stats, list):
        entries.extend(item for item in direct_stats if isinstance(item, dict))

    variables = payload.get("variables")
    if isinstance(variables, list):
        for var in variables:
            if not isinstance(var, dict):
                continue
            try:
                codi = int(var.get("codi"))
            except Exception:
                codi = None
            if codi is not None and int(codi) != int(variable_code):
                continue
            stats = var.get("estadistics", [])
            if isinstance(stats, list):
                entries.extend(item for item in stats if isinstance(item, dict))

    return entries


def _extract_annual_item_date(item: Dict[str, Any]) -> Optional[str]:
    for key in (
        "data",
        "date",
        "dataExtrem",
        "data_extrem",
        "dataMax",
        "dataMin",
        "dataValor",
        "valorData",
    ):
        if key in item:
            parsed = _parse_stats_date(item.get(key))
            if parsed:
                return parsed
    return None


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_meteocat_annual_stats_series(
    station_code: str,
    variable_code: int,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    if not code or not key:
        return {"ok": False, "error": "Falta station_code o API key", "data": {}}

    endpoint = f"{BASE_URL}/variables/estadistics/anuals/{int(variable_code)}"
    try:
        payload = _request_json(
            endpoint,
            key,
            params={
                "codiEstacio": code,
            },
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    entries = _collect_annual_stat_entries(payload, int(variable_code))
    if not entries:
        return {"ok": True, "error": "", "data": {}}

    per_year: Dict[int, Dict[str, Any]] = {}
    for item in entries:
        candidate_year = _parse_stats_year(item.get("any"))
        if candidate_year is None:
            candidate_year = _parse_stats_year(item.get("data"))
        if candidate_year is None:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        per_year[int(candidate_year)] = {
            "year": int(candidate_year),
            "value": float(value),
            "date": _extract_annual_item_date(item),
        }

    return {"ok": True, "error": "", "data": per_year}


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_meteocat_monthly_stats_year(
    station_code: str,
    variable_code: int,
    year: int,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    if not code or not key:
        return {"ok": False, "error": "Falta station_code o API key", "data": {}}

    endpoint = f"{BASE_URL}/variables/estadistics/mensuals/{int(variable_code)}"
    try:
        payload = _request_json(
            endpoint,
            key,
            params={
                "codiEstacio": code,
                "any": f"{int(year):04d}",
            },
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    entries = _collect_annual_stat_entries(payload, int(variable_code))
    if not entries:
        return {"ok": True, "error": "", "data": {}}

    per_month: Dict[str, Dict[str, Any]] = {}
    for item in entries:
        month_key = _parse_stats_month(item.get("data"))
        if not month_key:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        per_month[month_key] = {
            "month": month_key,
            "value": float(value),
            "date": _extract_annual_item_date(item),
        }

    return {"ok": True, "error": "", "data": per_month}


def fetch_meteocat_monthly_history_for_year(
    station_code: str,
    year: int,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    yy = int(year)
    if not code:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)

    rows_by_month = _build_climo_rows([f"{yy:04d}-{mm:02d}-01" for mm in range(1, 13)])

    monthly_payload_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}

    def _fetch_cached_series(var_code: int) -> Dict[str, Dict[str, Any]]:
        cache_key = int(var_code)
        cached = monthly_payload_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = fetch_meteocat_monthly_stats_year(
            station_code=code,
            variable_code=int(var_code),
            year=yy,
            api_key=api_key,
        )
        data = payload.get("data")
        out = data if isinstance(data, dict) else {}
        monthly_payload_cache[cache_key] = out
        return out

    for metric_name, candidates in METEOCAT_MONTHLY_CLIMO_CODES.items():
        chosen_code = _select_metric_candidate_code(
            [int(c) for c in candidates],
            lambda var_code: any(
                (month_key in rows_by_month) and _metric_value_available(item)
                for month_key, item in _fetch_cached_series(int(var_code)).items()
            ),
        )
        if chosen_code is None:
            continue

        chosen_series = _fetch_cached_series(chosen_code)
        for month_key, data in chosen_series.items():
            if month_key not in rows_by_month or not isinstance(data, dict):
                continue
            _apply_climo_metric_value(rows_by_month[month_key], metric_name, data)

    return _finalize_climo_rows(rows_by_month)


def fetch_meteocat_monthly_history_for_periods(
    station_code: str,
    periods: List[Any],
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)

    requested_months: Dict[str, Tuple[int, int]] = {}
    for period in periods:
        yy = int(getattr(period, "start").year)
        mm = int(getattr(period, "start").month)
        key = f"{yy:04d}-{mm:02d}-01"
        requested_months[key] = (yy, mm)

    rows_by_month = _build_climo_rows(list(requested_months.keys()))

    monthly_payload_cache: Dict[Tuple[int, int], Dict[str, Dict[str, Any]]] = {}

    def _fetch_cached_series(var_code: int, year: int) -> Dict[str, Dict[str, Any]]:
        cache_key = (int(var_code), int(year))
        cached = monthly_payload_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = fetch_meteocat_monthly_stats_year(
            station_code=code,
            variable_code=int(var_code),
            year=int(year),
            api_key=api_key,
        )
        data = payload.get("data")
        out = data if isinstance(data, dict) else {}
        monthly_payload_cache[cache_key] = out
        return out

    years = sorted({int(year) for year, _ in requested_months.values()})
    for metric_name, candidates in METEOCAT_MONTHLY_CLIMO_CODES.items():
        chosen_code = _select_metric_candidate_code(
            [int(c) for c in candidates],
            lambda var_code: any(
                (month_key in rows_by_month) and _metric_value_available(item)
                for yy in years
                for month_key, item in _fetch_cached_series(int(var_code), yy).items()
            ),
        )
        if chosen_code is None:
            continue

        for yy in years:
            chosen_series = _fetch_cached_series(chosen_code, yy)
            for month_key, data in chosen_series.items():
                if month_key not in rows_by_month or not isinstance(data, dict):
                    continue
                _apply_climo_metric_value(rows_by_month[month_key], metric_name, data)

    return _finalize_climo_rows(rows_by_month)


def _fetch_daily_metric_for_months(
    station_code: str,
    variable_codes: List[int],
    year: int,
    months: List[int],
    api_key: Optional[str] = None,
) -> Dict[str, float]:
    values_by_day: Dict[str, float] = {}
    chosen_code: Optional[int] = None
    candidate_codes = [int(code) for code in variable_codes]

    for month in months:
        if chosen_code is not None:
            payload = fetch_meteocat_daily_stats_month(
                station_code=station_code,
                variable_code=int(chosen_code),
                year=int(year),
                month=int(month),
                api_key=api_key,
            )
            data = payload.get("data")
            if isinstance(data, dict) and data:
                values_by_day.update({k: float(v) for k, v in data.items()})
                continue

        for code_candidate in candidate_codes:
            payload = fetch_meteocat_daily_stats_month(
                station_code=station_code,
                variable_code=int(code_candidate),
                year=int(year),
                month=int(month),
                api_key=api_key,
            )
            data = payload.get("data")
            if isinstance(data, dict) and data:
                values_by_day.update({k: float(v) for k, v in data.items()})
                chosen_code = int(code_candidate)
                break

    return values_by_day


def fetch_meteocat_daily_extremes_for_year(
    station_code: str,
    year: int,
    api_key: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    code = str(station_code).strip().upper()
    yy = int(year)
    if not code:
        return {}

    result: Dict[str, Dict[str, str]] = {}

    # Mínima de máximas: noviembre-abril
    tmax_days = _fetch_daily_metric_for_months(
        station_code=code,
        variable_codes=[STAT_TEMP_MAX],
        year=yy,
        months=[11, 12, 1, 2, 3, 4],
        api_key=api_key,
    )
    if tmax_days:
        tmax_series = pd.Series(tmax_days, dtype=float)
        tmax_series = pd.to_numeric(tmax_series, errors="coerce").dropna()
        if not tmax_series.empty:
            min_day = str(tmax_series.idxmin())
            min_value = float(tmax_series.min())
            result["Mínima de máximas"] = {
                "Valor": f"{min_value:.1f} °C",
                "Fecha": _format_date_for_ui(min_day),
            }

    # Máxima de mínimas: mayo-septiembre
    tmin_days = _fetch_daily_metric_for_months(
        station_code=code,
        variable_codes=[STAT_TEMP_MIN],
        year=yy,
        months=[5, 6, 7, 8, 9],
        api_key=api_key,
    )
    if tmin_days:
        tmin_series = pd.Series(tmin_days, dtype=float)
        tmin_series = pd.to_numeric(tmin_series, errors="coerce").dropna()
        if not tmin_series.empty:
            max_day = str(tmin_series.idxmax())
            max_value = float(tmin_series.max())
            result["Máxima de mínimas"] = {
                "Valor": f"{max_value:.1f} °C",
                "Fecha": _format_date_for_ui(max_day),
            }

    # Día más ventoso: todo el año
    wind_days = _fetch_daily_metric_for_months(
        station_code=code,
        variable_codes=[STAT_WIND_MEAN_2, STAT_WIND_MEAN_6, STAT_WIND_MEAN_10],
        year=yy,
        months=list(range(1, 13)),
        api_key=api_key,
    )
    if wind_days:
        wind_series = pd.Series(wind_days, dtype=float)
        wind_series = pd.to_numeric(wind_series, errors="coerce").dropna()
        if not wind_series.empty:
            wind_day = str(wind_series.idxmax())
            wind_value = float(wind_series.max()) * 3.6
            result["Día más ventoso (viento medio)"] = {
                "Valor": f"{wind_value:.1f} km/h",
                "Fecha": _format_date_for_ui(wind_day),
            }

    return result


def fetch_meteocat_daily_extremes_for_periods(
    station_code: str,
    periods: List[Any],
    api_key: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return {}

    requested = sorted(
        {(int(getattr(period, "start").year), int(getattr(period, "start").month)) for period in periods}
    )
    if not requested:
        return {}

    tmax_days: Dict[str, float] = {}
    tmin_days: Dict[str, float] = {}
    wind_days: Dict[str, float] = {}

    chosen_wind_code: Optional[int] = None
    wind_candidates = [STAT_WIND_MEAN_2, STAT_WIND_MEAN_6, STAT_WIND_MEAN_10]

    for yy, mm in requested:
        payload_tmax = fetch_meteocat_daily_stats_month(
            station_code=code,
            variable_code=int(STAT_TEMP_MAX),
            year=int(yy),
            month=int(mm),
            api_key=api_key,
        )
        data_tmax = payload_tmax.get("data")
        if isinstance(data_tmax, dict):
            tmax_days.update({k: float(v) for k, v in data_tmax.items()})

        payload_tmin = fetch_meteocat_daily_stats_month(
            station_code=code,
            variable_code=int(STAT_TEMP_MIN),
            year=int(yy),
            month=int(mm),
            api_key=api_key,
        )
        data_tmin = payload_tmin.get("data")
        if isinstance(data_tmin, dict):
            tmin_days.update({k: float(v) for k, v in data_tmin.items()})

        if chosen_wind_code is not None:
            payload_wind = fetch_meteocat_daily_stats_month(
                station_code=code,
                variable_code=int(chosen_wind_code),
                year=int(yy),
                month=int(mm),
                api_key=api_key,
            )
            data_wind = payload_wind.get("data")
            if isinstance(data_wind, dict) and data_wind:
                wind_days.update({k: float(v) for k, v in data_wind.items()})
                continue

        for wind_code in wind_candidates:
            payload_wind = fetch_meteocat_daily_stats_month(
                station_code=code,
                variable_code=int(wind_code),
                year=int(yy),
                month=int(mm),
                api_key=api_key,
            )
            data_wind = payload_wind.get("data")
            if isinstance(data_wind, dict) and data_wind:
                chosen_wind_code = int(wind_code)
                wind_days.update({k: float(v) for k, v in data_wind.items()})
                break

    result: Dict[str, Dict[str, str]] = {}

    if tmax_days:
        tmax_series = pd.to_numeric(pd.Series(tmax_days, dtype=float), errors="coerce").dropna()
        if not tmax_series.empty:
            min_day = str(tmax_series.idxmin())
            result["Mínima de máximas"] = {
                "Valor": f"{float(tmax_series.min()):.1f} °C",
                "Fecha": _format_date_for_ui(min_day),
            }

    if tmin_days:
        tmin_series = pd.to_numeric(pd.Series(tmin_days, dtype=float), errors="coerce").dropna()
        if not tmin_series.empty:
            max_day = str(tmin_series.idxmax())
            result["Máxima de mínimas"] = {
                "Valor": f"{float(tmin_series.max()):.1f} °C",
                "Fecha": _format_date_for_ui(max_day),
            }

            tropical_nights = int((tmin_series > 20.0).sum())
            torrid_nights = int((tmin_series > 25.0).sum())
            result["Noches tropicales (mín > 20 °C)"] = {"Valor": f"{tropical_nights} noches", "Fecha": "—"}
            result["Noches tórridas (mín > 25 °C)"] = {"Valor": f"{torrid_nights} noches", "Fecha": "—"}

    if wind_days:
        wind_series = pd.to_numeric(pd.Series(wind_days, dtype=float), errors="coerce").dropna()
        if not wind_series.empty:
            wind_day = str(wind_series.idxmax())
            wind_value = float(wind_series.max()) * 3.6
            result["Día más ventoso (viento medio)"] = {
                "Valor": f"{wind_value:.1f} km/h",
                "Fecha": _format_date_for_ui(wind_day),
            }

    return result


def _format_date_for_ui(raw_day: str) -> str:
    parsed = _parse_stats_date(raw_day)
    if not parsed:
        return "—"
    try:
        return datetime.fromisoformat(parsed).strftime("%d/%m/%Y")
    except Exception:
        return "—"


def fetch_meteocat_annual_history_for_years(
    station_code: str,
    years: List[int],
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    valid_years = sorted({int(year) for year in years})
    if not code or not valid_years:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)

    rows_by_year: Dict[int, Dict[str, Any]] = {
        int(year): _empty_climo_row(f"{int(year):04d}-01-01", _climo_epoch_from_label(f"{int(year):04d}-01-01"))
        for year in valid_years
    }

    # Cache local para evitar repetir la misma llamada (variable).
    annual_payload_cache: Dict[int, Dict[int, Dict[str, Any]]] = {}

    def _fetch_cached_series(var_code: int) -> Dict[int, Dict[str, Any]]:
        cache_key = int(var_code)
        cached = annual_payload_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = fetch_meteocat_annual_stats_series(
            station_code=code,
            variable_code=int(var_code),
            api_key=api_key,
        )
        data = payload.get("data")
        out = data if isinstance(data, dict) else {}
        annual_payload_cache[cache_key] = out
        return out

    # Minimizar llamadas:
    # 1) Elegir un único código por métrica probando candidatos.
    # 2) Con ese código, rellenar todos los años usando la serie ya descargada.
    selected_years = {int(y) for y in valid_years}
    for metric_name, candidates in METEOCAT_ANNUAL_CLIMO_CODES.items():
        chosen_code = _select_metric_candidate_code(
            [int(c) for c in candidates],
            lambda var_code: any(
                (int(y) in selected_years) and _metric_value_available(item)
                for y, item in _fetch_cached_series(int(var_code)).items()
            ),
        )
        if chosen_code is None:
            continue

        chosen_series = _fetch_cached_series(chosen_code)
        for year in valid_years:
            data = chosen_series.get(int(year), {})
            if not data:
                continue
            _apply_climo_metric_value(rows_by_year[int(year)], metric_name, data)

    for year in valid_years:
        missing_mean = (
            _is_nan(rows_by_year[int(year)]["temp_mean"])
            and not _is_nan(rows_by_year[int(year)]["temp_max"])
            and not _is_nan(rows_by_year[int(year)]["temp_min"])
        )
        if missing_mean:
            rows_by_year[int(year)]["temp_mean"] = (
                rows_by_year[int(year)]["temp_max"] + rows_by_year[int(year)]["temp_min"]
            ) / 2.0

    return _finalize_climo_rows(rows_by_year, fill_temp_mean=False)


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
        epoch = _parse_measurement_epoch(reading.get("data"))
        if epoch is None:
            continue
        if best_epoch is None or epoch > best_epoch:
            best_epoch = epoch
            best_value = _safe_float(reading.get("valor"))
            best_ts = str(reading.get("data", "")).strip() or None
    return best_value, best_epoch, best_ts


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
def fetch_meteocat_station_day(station_code: str, year: int, month: int, day: int, api_key: Optional[str] = None) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    key = str(api_key or METEOCAT_API_KEY).strip()
    _cache_version = METEOCAT_SERIES_CACHE_VERSION
    del _cache_version
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
            epoch = _parse_measurement_epoch(reading.get("data"))
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
    # PPT (codi 35) es la precipitación por intervalo; PPTacu (codi 70)
    # es un contador del datalogger y no representa la lluvia del día.
    s = _series_from_map(var_map, V_PRECIP)
    return _sum_series(s)


def _precip_window_mm(var_map: Dict[int, List[Tuple[int, float]]]) -> float:
    """
    Calcula la precipitación acumulada dentro de una ventana temporal local.

    Se usa solo PPT (codi 35). PPTacu (codi 70) es un contador acumulado
    del datalogger y puede arrastrar precipitación ajena al día local.
    """
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
    s_uv = _series_from_map(var_map, V_UV)

    joined = _join_by_epoch(s_temp, s_rh, s_p_abs, s_wind, s_gust, s_dir, s_solar, s_uv)
    epochs = sorted(joined.keys())

    temps = []
    humidities = []
    pressures_abs = []
    winds = []
    gusts = []
    dirs = []
    solar = []
    uv_indexes = []
    for ep in epochs:
        row = joined[ep]
        temps.append(row[0] if len(row) > 0 else float("nan"))
        humidities.append(row[1] if len(row) > 1 else float("nan"))
        pressures_abs.append(row[2] if len(row) > 2 else float("nan"))
        winds.append(_ms_to_kmh(row[3]) if len(row) > 3 else float("nan"))
        gusts.append(_ms_to_kmh(row[4]) if len(row) > 4 else float("nan"))
        dirs.append(row[5] if len(row) > 5 else float("nan"))
        solar_raw = row[6] if len(row) > 6 else float("nan")
        solar.append(_non_negative(solar_raw))
        uv_indexes.append(row[7] if len(row) > 7 else float("nan"))

    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": humidities,
        "pressures_abs": pressures_abs,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "solar_radiations": solar,
        "uv_indexes": uv_indexes,
        "has_data": len(epochs) > 0,
    }


def _iter_utc_dates_for_window(start_epoch: int, end_epoch: int):
    safe_end = max(int(start_epoch), int(end_epoch) - 1)
    cursor = datetime.fromtimestamp(int(start_epoch), tz=timezone.utc).date()
    limit = datetime.fromtimestamp(safe_end, tz=timezone.utc).date()
    while cursor <= limit:
        yield cursor
        cursor += timedelta(days=1)


def _merge_var_maps(var_maps: List[Dict[int, List[Tuple[int, float]]]]) -> Dict[int, List[Tuple[int, float]]]:
    merged: Dict[int, Dict[int, float]] = {}
    for var_map in var_maps:
        if not isinstance(var_map, dict):
            continue
        for code, rows in var_map.items():
            try:
                code_int = int(code)
            except Exception:
                continue
            bucket = merged.setdefault(code_int, {})
            for row in rows if isinstance(rows, list) else []:
                try:
                    ep = int(row[0])
                    val = float(row[1])
                except Exception:
                    continue
                bucket[ep] = val

    out: Dict[int, List[Tuple[int, float]]] = {}
    for code, rows_by_epoch in merged.items():
        out[code] = sorted(rows_by_epoch.items(), key=lambda item: item[0])
    return out


def _filter_var_map_by_epoch_range(
    var_map: Dict[int, List[Tuple[int, float]]],
    start_epoch: int,
    end_epoch: int,
) -> Dict[int, List[Tuple[int, float]]]:
    filtered: Dict[int, List[Tuple[int, float]]] = {}
    for code, rows in var_map.items():
        keep = []
        for row in rows if isinstance(rows, list) else []:
            try:
                ep = int(row[0])
                val = float(row[1])
            except Exception:
                continue
            if int(start_epoch) <= ep < int(end_epoch):
                keep.append((ep, val))
        if keep:
            filtered[int(code)] = keep
    return filtered


@st.cache_data(ttl=600, show_spinner=False)
def fetch_meteocat_local_day_window(
    station_code: str,
    hours_before_start: int = 0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    code = str(station_code).strip().upper()
    _cache_version = METEOCAT_SERIES_CACHE_VERSION
    del _cache_version
    if not code:
        return {
            "station_code": code,
            "start_epoch": 0,
            "end_epoch": 0,
            "variables": {},
            "series": {"epochs": [], "has_data": False},
            "has_data": False,
        }

    now_local = datetime.now(CAT_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    start_epoch = int((day_start - timedelta(hours=max(0, int(hours_before_start)))).timestamp())
    end_epoch = int(day_end.timestamp())

    var_maps: List[Dict[int, List[Tuple[int, float]]]] = []
    for utc_day in _iter_utc_dates_for_window(start_epoch, end_epoch):
        payload = fetch_meteocat_station_day(
            code,
            utc_day.year,
            utc_day.month,
            utc_day.day,
            api_key=api_key,
        )
        if not payload.get("ok"):
            continue
        var_map = payload.get("variables", {})
        if isinstance(var_map, dict) and var_map:
            var_maps.append(var_map)

    merged = _merge_var_maps(var_maps)
    filtered = _filter_var_map_by_epoch_range(merged, start_epoch, end_epoch)
    series = extract_meteocat_daily_timeseries(filtered)
    return {
        "station_code": code,
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
        "variables": filtered,
        "series": series,
        "has_data": bool(series.get("epochs")),
    }


def _merge_timeseries_dicts(series_list: List[Dict[str, List[float]]]) -> Dict[str, List[float]]:
    merged_rows: Dict[int, Dict[str, float]] = {}
    key_map = {
        "temps": "temp",
        "humidities": "rh",
        "pressures_abs": "p",
        "winds": "wind",
        "gusts": "gust",
        "wind_dirs": "dir",
        "solar_radiations": "solar",
        "uv_indexes": "uv",
    }

    for series in series_list:
        epochs = series.get("epochs", []) if isinstance(series, dict) else []
        for idx, epoch in enumerate(epochs):
            try:
                ep = int(epoch)
            except Exception:
                continue
            row = merged_rows.setdefault(ep, {})
            for src_key, dst_key in key_map.items():
                values = series.get(src_key, [])
                value = values[idx] if idx < len(values) else float("nan")
                row[dst_key] = value

    ordered_epochs = sorted(merged_rows.keys())
    return {
        "epochs": ordered_epochs,
        "temps": [merged_rows[ep].get("temp", float("nan")) for ep in ordered_epochs],
        "humidities": [merged_rows[ep].get("rh", float("nan")) for ep in ordered_epochs],
        "pressures_abs": [merged_rows[ep].get("p", float("nan")) for ep in ordered_epochs],
        "winds": [merged_rows[ep].get("wind", float("nan")) for ep in ordered_epochs],
        "gusts": [merged_rows[ep].get("gust", float("nan")) for ep in ordered_epochs],
        "wind_dirs": [merged_rows[ep].get("dir", float("nan")) for ep in ordered_epochs],
        "solar_radiations": [merged_rows[ep].get("solar", float("nan")) for ep in ordered_epochs],
        "uv_indexes": [merged_rows[ep].get("uv", float("nan")) for ep in ordered_epochs],
        "has_data": len(ordered_epochs) > 0,
    }


@st.cache_data(ttl=600, show_spinner=False)
def fetch_meteocat_today_series_with_lookback(
    station_code: str,
    hours_before_start: int = 3,
    api_key: Optional[str] = None,
) -> Dict[str, List[float]]:
    payload = fetch_meteocat_local_day_window(
        station_code=station_code,
        hours_before_start=hours_before_start,
        api_key=api_key,
    )
    series = payload.get("series", {})
    if isinstance(series, dict):
        return series
    return {"epochs": [], "has_data": False}


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_meteocat_recent_synoptic_series(
    station_code: str,
    days_back: int = 7,
    api_key: Optional[str] = None,
) -> Dict[str, List[float]]:
    code = str(station_code).strip().upper()
    if not code:
        return {"epochs": [], "temps": [], "humidities": [], "pressures": [], "has_data": False}

    today_local = datetime.now(CAT_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start_day = today_local - timedelta(days=max(1, int(days_back)))
    cursor = start_day
    series_parts: List[Dict[str, List[float]]] = []
    while cursor <= today_local:
        payload = fetch_meteocat_station_day(
            code,
            cursor.year,
            cursor.month,
            cursor.day,
            api_key=api_key,
        )
        if payload.get("ok"):
            var_map = payload.get("variables", {})
            if isinstance(var_map, dict) and var_map:
                series_parts.append(extract_meteocat_daily_timeseries(var_map))
        cursor += timedelta(days=1)

    merged = _merge_timeseries_dicts(series_parts)
    return {
        "epochs": merged.get("epochs", []),
        "temps": merged.get("temps", []),
        "humidities": merged.get("humidities", []),
        "pressures": merged.get("pressures_abs", []),
        "has_data": bool(merged.get("epochs")),
    }


def is_meteocat_connection() -> bool:
    return is_provider_connection("METEOCAT", st.session_state)


def get_meteocat_data(api_key: Optional[str] = None, state=None) -> Optional[Dict[str, Any]]:
    state = resolve_state(state)
    if not is_provider_connection("METEOCAT", state):
        return None

    station_code = get_connected_provider_station_id("METEOCAT", state)
    if not station_code:
        return None

    snapshot = fetch_meteocat_station_snapshot(station_code, api_key=api_key)
    if not snapshot.get("ok"):
        return None

    local_day_payload = fetch_meteocat_local_day_window(
        station_code,
        hours_before_start=0,
        api_key=api_key,
    )
    day_vars = local_day_payload.get("variables", {}) if local_day_payload.get("has_data") else {}

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

    # Extremos del dia local real, no del dia UTC del endpoint.
    temp_max = _safe_float(values.get("temp"))
    temp_min = _safe_float(values.get("temp"))
    rh_max = _safe_float(values.get("rh"))
    rh_min = _safe_float(values.get("rh"))
    gust_max = gust_kmh

    s_temp_local = _series_from_map(day_vars, V_TEMP)
    s_rh_local = _series_from_map(day_vars, V_RH)
    s_gmax = _series_from_map(day_vars, V_GUST)

    tmax = _max_of_series(s_temp_local)
    if not _is_nan(tmax):
        temp_max = tmax

    tmin = _min_of_series(s_temp_local)
    if not _is_nan(tmin):
        temp_min = tmin

    rhmax = _max_of_series(s_rh_local)
    if not _is_nan(rhmax):
        rh_max = rhmax
    rhmin = _min_of_series(s_rh_local)
    if not _is_nan(rhmin):
        rh_min = rhmin

    gmax = _max_of_series(s_gmax)
    if not _is_nan(gmax):
        gust_max = _ms_to_kmh(gmax)

    rain_today = _precip_window_mm(day_vars)
    rain_1min = _max_of_series(_series_from_map(day_vars, V_RAIN_1MIN_MAX))
    if _is_nan(rain_1min):
        rain_1min = float("nan")
    raw_series = local_day_payload.get("series", {})
    series = raw_series if isinstance(raw_series, dict) else extract_meteocat_daily_timeseries(day_vars)

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
        "solar_radiation": _non_negative(_safe_float(values.get("solar"))),
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
        "_series": series,
    }

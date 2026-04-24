"""
Servicio para integrar observaciones y climatología de Meteo-France.
"""

import json
import math
import os
import time
from io import StringIO
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import requests
import streamlit as st

from data_files import METEOFRANCE_STATIONS_PATH
from utils.provider_state import (
    clear_provider_runtime_error,
    get_connected_provider_station_id,
    get_provider_station_id,
    is_provider_connection,
    resolve_state,
    set_provider_runtime_error,
)


METEOFRANCE_BASE_URL = os.getenv(
    "METEOFRANCE_BASE_URL",
    "https://public-api.meteofrance.fr/public/DPObs/v1",
).rstrip("/")
METEOFRANCE_CLIMO_BASE_URL = os.getenv(
    "METEOFRANCE_CLIMO_BASE_URL",
    "https://public-api.meteofrance.fr/public/DPClim/v1",
).rstrip("/")
METEOFRANCE_TIMEOUT_SECONDS = int(os.getenv("METEOFRANCE_TIMEOUT_SECONDS", "18"))

_CLIMO_DAILY_COLS = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "gust_max",
    "precip_total",
]

_CLIMO_EXTRA_COLS = [
    "solar_mean",
    "solar_hours",
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_max_date",
    "temp_abs_min",
    "temp_abs_min_date",
    "gust_abs_max_date",
    "precip_max_24h_date",
    "tropical_nights",
    "frost_nights",
]

# Fallback local con la key facilitada por el usuario para dejar la integración operativa.
_DEFAULT_METEOFRANCE_API_KEY = (
    "eyJ4NXQiOiJZV0kxTTJZNE1qWTNOemsyTkRZeU5XTTRPV014TXpjek1UVmhNbU14T1RSa09ETXlOVEE0Tnc9PSIs"
    "ImtpZCI6ImdhdGV3YXlfY2VydGlmaWNhdGVfYWxpYXMiLCJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9."
    "eyJzdWIiOiJtZXRlb2xhYnhAY2FyYm9uLnN1cGVyIiwiYXBwbGljYXRpb24iOnsib3duZXIiOiJtZXRlb2xhYngiLCJ0"
    "aWVyUXVvdGFUeXBlIjpudWxsLCJ0aWVyIjoiVW5saW1pdGVkIiwibmFtZSI6IkRlZmF1bHRBcHBsaWNhdGlvbiIsImlk"
    "IjozNzQ3OSwidXVpZCI6IjVlZTZkN2NjLWVjODUtNGRiYy1hNTExLWYzNTQ3YTZiZTQyNCJ9LCJpc3MiOiJodHRwczpcL"
    "1wvcG9ydGFpbC1hcGkubWV0ZW9mcmFuY2UuZnI6NDQzXC9vYXV0aDJcL3Rva2VuIiwidGllckluZm8iOnsiNTBQZXJNaW"
    "4iOnsidGllclF1b3RhVHlwZSI6InJlcXVlc3RDb3VudCIsImdyYXBoUUxNYXhDb21wbGV4aXR5IjowLCJncmFwaFFMT"
    "WF4RGVwdGgiOjAsInN0b3BPblF1b3RhUmVhY2giOnRydWUsInNwaWtlQXJyZXN0TGltaXQiOjAsInNwaWtlQXJyZXN0VW5"
    "pdCI6InNlYyJ9fSwia2V5dHlwZSI6IlBST0RVQ1RJT04iLCJzdWJzY3JpYmVkQVBJcyI6W3sic3Vic2NyaWJlclRlbmFu"
    "dERvbWFpbiI6ImNhcmJvbi5zdXBlciIsIm5hbWUiOiJEb25uZWVzUHVibGlxdWVzT2JzZXJ2YXRpb24iLCJjb250ZXh0"
    "IjoiXC9wdWJsaWNcL0RQT2JzXC92MSIsInB1Ymxpc2hlciI6ImJhc3RpZW5nIiwidmVyc2lvbiI6InYxIiwic3Vic2Ny"
    "aXB0aW9uVGllciI6IjUwUGVyTWluIn0seyJzdWJzY3JpYmVyVGVuYW50RG9tYWluIjoiY2FyYm9uLnN1cGVyIiwibmFt"
    "ZSI6IkFST01FIiwiY29udGV4dCI6IlwvcHVibGljXC9hcm9tZVwvMS4wIiwicHVibGlzaGVyIjoiYWRtaW5fbWYiLCJ2Z"
    "XJzaW9uIjoiMS4wIiwic3Vic2NyaXB0aW9uVGllciI6IjUwUGVyTWluIn0seyJzdWJzY3JpYmVyVGVuYW50RG9tYWluI"
    "joiY2FyYm9uLnN1cGVyIiwibmFtZSI6IkRvbm5lZXNQdWJsaXF1ZXNDbGltYXRvbG9naWUiLCJjb250ZXh0IjoiXC9wd"
    "WJsaWNcL0RQQ2xpbVwvdjEiLCJwdWJsaXNoZXIiOiJhZG1pbl9tZiIsInZlcnNpb24iOiJ2MSIsInN1YnNjcmlwdGlvb"
    "lRpZXIiOiI1MFBlck1pbiJ9XSwiZXhwIjoxODY5NjI3MDI3LCJ0b2tlbl90eXBlIjoiYXBpS2V5IiwiaWF0IjoxNzc0"
    "OTU0MjI3LCJqdGkiOiJiOTFlODcyOC0yMTFiLTQ1ODEtYThiMS1hNzJhN2E4MWFmNTcifQ==."
    "USVe2oZHdcjCOTOS-6hxlvUutb8nukTsKj7DSPLk0vfga8F6ySiQshDzzN9BBPPzhIgt26MfV5Q7j3WHoVH_vTJHpnuiP8"
    "OFECkZN1GJSlvPXfP0NGulj_Pm7419undNhpMYkFcuBMe4YCSfWyfCwiI9Iz5khos6fLZPq6WX0ICUP9DGeROt6zrxdHJO"
    "Zs3cT3vgQmu2kFyNfxKS2vm6BYD3vydA43RifEzEJpaDwX-N5IHe6SXOPWIDUtvtLj4G3DSOYijur-J312rT8gcx-T9vBe"
    "ex0bkAEI5R0zxcwyueq25O3uu10ux1zJnw3jSaWi_F7Wpc9AtJD3fdd9HAdw=="
)


def _get_setting(env_key: str, default: str = "") -> str:
    try:
        secret_val = st.secrets.get(env_key, "")
        if secret_val not in (None, ""):
            return str(secret_val).strip()
    except Exception:
        pass
    return str(os.getenv(env_key, default)).strip()


METEOFRANCE_API_KEY = _get_setting(
    "METEOFRANCE_API_KEY",
    _DEFAULT_METEOFRANCE_API_KEY,
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


def _k_to_c(value: Any) -> float:
    v = _safe_float(value)
    return v - 273.15 if not _is_nan(v) else float("nan")


def _pa_to_hpa(value: Any) -> float:
    v = _safe_float(value)
    return v / 100.0 if not _is_nan(v) else float("nan")


def _ms_to_kmh(value: Any) -> float:
    v = _safe_float(value)
    return v * 3.6 if not _is_nan(v) else float("nan")


def _first_valid(*values: Any) -> float:
    for value in values:
        v = _safe_float(value)
        if not _is_nan(v):
            return v
    return float("nan")


def _last_valid(values: List[float]) -> float:
    for value in reversed(values):
        if not _is_nan(_safe_float(value)):
            return float(value)
    return float("nan")


def _max_valid(values: List[float]) -> float:
    valid = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return max(valid) if valid else float("nan")


def _min_valid(values: List[float]) -> float:
    valid = [float(v) for v in values if not _is_nan(_safe_float(v))]
    return min(valid) if valid else float("nan")


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


def _utc_iso(dt: datetime) -> str:
    dt_utc = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt_utc.isoformat().replace("+00:00", "Z")


def _resolve_station_tz(station_id: str, lat: float, lon: float) -> str:
    sid = str(station_id or "").strip()
    prefix3 = sid[:3]
    prefix2 = sid[:2]

    # Prioridad por códigos territoriales Meteo-France / INSEE.
    if prefix3 == "971":
        return "America/Guadeloupe"
    if prefix3 == "972":
        return "America/Martinique"
    if prefix3 == "973":
        return "America/Cayenne"
    if prefix3 == "974":
        return "Indian/Reunion"
    if prefix3 == "975":
        return "America/Miquelon"
    if prefix3 == "976":
        return "Indian/Mayotte"
    if prefix3 in {"977", "978"}:
        return "America/Guadeloupe"
    if prefix3 == "986":
        return "Pacific/Wallis"
    if prefix3 == "987":
        return "Pacific/Tahiti"
    if prefix3 == "988":
        return "Pacific/Noumea"

    # Fallback por posición para estaciones especiales o catálogos futuros.
    lat_v = _safe_float(lat)
    lon_v = _safe_float(lon)
    if not _is_nan(lat_v) and not _is_nan(lon_v):
        if 2.0 <= lat_v <= 9.5 and -55.5 <= lon_v <= -50.0:
            return "America/Cayenne"
        if 14.0 <= lat_v <= 18.8 and -63.8 <= lon_v <= -60.8:
            return "America/Guadeloupe"
        if 14.0 <= lat_v <= 15.3 and -61.4 <= lon_v <= -60.7:
            return "America/Martinique"
        if -22.0 <= lat_v <= -19.0 and 54.5 <= lon_v <= 56.5:
            return "Indian/Reunion"
        if -13.5 <= lat_v <= -12.0 and 44.9 <= lon_v <= 45.5:
            return "Indian/Mayotte"
        if -18.5 <= lat_v <= -7.0 and 176.0 <= lon_v <= 179.5:
            return "Pacific/Wallis"
        if -28.0 <= lat_v <= -7.0 and -155.0 <= lon_v <= -118.0:
            return "Pacific/Tahiti"
        if -24.0 <= lat_v <= -18.0 and 162.0 <= lon_v <= 169.0:
            return "Pacific/Noumea"

    # Metropole/Corse et fallback.
    if prefix2 in {
        "01", "02", "03", "04", "05", "06", "07", "08", "09",
        "10", "11", "12", "13", "14", "15", "16", "17", "18", "19",
        "20", "21", "22", "23", "24", "25", "26", "27", "28", "29",
        "30", "31", "32", "33", "34", "35", "36", "37", "38", "39",
        "40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
        "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
        "60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
        "70", "71", "72", "73", "74", "75", "76", "77", "78", "79",
        "80", "81", "82", "83", "84", "85", "86", "87", "88", "89",
        "90", "91", "92", "93", "94", "95",
    }:
        return "Europe/Paris"

    return "Europe/Paris"


def _request_headers(api_key: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "apikey": api_key,
        "User-Agent": "MeteoLabX/1.0",
    }


@st.cache_data(ttl=300, show_spinner=False)
def _request_json_cached(path: str, params_json: str, api_key: str) -> Any:
    params = json.loads(params_json) if params_json else {}
    response = requests.get(
        f"{METEOFRANCE_BASE_URL}{path}",
        params=params,
        headers=_request_headers(api_key),
        timeout=METEOFRANCE_TIMEOUT_SECONDS,
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


def _request_json(path: str, params: Dict[str, Any], api_key: str) -> Any:
    return _request_json_cached(
        path=path,
        params_json=json.dumps(params, sort_keys=True, ensure_ascii=False),
        api_key=api_key,
    )


def _request_response(
    base_url: str,
    path: str,
    params: Dict[str, Any],
    api_key: str,
    *,
    accept: str,
) -> requests.Response:
    response = requests.get(
        f"{base_url}{path}",
        params=params,
        headers={**_request_headers(api_key), "Accept": accept},
        timeout=METEOFRANCE_TIMEOUT_SECONDS,
    )
    return response


@lru_cache(maxsize=2)
def _load_stations(path: str = str(METEOFRANCE_STATIONS_PATH)) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _find_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip()
    if not target:
        return {}
    for station in _load_stations():
        if str(station.get("id_station", "")).strip() == target:
            return station
    return {}


def get_meteofrance_station_series_start_date(station_id: str) -> Optional[str]:
    station = _find_station(station_id)
    raw = str(station.get("date_ouverture", "") or "").strip()
    return raw or None


def _empty_climo_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_CLIMO_DAILY_COLS + _CLIMO_EXTRA_COLS)


def _climo_num(value: Any) -> float:
    if value is None:
        return float("nan")
    raw = str(value).strip()
    if not raw:
        return float("nan")
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except Exception:
        return float("nan")


def _climo_date_token_to_iso(token: Any, year: Optional[int], month: Optional[int]) -> Optional[str]:
    raw = str(token or "").strip()
    if not raw:
        return None
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-01"
    if raw.isdigit() and len(raw) <= 2 and year is not None and month is not None:
        day = int(raw)
        if 1 <= day <= 31:
            return f"{int(year):04d}-{int(month):02d}-{day:02d}"
    return None


def _csv_to_df(csv_text: str) -> pd.DataFrame:
    text = str(csv_text or "").strip()
    if not text:
        return pd.DataFrame()
    return pd.read_csv(
        StringIO(text),
        sep=";",
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        engine="python",
    )


def _first_valid_number(*values: Any) -> float:
    for value in values:
        number = _climo_num(value)
        if not pd.isna(number):
            return float(number)
    return float("nan")


def _best_gust_with_date(
    candidates: Sequence[Tuple[Any, Any]],
    year: Optional[int] = None,
    month: Optional[int] = None,
    default_date: Optional[str] = None,
) -> Tuple[float, Optional[str]]:
    best_value = float("nan")
    best_date = default_date
    for value_raw, date_raw in candidates:
        value = _climo_num(value_raw)
        if pd.isna(value):
            continue
        if pd.isna(best_value) or float(value) > float(best_value):
            best_value = float(value)
            best_date = _climo_date_token_to_iso(date_raw, year, month) or default_date
    return best_value, best_date


def _parse_daily_climo_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    date_txt = _climo_date_token_to_iso(record.get("DATE"), None, None)
    if not date_txt:
        return None
    try:
        ts = pd.Timestamp(date_txt)
    except Exception:
        return None
    epoch = float(ts.replace(tzinfo=timezone.utc).timestamp())
    precip_total = _climo_num(record.get("RR"))
    gust_value, gust_date = _best_gust_with_date(
        [
            (record.get("FXI"), date_txt),
            (record.get("FXY"), date_txt),
            (record.get("FXI2"), date_txt),
            (record.get("FXI3S"), date_txt),
        ],
        default_date=date_txt,
    )
    temp_min = _climo_num(record.get("TN"))
    temp_max = _climo_num(record.get("TX"))
    temp_mean = _climo_num(record.get("TM"))
    if pd.isna(temp_mean) and not pd.isna(temp_min) and not pd.isna(temp_max):
        temp_mean = (float(temp_min) + float(temp_max)) / 2.0
    frost_nights = 1.0 if not pd.isna(temp_min) and float(temp_min) <= 0.0 else 0.0
    tropical_nights = 1.0 if not pd.isna(temp_min) and float(temp_min) >= 20.0 else 0.0
    rain_days = 1.0 if not pd.isna(precip_total) and float(precip_total) >= 1.0 else 0.0
    return {
        "date": ts.normalize(),
        "epoch": epoch,
        "temp_mean": float(temp_mean) if not pd.isna(temp_mean) else float("nan"),
        "temp_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "wind_mean": _climo_num(record.get("FFM")),
        "gust_max": gust_value,
        "precip_total": max(0.0, float(precip_total)) if not pd.isna(precip_total) else float("nan"),
        "solar_mean": float("nan"),
        "solar_hours": float("nan"),
        "precip_max_24h": max(0.0, float(precip_total)) if not pd.isna(precip_total) else float("nan"),
        "rain_days": rain_days,
        "temp_abs_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_abs_max_date": date_txt if not pd.isna(temp_max) else None,
        "temp_abs_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "temp_abs_min_date": date_txt if not pd.isna(temp_min) else None,
        "gust_abs_max_date": gust_date,
        "precip_max_24h_date": date_txt if not pd.isna(precip_total) else None,
        "tropical_nights": tropical_nights,
        "frost_nights": frost_nights,
    }


def _parse_monthly_climo_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_period = str(record.get("DATE", "") or "").strip()
    if len(raw_period) != 6 or not raw_period.isdigit():
        return None
    year = int(raw_period[:4])
    month = int(raw_period[4:6])
    if month < 1 or month > 12:
        return None
    date_txt = f"{year:04d}-{month:02d}-01"
    ts = pd.Timestamp(date_txt)
    epoch = float(ts.replace(tzinfo=timezone.utc).timestamp())
    precip_total = _climo_num(record.get("RR"))
    gust_value, gust_date = _best_gust_with_date(
        [
            (record.get("FXIAB"), record.get("FXIDAT")),
            (record.get("FXYAB"), record.get("FXYABDAT")),
            (record.get("FXI3SAB"), record.get("FXI3SDAT")),
        ],
        year=year,
        month=month,
        default_date=date_txt,
    )
    precip_max_24h = _climo_num(record.get("RRAB"))
    temp_abs_max = _climo_num(record.get("TXAB"))
    temp_abs_min = _climo_num(record.get("TNAB"))
    return {
        "date": ts.normalize(),
        "epoch": epoch,
        "temp_mean": _climo_num(record.get("TM")),
        "temp_max": _climo_num(record.get("TX")),
        "temp_min": _climo_num(record.get("TN")),
        "wind_mean": _climo_num(record.get("FFM")),
        "gust_max": gust_value,
        "precip_total": max(0.0, float(precip_total)) if not pd.isna(precip_total) else float("nan"),
        "solar_mean": float("nan"),
        "solar_hours": float("nan"),
        "precip_max_24h": max(0.0, float(precip_max_24h)) if not pd.isna(precip_max_24h) else float("nan"),
        "rain_days": _climo_num(record.get("NBJRR1")),
        "temp_abs_max": float(temp_abs_max) if not pd.isna(temp_abs_max) else float("nan"),
        "temp_abs_max_date": _climo_date_token_to_iso(record.get("TXDAT"), year, month),
        "temp_abs_min": float(temp_abs_min) if not pd.isna(temp_abs_min) else float("nan"),
        "temp_abs_min_date": _climo_date_token_to_iso(record.get("TNDAT"), year, month),
        "gust_abs_max_date": gust_date,
        "precip_max_24h_date": _climo_date_token_to_iso(record.get("RRABDAT"), year, month),
        "tropical_nights": _first_valid_number(record.get("NBJTNI20"), record.get("NBJTNS20")),
        "frost_nights": _first_valid_number(record.get("NBJGELEE")),
    }


def _normalize_climo_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_climo_df()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).copy()
    if df.empty:
        return _empty_climo_df()
    for col in _CLIMO_DAILY_COLS + _CLIMO_EXTRA_COLS:
        if col not in df.columns:
            df[col] = float("nan") if not col.endswith("_date") else None
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total",
        "solar_mean", "solar_hours", "precip_max_24h", "rain_days", "temp_abs_max",
        "temp_abs_min", "tropical_nights", "frost_nights",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["precip_total"] = df["precip_total"].clip(lower=0)
    df["precip_max_24h"] = df["precip_max_24h"].clip(lower=0)
    df["rain_days"] = df["rain_days"].clip(lower=0)
    df["tropical_nights"] = df["tropical_nights"].clip(lower=0)
    df["frost_nights"] = df["frost_nights"].clip(lower=0)
    df = (
        df.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df[_CLIMO_DAILY_COLS + _CLIMO_EXTRA_COLS]


def _command_response_json(path: str, params: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    response = _request_response(
        METEOFRANCE_CLIMO_BASE_URL,
        path,
        params,
        api_key,
        accept="application/json",
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
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"Respuesta JSON inválida de Meteo-France climatología: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Respuesta inesperada de Meteo-France climatología.")
    return payload


def _extract_command_id(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    for value in payload.values():
        if isinstance(value, dict):
            raw = str(value.get("return", "") or "").strip()
            if raw:
                return raw
    return ""


def _poll_command_csv(command_id: str, api_key: str, *, max_attempts: int = 10, wait_seconds: float = 0.7) -> str:
    last_detail = "producción no disponible"
    for attempt in range(max_attempts):
        response = _request_response(
            METEOFRANCE_CLIMO_BASE_URL,
            "/commande/fichier",
            {"id-cmde": str(command_id).strip()},
            api_key,
            accept="text/csv,application/json",
        )
        if response.status_code == 201:
            return response.text
        if response.status_code == 204:
            last_detail = "producción aún en curso"
            if attempt < max_attempts - 1:
                time.sleep(wait_seconds)
                continue
        elif response.status_code == 410:
            last_detail = "la production a déjà été livrée"
            break
        else:
            snippet = str(response.text or "").strip().replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            last_detail = f"{response.status_code} {response.reason}"
            if snippet:
                last_detail += f" | body: {snippet}"
            break
    raise RuntimeError(f"Meteo-France climatología: fichero no disponible para la commande {command_id} ({last_detail}).")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_meteofrance_climo_csv(
    station_id: str,
    endpoint: str,
    start_iso: str,
    end_iso: str,
    api_key: str,
) -> str:
    payload = _command_response_json(
        endpoint,
        {
            "id-station": str(station_id).strip(),
            "date-deb-periode": str(start_iso).strip(),
            "date-fin-periode": str(end_iso).strip(),
        },
        api_key,
    )
    command_id = _extract_command_id(payload)
    if not command_id:
        raise RuntimeError("Meteo-France climatología no devolvió id de commande.")
    return _poll_command_csv(command_id, api_key)


def _to_day_start_iso(value: date) -> str:
    return f"{value.strftime('%Y-%m-%d')}T00:00:00Z"


def _to_day_end_iso(value: date) -> str:
    return f"{value.strftime('%Y-%m-%d')}T23:59:59Z"


def _to_climo_end_iso(value: date) -> str:
    today_utc = datetime.now(timezone.utc).date()
    if value >= today_utc:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        return now_utc.isoformat().replace("+00:00", "Z")
    return _to_day_end_iso(value)


def _clamp_climo_dates(start_date: date, end_date: date) -> Tuple[Optional[date], Optional[date]]:
    today = datetime.now(timezone.utc).date()
    start = min(start_date, today)
    end = min(end_date, today)
    if start > end:
        return None, None
    return start, end


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_meteofrance_climo_daily_range(
    station_id: str,
    start_date: date,
    end_date: date,
    api_key: str,
) -> pd.DataFrame:
    start_date, end_date = _clamp_climo_dates(start_date, end_date)
    if start_date is None or end_date is None:
        return _empty_climo_df()
    csv_text = fetch_meteofrance_climo_csv(
        station_id=station_id,
        endpoint="/commande-station/quotidienne",
        start_iso=_to_day_start_iso(start_date),
        end_iso=_to_climo_end_iso(end_date),
        api_key=api_key,
    )
    raw_df = _csv_to_df(csv_text)
    rows = [_parse_daily_climo_row(rec) for rec in raw_df.to_dict("records")]
    return _normalize_climo_rows([row for row in rows if row])


def fetch_meteofrance_climo_daily_for_periods(
    station_id: str,
    periods: Sequence[Any],
    api_key: str,
) -> pd.DataFrame:
    if not periods:
        return _empty_climo_df()
    by_year: Dict[int, Tuple[date, date]] = {}
    for period in periods:
        start = period.start if hasattr(period, "start") else period[0]
        end = period.end if hasattr(period, "end") else period[1]
        year = int(start.year)
        current = by_year.get(year)
        if current is None:
            by_year[year] = (start, end)
        else:
            by_year[year] = (min(current[0], start), max(current[1], end))
    chunks: List[pd.DataFrame] = []
    for year, (year_start, year_end) in sorted(by_year.items()):
        chunk = fetch_meteofrance_climo_daily_range(
            station_id=station_id,
            start_date=year_start,
            end_date=year_end,
            api_key=api_key,
        )
        if chunk.empty:
            continue
        mask = pd.Series(False, index=chunk.index)
        dates = pd.to_datetime(chunk["date"], errors="coerce").dt.normalize()
        for period in periods:
            start = pd.Timestamp(period.start if hasattr(period, "start") else period[0])
            end = pd.Timestamp(period.end if hasattr(period, "end") else period[1])
            if int(start.year) != year:
                continue
            mask = mask | ((dates >= start.normalize()) & (dates <= end.normalize()))
        filtered = chunk.loc[mask].copy()
        if not filtered.empty:
            chunks.append(filtered)
    if not chunks:
        return _empty_climo_df()
    return _normalize_climo_rows(pd.concat(chunks, ignore_index=True).to_dict("records"))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_meteofrance_climo_monthly_for_year(
    station_id: str,
    year: int,
    api_key: str,
) -> pd.DataFrame:
    yy = int(year)
    start_date, end_date = _clamp_climo_dates(date(yy, 1, 1), date(yy, 12, 31))
    if start_date is None or end_date is None:
        return _empty_climo_df()
    csv_text = fetch_meteofrance_climo_csv(
        station_id=station_id,
        endpoint="/commande-station/mensuelle",
        start_iso=_to_day_start_iso(start_date),
        end_iso=_to_day_start_iso(end_date),
        api_key=api_key,
    )
    raw_df = _csv_to_df(csv_text)
    rows = [_parse_monthly_climo_row(rec) for rec in raw_df.to_dict("records")]
    frame = _normalize_climo_rows([row for row in rows if row])
    return frame[frame["date"].dt.year == yy].reset_index(drop=True) if not frame.empty else frame


def _aggregate_yearly_from_monthly(monthly_df: pd.DataFrame) -> pd.DataFrame:
    if monthly_df.empty:
        return _empty_climo_df()
    rows: List[Dict[str, Any]] = []
    for year, group in monthly_df.groupby(pd.to_datetime(monthly_df["date"]).dt.year):
        frame = group.copy().sort_values("date").reset_index(drop=True)
        row: Dict[str, Any] = {
            "date": pd.Timestamp(year=int(year), month=1, day=1),
            "epoch": 0.0,
            "temp_mean": float(pd.to_numeric(frame["temp_mean"], errors="coerce").mean()),
            "temp_max": float(pd.to_numeric(frame["temp_max"], errors="coerce").mean()),
            "temp_min": float(pd.to_numeric(frame["temp_min"], errors="coerce").mean()),
            "wind_mean": float(pd.to_numeric(frame["wind_mean"], errors="coerce").mean()),
            "gust_max": float(pd.to_numeric(frame["gust_max"], errors="coerce").max()),
            "precip_total": float(pd.to_numeric(frame["precip_total"], errors="coerce").sum(min_count=1)),
            "solar_mean": float(pd.to_numeric(frame["solar_mean"], errors="coerce").mean()),
            "solar_hours": float(pd.to_numeric(frame["solar_hours"], errors="coerce").sum(min_count=1)),
            "precip_max_24h": float(pd.to_numeric(frame["precip_max_24h"], errors="coerce").max()),
            "rain_days": float(pd.to_numeric(frame["rain_days"], errors="coerce").sum(min_count=1)),
            "temp_abs_max": float(pd.to_numeric(frame["temp_abs_max"], errors="coerce").max()),
            "temp_abs_max_date": None,
            "temp_abs_min": float(pd.to_numeric(frame["temp_abs_min"], errors="coerce").min()),
            "temp_abs_min_date": None,
            "gust_abs_max_date": None,
            "precip_max_24h_date": None,
            "tropical_nights": float(pd.to_numeric(frame["tropical_nights"], errors="coerce").sum(min_count=1)),
            "frost_nights": float(pd.to_numeric(frame["frost_nights"], errors="coerce").sum(min_count=1)),
        }
        for value_col, date_col, mode in [
            ("temp_abs_max", "temp_abs_max_date", "max"),
            ("temp_abs_min", "temp_abs_min_date", "min"),
            ("gust_max", "gust_abs_max_date", "max"),
            ("precip_max_24h", "precip_max_24h_date", "max"),
        ]:
            series = pd.to_numeric(frame[value_col], errors="coerce")
            valid = series.dropna()
            if valid.empty:
                continue
            idx = valid.idxmax() if mode == "max" else valid.idxmin()
            row[date_col] = frame.loc[idx, date_col]
        rows.append(row)
    return _normalize_climo_rows(rows)


def fetch_meteofrance_climo_yearly_for_years(
    station_id: str,
    years: Sequence[int],
    api_key: str,
) -> pd.DataFrame:
    valid_years = sorted({int(year) for year in years})
    if not valid_years:
        return _empty_climo_df()
    monthly_chunks: List[pd.DataFrame] = []
    for year in valid_years:
        monthly = fetch_meteofrance_climo_monthly_for_year(
            station_id=station_id,
            year=year,
            api_key=api_key,
        )
        if not monthly.empty:
            monthly_chunks.append(monthly)
    if not monthly_chunks:
        return _empty_climo_df()
    monthly_df = pd.concat(monthly_chunks, ignore_index=True)
    yearly_df = _aggregate_yearly_from_monthly(monthly_df)
    return yearly_df[yearly_df["date"].dt.year.isin(valid_years)].reset_index(drop=True)


def _parse_obs_row(row: Dict[str, Any], elevation_m: float) -> Dict[str, Any]:
    epoch = (
        _parse_epoch(row.get("validity_time"))
        or _parse_epoch(row.get("reference_time"))
        or _parse_epoch(row.get("insert_time"))
    )
    p_abs = _pa_to_hpa(row.get("pres"))
    p_msl = _pa_to_hpa(row.get("pmer"))

    if _is_nan(p_abs) and not _is_nan(p_msl):
        p_abs = float(p_msl) / math.exp(float(elevation_m or 0.0) / 8000.0)
    if _is_nan(p_msl) and not _is_nan(p_abs):
        p_msl = float(p_abs) * math.exp(float(elevation_m or 0.0) / 8000.0)

    return {
        "epoch": int(epoch) if epoch is not None else None,
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "temp_c": _k_to_c(row.get("t")),
        "dewpoint_c": _k_to_c(row.get("td")),
        "temp_max_c": _first_valid(_k_to_c(row.get("tx")), _k_to_c(row.get("t"))),
        "temp_min_c": _first_valid(_k_to_c(row.get("tn")), _k_to_c(row.get("t"))),
        "rh": _safe_float(row.get("u")),
        "rh_max": _first_valid(row.get("ux"), row.get("u")),
        "rh_min": _first_valid(row.get("un"), row.get("u")),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _ms_to_kmh(row.get("ff")),
        "gust_kmh": _ms_to_kmh(_first_valid(row.get("fxi10"), row.get("fxi"), row.get("fxy"))),
        "wind_dir_deg": _safe_float(row.get("dd")),
        "precip_mm": _first_valid(row.get("rr_per"), row.get("rr1")),
    }


def _local_today_window() -> Tuple[datetime, datetime]:
    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


def _filter_today_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    start_local, end_local = _local_today_window()
    start_epoch = int(start_local.timestamp())
    end_epoch = int(end_local.timestamp())
    return [
        row for row in rows
        if row.get("epoch") is not None and start_epoch <= int(row["epoch"]) < end_epoch
    ]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_meteofrance_latest_6m(station_id: str, api_key: str) -> List[Dict[str, Any]]:
    payload = _request_json(
        "/station/infrahoraire-6m",
        {"id_station": str(station_id).strip(), "format": "json"},
        api_key=api_key,
    )
    return payload if isinstance(payload, list) else []


@st.cache_data(ttl=300, show_spinner=False)
def fetch_meteofrance_hourly_series_today_with_lookback(
    station_id: str,
    api_key: str,
    *,
    hours_before_start: int = 0,
) -> List[Dict[str, Any]]:
    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    current_hour = now_local.replace(minute=0, second=0, microsecond=0)
    start_query = start_local - timedelta(hours=max(0, int(hours_before_start)))

    rows: Dict[int, Dict[str, Any]] = {}
    current = start_query
    while current <= current_hour:
        params = {
            "id_station": str(station_id).strip(),
            "format": "json",
            "date": _utc_iso(current),
        }
        try:
            payload = _request_json("/station/horaire", params, api_key=api_key)
        except Exception:
            current += timedelta(hours=1)
            continue

        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                epoch = _parse_epoch(first.get("validity_time"))
                if epoch is not None:
                    rows[int(epoch)] = first
        current += timedelta(hours=1)

    ordered_epochs = sorted(rows.keys())
    return [rows[ep] for ep in ordered_epochs]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_meteofrance_hourly_series_today(station_id: str, api_key: str) -> List[Dict[str, Any]]:
    return fetch_meteofrance_hourly_series_today_with_lookback(
        station_id,
        api_key,
        hours_before_start=0,
    )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_meteofrance_today_pressure_series_with_lookback(
    station_id: str,
    api_key: str,
    *,
    hours_before_start: int = 3,
) -> Dict[str, Any]:
    station_meta = _find_station(station_id)
    elevation = _safe_float(station_meta.get("elev"), default=0.0)
    hourly_rows_raw = fetch_meteofrance_hourly_series_today_with_lookback(
        station_id,
        api_key,
        hours_before_start=max(0, int(hours_before_start)),
    )
    rows = [_parse_obs_row(row, elevation) for row in hourly_rows_raw if isinstance(row, dict)]
    rows = [row for row in rows if row.get("epoch") is not None]
    rows.sort(key=lambda row: int(row["epoch"]))
    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "pressures_abs": [float(row.get("p_abs_hpa", float("nan"))) for row in rows],
        "has_data": bool(rows),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_meteofrance_recent_synoptic_series(
    station_id: str,
    api_key: str,
    *,
    days_back: int = 7,
    step_hours: int = 3,
) -> Dict[str, Any]:
    station_meta = _find_station(station_id)
    elevation = _safe_float(station_meta.get("elev"), default=0.0)
    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_utc = now_utc - timedelta(days=max(1, int(days_back)))
    rows: Dict[int, Dict[str, Any]] = {}
    current = start_utc
    while current <= now_utc:
        params = {
            "id_station": str(station_id).strip(),
            "format": "json",
            "date": _utc_iso(current),
        }
        try:
            payload = _request_json("/station/horaire", params, api_key=api_key)
        except Exception:
            current += timedelta(hours=max(1, int(step_hours)))
            continue
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                parsed = _parse_obs_row(first, elevation)
                epoch = parsed.get("epoch")
                if epoch is not None:
                    rows[int(epoch)] = parsed
        current += timedelta(hours=max(1, int(step_hours)))

    ordered = [rows[ep] for ep in sorted(rows.keys())]
    pressures_abs = [float(row.get("p_abs_hpa", float("nan"))) for row in ordered]
    return {
        "epochs": [int(row["epoch"]) for row in ordered if row.get("epoch") is not None],
        "temps": [float(row.get("temp_c", float("nan"))) for row in ordered],
        "humidities": [float(row.get("rh", float("nan"))) for row in ordered],
        "pressures": pressures_abs,
        "has_data": bool(ordered),
    }


def _pressure_3h_reference(rows: List[Dict[str, Any]]) -> Tuple[float, Optional[int], Optional[int]]:
    valid = [
        (int(row["epoch"]), float(row["p_msl_hpa"]))
        for row in rows
        if row.get("epoch") is not None and not _is_nan(_safe_float(row.get("p_msl_hpa")))
    ]
    if len(valid) < 2:
        return float("nan"), None, None
    ep_now, _ = valid[-1]
    target = ep_now - (3 * 3600)
    ep_old, p_old = min(valid, key=lambda item: abs(item[0] - target))
    return float(p_old), int(ep_old), int(ep_now)


def is_meteofrance_connection() -> bool:
    return is_provider_connection("METEOFRANCE", st.session_state)


def get_meteofrance_data(state=None) -> Optional[Dict[str, Any]]:
    state = resolve_state(state)
    if not is_provider_connection("METEOFRANCE", state):
        return None

    api_key = str(METEOFRANCE_API_KEY or "").strip()
    if not api_key:
        set_provider_runtime_error("METEOFRANCE", "Falta METEOFRANCE_API_KEY.", state)
        return None

    station_id = get_connected_provider_station_id("METEOFRANCE", state)
    if not station_id:
        set_provider_runtime_error("METEOFRANCE", "Falta id_station de Meteo-France.", state)
        return None

    station_meta = _find_station(station_id)
    station_name = str(station_meta.get("name", "") or station_id).strip()
    station_lat = _safe_float(station_meta.get("lat"))
    station_lon = _safe_float(station_meta.get("lon"))
    elevation = _safe_float(station_meta.get("elev"), default=0.0)
    station_pack = str(station_meta.get("pack", "")).strip()
    station_tz = _resolve_station_tz(station_id, station_lat, station_lon)

    try:
        latest_rows_raw = fetch_meteofrance_latest_6m(station_id, api_key=api_key)
        hourly_rows_raw = fetch_meteofrance_hourly_series_today(station_id, api_key=api_key)
    except Exception as exc:
        set_provider_runtime_error("METEOFRANCE", str(exc), state)
        return None

    latest_rows = [_parse_obs_row(row, elevation) for row in latest_rows_raw if isinstance(row, dict)]
    latest_rows = [row for row in latest_rows if row.get("epoch") is not None]

    hourly_rows = [_parse_obs_row(row, elevation) for row in hourly_rows_raw if isinstance(row, dict)]
    hourly_rows = [row for row in hourly_rows if row.get("epoch") is not None]
    hourly_rows = _filter_today_rows(hourly_rows)

    current = latest_rows[-1] if latest_rows else (hourly_rows[-1] if hourly_rows else None)
    if current is None:
        set_provider_runtime_error("METEOFRANCE", f"Sin datos de observación para {station_id}.", state)
        return None

    chart_epochs = [int(row["epoch"]) for row in hourly_rows]
    chart_temps = [float(row["temp_c"]) for row in hourly_rows]
    chart_rhs = [float(row["rh"]) for row in hourly_rows]
    chart_p_abs = [float(row["p_abs_hpa"]) for row in hourly_rows]
    chart_winds = [float(row["wind_kmh"]) for row in hourly_rows]
    chart_gusts = [float(row["gust_kmh"]) for row in hourly_rows]
    chart_dirs = [float(row["wind_dir_deg"]) for row in hourly_rows]
    chart_precips = [float(row["precip_mm"]) for row in hourly_rows]

    current_temp = _safe_float(current.get("temp_c"), default=_last_valid(chart_temps))
    current_rh = _safe_float(current.get("rh"), default=_last_valid(chart_rhs))
    current_td = _safe_float(current.get("dewpoint_c"))
    current_p_abs = _safe_float(current.get("p_abs_hpa"), default=_last_valid(chart_p_abs))
    current_p_msl = _safe_float(
        current.get("p_msl_hpa"),
        default=_last_valid(
            [
                _safe_float(row.get("p_msl_hpa"))
                for row in hourly_rows
            ]
        ),
    )
    current_wind = _safe_float(current.get("wind_kmh"), default=_last_valid(chart_winds))
    current_gust = _safe_float(current.get("gust_kmh"), default=_last_valid(chart_gusts))
    current_dir = _safe_float(current.get("wind_dir_deg"), default=_last_valid(chart_dirs))

    temp_max = _max_valid(
        [float(row["temp_max_c"]) for row in hourly_rows] + [current.get("temp_c", float("nan"))]
    )
    temp_min = _min_valid(
        [float(row["temp_min_c"]) for row in hourly_rows] + [current.get("temp_c", float("nan"))]
    )
    rh_max = _max_valid(
        [float(row["rh_max"]) for row in hourly_rows] + [current.get("rh", float("nan"))]
    )
    rh_min = _min_valid(
        [float(row["rh_min"]) for row in hourly_rows] + [current.get("rh", float("nan"))]
    )
    gust_max = _max_valid(
        [float(row["gust_kmh"]) for row in hourly_rows] + [current.get("gust_kmh", float("nan"))]
    )
    precip_total = float(
        sum(
            max(0.0, _safe_float(value))
            for value in chart_precips
            if not _is_nan(_safe_float(value))
        )
    )
    pressure_3h_ago, epoch_3h_ago, epoch_now_ref = _pressure_3h_reference(hourly_rows)

    lat_now = current.get("lat", float("nan"))
    lon_now = current.get("lon", float("nan"))
    if _is_nan(_safe_float(lat_now)):
        lat_now = station_lat
    if _is_nan(_safe_float(lon_now)):
        lon_now = station_lon

    base_epoch = int(current.get("epoch") or datetime.now(timezone.utc).timestamp())
    if epoch_now_ref is not None:
        base_epoch = int(max(base_epoch, epoch_now_ref))

    base = {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_pack": station_pack,
        "station_tz": station_tz,
        "lat": _safe_float(lat_now),
        "lon": _safe_float(lon_now),
        "elevation": float(elevation),
        "epoch": base_epoch,
        "Tc": current_temp,
        "RH": current_rh,
        "Td": current_td,
        "p_hpa": current_p_msl,
        "p_abs_hpa": current_p_abs,
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": current_wind,
        "gust": current_gust,
        "wind_dir_deg": current_dir,
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "temp_max": temp_max,
        "temp_min": temp_min,
        "rh_max": rh_max,
        "rh_min": rh_min,
        "gust_max": gust_max,
        "_series": {
            "epochs": chart_epochs,
            "temps": chart_temps,
            "humidities": chart_rhs,
            "pressures_abs": chart_p_abs,
            "winds": chart_winds,
            "gusts": chart_gusts,
            "wind_dirs": chart_dirs,
            "precips": chart_precips,
            "solar_radiations": [float("nan")] * len(chart_epochs),
            "has_data": bool(chart_epochs),
        },
        "_series_7d": {
            "epochs": chart_epochs,
            "temps": chart_temps,
            "humidities": chart_rhs,
            "pressures_abs": chart_p_abs,
            "has_data": bool(chart_epochs),
        },
    }
    clear_provider_runtime_error("METEOFRANCE", state)
    return base

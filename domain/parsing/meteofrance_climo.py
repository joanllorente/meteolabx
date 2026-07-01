"""
Parsing puro de la climatología de Météo-France (commande-station
quotidienne/mensuelle, CSV con separador ';').

Módulo de dominio sin ``streamlit`` ni transporte, consumido por
``server/services/meteofrance_climo.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd


_CLIMO_DAILY_COLS = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "wind_dir_mean",
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
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "wind_dir_mean", "gust_max", "precip_total",
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

def _extract_command_id(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    for value in payload.values():
        if isinstance(value, dict):
            raw = str(value.get("return", "") or "").strip()
            if raw:
                return raw
    return ""

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

def _clamp_climo_dates(start_date: date, end_date: date) -> Tuple[Optional[date], Optional[date]]:
    today = datetime.now(timezone.utc).date()
    start = min(start_date, today)
    end = min(end_date, today)
    if start > end:
        return None, None
    return start, end

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


def group_period_tuples_by_year(
    periods: Sequence[Tuple[date, date]],
) -> Dict[int, Tuple[date, date]]:
    """Una commande por año natural: une los periodos que caen en él."""
    by_year: Dict[int, Tuple[date, date]] = {}
    for start, end in periods:
        year = int(start.year)
        current = by_year.get(year)
        if current is None:
            by_year[year] = (start, end)
        else:
            by_year[year] = (min(current[0], start), max(current[1], end))
    return by_year


def filter_daily_df_to_periods(
    chunk: pd.DataFrame,
    periods: Sequence[Tuple[date, date]],
    year: int,
) -> pd.DataFrame:
    """Recorta el chunk anual a los periodos pedidos de ese año."""
    if chunk.empty:
        return chunk
    mask = pd.Series(False, index=chunk.index)
    dates = pd.to_datetime(chunk["date"], errors="coerce").dt.normalize()
    for start, end in periods:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if int(start_ts.year) != int(year):
            continue
        mask = mask | ((dates >= start_ts.normalize()) & (dates <= end_ts.normalize()))
    return chunk.loc[mask].copy()

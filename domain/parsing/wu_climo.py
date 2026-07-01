"""
Parsing puro del histórico diario de WU (v2/pws/history/daily).

Módulo de dominio sin ``streamlit`` ni transporte: normaliza los payloads
del endpoint histórico al esquema común consumido por
``server/services/wu_climo.py``.

La calibración per-user de WU NO vive aquí: es responsabilidad del
frontend (se aplica en el caller, como con la serie hourly/7day).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from config import RAIN_QUANTIZE_CORRECTION, RAIN_TIP_RESOLUTION

DAILY_SCHEMA = [
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


def _safe_float(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _first_valid_float(*values: Any) -> float:
    for value in values:
        v = _safe_float(value)
        if v == v:
            return v
    return float("nan")


# Clones puros de ``api.weather_underground`` (ese módulo importa
# streamlit, este no puede arrastrarlo). Misma semántica exacta.

CARDINAL_16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def _parse_wind_direction(val: Any) -> float:
    f = _safe_float(val)
    if f == f:
        return f % 360
    if val is None:
        return float("nan")
    s = str(val).strip().upper()
    if not s:
        return float("nan")
    if s in ("CALM", "CALMA"):
        return 0.0
    if s in CARDINAL_16:
        idx = CARDINAL_16.index(s)
        return (idx * 22.5) % 360
    return float("nan")


def first_valid_wind_dir(*vals: Any) -> float:
    for v in vals:
        d = _parse_wind_direction(v)
        if d == d:
            return d
    return float("nan")


def quantize_rain_mm_wu(mm_wu: float) -> float:
    if mm_wu != mm_wu:
        return float("nan")
    mm_corr = mm_wu * RAIN_QUANTIZE_CORRECTION
    tips = round(mm_corr / RAIN_TIP_RESOLUTION)
    return tips * RAIN_TIP_RESOLUTION


def parse_obs_date(observation: Dict[str, Any]) -> Optional[pd.Timestamp]:
    local_time = observation.get("obsTimeLocal")
    if isinstance(local_time, str) and len(local_time) >= 10:
        try:
            return pd.to_datetime(local_time[:10], format="%Y-%m-%d", errors="raise")
        except Exception:
            pass

    epoch = observation.get("epoch")
    try:
        epoch_i = int(epoch)
    except Exception:
        epoch_i = 0
    if epoch_i > 0:
        return pd.to_datetime(epoch_i, unit="s")
    return None


def empty_daily_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_SCHEMA)


def normalize_wu_daily_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    """Payload de history/daily → DataFrame del esquema común (1 fila/día)."""
    observations = payload.get("observations", []) if isinstance(payload, dict) else []
    if not isinstance(observations, list) or not observations:
        return empty_daily_dataframe()

    rows: List[Dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        timestamp = parse_obs_date(observation)
        if timestamp is None:
            continue

        metric = observation.get("metric", {})
        if not isinstance(metric, dict):
            metric = {}

        rows.append(
            {
                "date": pd.to_datetime(timestamp).normalize(),
                "epoch": _safe_float(observation.get("epoch")),
                "temp_mean": _first_valid_float(metric.get("tempAvg"), metric.get("temp")),
                "temp_max": _first_valid_float(metric.get("tempHigh"), metric.get("tempMax")),
                "temp_min": _first_valid_float(metric.get("tempLow"), metric.get("tempMin")),
                "wind_mean": _first_valid_float(
                    metric.get("windspeedAvg"),
                    metric.get("windSpeedAvg"),
                    metric.get("windspeed"),
                    metric.get("windSpeed"),
                ),
                "wind_dir_mean": first_valid_wind_dir(
                    metric.get("winddirAvg"),
                    metric.get("windDirAvg"),
                    metric.get("winddir"),
                    metric.get("windDir"),
                    metric.get("windDirection"),
                    observation.get("winddirAvg"),
                    observation.get("windDirAvg"),
                    observation.get("winddir"),
                    observation.get("windDir"),
                    observation.get("windDirection"),
                ),
                "gust_max": _first_valid_float(
                    metric.get("windgustHigh"),
                    metric.get("windGust"),
                    metric.get("windgust"),
                ),
                "precip_total": quantize_rain_mm_wu(
                    _first_valid_float(metric.get("precipTotal"), observation.get("precipTotal"))
                ),
            }
        )

    if not rows:
        return empty_daily_dataframe()

    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = pd.NA

    numeric_columns = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "wind_dir_mean", "gust_max", "precip_total"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
    if missing_mean.any():
        frame.loc[missing_mean, "temp_mean"] = (frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]) / 2.0

    frame["precip_total"] = frame["precip_total"].clip(lower=0)

    frame["quality"] = frame[numeric_columns[1:]].notna().sum(axis=1)
    frame = (
        frame.sort_values(["date", "quality", "epoch"], ascending=[True, True, True])
        .drop_duplicates(subset=["date"], keep="last")
        .drop(columns=["quality"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[DAILY_SCHEMA]


def iter_chunks(start_date: date, end_date: date, max_days: int = 31) -> Iterable[Tuple[date, date]]:
    """Trocea un rango en ventanas de hasta ``max_days`` (límite del API)."""
    cursor = start_date
    delta_days = int(max_days) - 1
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=delta_days), end_date)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def clip_period_tuples_to_today(
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> List[Tuple[date, date]]:
    """Descarta periodos futuros y recorta el final al día de hoy."""
    today = today_date or date.today()
    clipped: List[Tuple[date, date]] = []
    for start, end in periods:
        if start > today:
            continue
        clipped.append((start, min(end, today)))
    return clipped


def merge_daily_chunks(chunks: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatena chunks normalizados y deduplica por fecha (último gana)."""
    non_empty = [chunk for chunk in chunks if isinstance(chunk, pd.DataFrame) and not chunk.empty]
    if not non_empty:
        return empty_daily_dataframe()

    all_days = pd.concat(non_empty, ignore_index=True)
    all_days["date"] = pd.to_datetime(all_days["date"]).dt.normalize()
    all_days = (
        all_days.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return all_days[DAILY_SCHEMA]

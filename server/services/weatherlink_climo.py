"""
Histórico diario de WeatherLink v2 como servicio async puro.

WeatherLink expone histórico crudo en ventanas de hasta 24 h. Esta rama
trocea los periodos seleccionados por día local de la estación y agrega
las lecturas al esquema común que consume la pestaña Histórico.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.weatherlink import (
    _safe_float,
    _station_tzinfo,
    normalize_weatherlink_historic_series,
)
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.schemas.errors import ProviderError
from server.services.weatherlink import _fetch_station_meta, _get_json, _require_credentials

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_DAYS = 8
_MIN_REQUEST_INTERVAL_S = 0.2
_RATELIMIT_RETRIES = 2
_RATELIMIT_BACKOFF_S = 2.0


def _empty_daily_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_SCHEMA)


def _valid_numbers(values: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for value in values or []:
        parsed = _safe_float(value)
        if not math.isnan(parsed):
            out.append(float(parsed))
    return out


def _mean(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return float(sum(valid) / len(valid)) if valid else float("nan")


def _max(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return max(valid) if valid else float("nan")


def _sum(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return float(sum(valid)) if valid else float("nan")


def _min(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return min(valid) if valid else float("nan")


def _circular_mean_degrees(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    if not valid:
        return float("nan")
    sin_sum = sum(math.sin(math.radians(value % 360.0)) for value in valid)
    cos_sum = sum(math.cos(math.radians(value % 360.0)) for value in valid)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return float("nan")
    return float((math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0)


def _day_window(day: date, station: Dict[str, Any]) -> Tuple[int, int]:
    tzinfo = _station_tzinfo(station or {})
    start_dt = datetime.combine(day, time.min, tzinfo=tzinfo)
    end_dt = start_dt + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _series_to_daily_row(
    series: Dict[str, Any],
    day: date,
    station: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    epochs = [int(_safe_float(epoch, 0) or 0) for epoch in series.get("epochs", [])]
    if not epochs:
        return None

    tzinfo = _station_tzinfo(station or {})
    row_indexes: List[int] = []
    for idx, epoch in enumerate(epochs):
        if epoch <= 0:
            continue
        if datetime.fromtimestamp(epoch, tzinfo).date() == day:
            row_indexes.append(idx)

    if not row_indexes:
        return None

    def _col(name: str) -> List[Any]:
        values = list(series.get(name, []) or [])
        return [values[idx] if idx < len(values) else float("nan") for idx in row_indexes]

    temp_values = _col("temps")
    latest_epoch = max(epochs[idx] for idx in row_indexes)
    # La lluvia del día es la SUMA de la caída en cada intervalo (``rainfall_mm``
    # es el incremento por registro, no un acumulado corrido), no el máximo: con
    # ``_max`` solo se contaba el intervalo más lluvioso del día y el total
    # mensual salía ~20x corto (Roses enero: 12,6 mm vs 271 mm reales).
    precip_total = _sum(_col("precips"))
    if not math.isnan(precip_total):
        precip_total = max(0.0, precip_total)

    return {
        "date": pd.to_datetime(day.isoformat()),
        "epoch": float(latest_epoch),
        "temp_mean": _mean(temp_values),
        "temp_max": _max(temp_values),
        "temp_min": _min(temp_values),
        "wind_mean": _mean(_col("winds")),
        "wind_dir_mean": _circular_mean_degrees(_col("wind_dirs")),
        "gust_max": _max(_col("gusts")),
        "precip_total": precip_total,
    }


def _finalize_daily_rows(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_daily_dataframe()
    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    for column in [c for c in DAILY_SCHEMA if c != "date"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["date"])
        .sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[DAILY_SCHEMA]


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    api_secret: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    """Histórico diario para los periodos pedidos, en el esquema común."""
    _require_credentials(api_key, api_secret)
    station = str(station_id or "").strip()
    if not station or not periods:
        return _empty_daily_dataframe()

    station_meta = await _fetch_station_meta(
        station, api_key, api_secret, client, timeout_s=16.0,
    )
    days: List[date] = []
    for start, end in clip_period_tuples_to_today(list(periods), today_date=today_date):
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
    if not days:
        return _empty_daily_dataframe()

    altitude = _safe_float((station_meta or {}).get("elevation"))
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_DAYS)
    pace_lock = asyncio.Lock()
    next_request_at = 0.0

    async def _pace_request() -> None:
        nonlocal next_request_at
        loop = asyncio.get_running_loop()
        async with pace_lock:
            now = loop.time()
            if now < next_request_at:
                await asyncio.sleep(next_request_at - now)
                now = loop.time()
            next_request_at = now + _MIN_REQUEST_INTERVAL_S

    async def _fetch_day(day: date) -> Optional[Dict[str, Any]]:
        start_ts, end_ts = _day_window(day, station_meta or {})
        async with semaphore:
            for attempt in range(_RATELIMIT_RETRIES + 1):
                try:
                    await _pace_request()
                    payload = await _get_json(
                        client,
                        f"historic/{station}",
                        api_key,
                        api_secret,
                        params={"start-timestamp": start_ts, "end-timestamp": end_ts},
                        timeout_s=16.0,
                    )
                    break
                except ProviderError as exc:
                    if exc.error_code == "provider_ratelimit" and attempt < _RATELIMIT_RETRIES:
                        await asyncio.sleep(_RATELIMIT_BACKOFF_S * (attempt + 1))
                        continue
                    if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                        raise
                    logger.warning(
                        "WeatherLink climo day %s failed for %s: %s",
                        day.isoformat(), station, exc.detail or exc.error_code,
                    )
                    return None
        series = normalize_weatherlink_historic_series(payload, altitude_m=altitude)
        if not series.get("has_data"):
            return None
        return _series_to_daily_row(series, day, station_meta or {})

    rows = await asyncio.gather(*(_fetch_day(day) for day in days))
    return _finalize_daily_rows([row for row in rows if isinstance(row, dict)])


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    api_secret: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    """Selecciona y ensambla el modo solicitado por el contrato HTTP."""
    return await fetch_climo_daily_for_periods(
        client, station_id, api_key, api_secret, periods,
    )

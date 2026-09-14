"""Daily historical observations from MET Norway Frost."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.periods import merge_date_periods
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.services.climo_cache import get_or_fetch_climo_block
from server.services.frost import _request_observations_resilient, _safe_float

PROVIDER = "FROST"
DAILY_ELEMENTS = (
    "mean(air_temperature P1D)",
    "max(air_temperature P1D)",
    "min(air_temperature P1D)",
    "sum(precipitation_amount P1D)",
    "mean(wind_speed P1D)",
    "max(wind_speed_of_gust P1D)",
)
ELEMENT_COLUMNS = {
    "mean(air_temperature P1D)": ("temp_mean", 1.0),
    "max(air_temperature P1D)": ("temp_max", 1.0),
    "min(air_temperature P1D)": ("temp_min", 1.0),
    "sum(precipitation_amount P1D)": ("precip_total", 1.0),
    "mean(wind_speed P1D)": ("wind_mean", 3.6),
    "max(wind_speed_of_gust P1D)": ("gust_max", 3.6),
}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_SCHEMA)


def _acceptable_quality(observation: dict[str, Any]) -> bool:
    quality = observation.get("qualityCode")
    if quality in (None, ""):
        return True
    try:
        return int(quality) <= 4
    except (TypeError, ValueError):
        return False


def rows_to_daily(payload: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        try:
            instant = datetime.fromisoformat(
                str(item.get("referenceTime") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        row: dict[str, Any] = {
            "date": pd.Timestamp(instant.date()),
            "epoch": float(instant.replace(tzinfo=instant.tzinfo or timezone.utc).timestamp()),
        }
        for observation in item.get("observations", []):
            if not isinstance(observation, dict) or not _acceptable_quality(observation):
                continue
            mapping = ELEMENT_COLUMNS.get(str(observation.get("elementId") or ""))
            if not mapping:
                continue
            column, factor = mapping
            value = _safe_float(observation.get("value"))
            if not math.isnan(value):
                row[column] = value * factor
        if len(row) > 2:
            rows.append(row)
    if not rows:
        return _empty()
    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = pd.NA
    for column in DAILY_SCHEMA:
        if column != "date":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame[DAILY_SCHEMA].sort_values("date").drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


async def fetch_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    client_id: str,
    client_secret: str,
    today_date: date | None = None,
) -> pd.DataFrame:
    periods = merge_date_periods(
        clip_period_tuples_to_today(list(periods), today_date=today_date)
    )
    chunks: list[pd.DataFrame] = []
    for start, end in periods:
        async def fetch(start=start, end=end):
            return await _request_observations_resilient(
                station_id, client_id, client_secret, client,
                # Frost interpreta el extremo final de un rango de fechas a
                # medianoche; usar el día siguiente conserva el último día
                # solicitado completo.
                referencetime=f"{start.isoformat()}/{(end + timedelta(days=1)).isoformat()}",
                elements=DAILY_ELEMENTS, timeout_s=45.0,
            )

        payload = await get_or_fetch_climo_block(
            provider=PROVIDER,
            kind=f"observed-daily:{start.isoformat()}:{end.isoformat()}",
            station_id=station_id,
            credential=f"{client_id}:{client_secret}",
            client=client,
            end_date=end,
            fetcher=fetch,
        )
        frame = rows_to_daily(payload)
        if not frame.empty:
            chunks.append(frame)
    if not chunks:
        return _empty()
    return (
        pd.concat(chunks, ignore_index=True).sort_values("date")
        .drop_duplicates("date", keep="last").reset_index(drop=True)
    )

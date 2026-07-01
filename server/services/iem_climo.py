"""Historical datasets for IEM ASOS/METAR stations."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.schemas.errors import ProviderError
from server.services.iem import (
    ASOS_DATA_COLUMNS,
    ASOS_HISTORY_URL,
    PROVIDER,
    USER_AGENT,
    _f_to_c,
    _inch_to_mm,
    _knots_to_kmh,
    _safe_float,
    _station_meta,
    _station_parts,
    _station_tz,
)

logger = logging.getLogger(__name__)

_REQUEST_SPACING_S = 1.05
_RETRYABLE_STATUS_CODES = {429, 503}
_MAX_ATTEMPTS = 2
_DEFAULT_RETRY_DELAY_S = 2.5


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


def _min(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return min(valid) if valid else float("nan")


def _max(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return max(valid) if valid else float("nan")


def _sum(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    return float(sum(valid)) if valid else float("nan")


def _circular_mean_degrees(values: Sequence[Any]) -> float:
    valid = _valid_numbers(values)
    if not valid:
        return float("nan")
    sin_sum = sum(math.sin(math.radians(value % 360.0)) for value in valid)
    cos_sum = sum(math.cos(math.radians(value % 360.0)) for value in valid)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return float("nan")
    return float((math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0)


def _period_window(start: date, end: date, meta: Dict[str, Any]) -> Tuple[str, str]:
    tzinfo = _station_tz(meta)
    start_dt = datetime.combine(start, time.min, tzinfo=tzinfo)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tzinfo)
    return (
        start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _parse_valid_datetime(row: Dict[str, Any], meta: Dict[str, Any]) -> Optional[datetime]:
    raw = str(row.get("valid") or row.get("utc_valid") or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_station_tz(meta))
    return parsed.astimezone(_station_tz(meta))


def _csv_rows(text: str) -> List[Dict[str, str]]:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    header_idx = 0
    for idx, line in enumerate(lines):
        lower = line.lower()
        if "valid" in lower and ("station" in lower or "tmpf" in lower):
            header_idx = idx
            break
    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))
    return [dict(row) for row in reader if isinstance(row, dict) and row.get("valid")]


def _row_metric_values(row: Dict[str, Any]) -> Dict[str, float]:
    return {
        "temp": _f_to_c(row.get("tmpf")),
        "wind": _knots_to_kmh(row.get("sknt")),
        "gust": _knots_to_kmh(row.get("gust")),
        "wind_dir": _safe_float(row.get("drct")),
        "precip": _inch_to_mm(row.get("p01i")),
    }


def _finalize_daily_rows(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_daily_dataframe()
    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    for column in [column for column in DAILY_SCHEMA if column != "date"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["date"])
        .sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[DAILY_SCHEMA]


def _aggregate_daily(csv_text: str, meta: Dict[str, Any]) -> pd.DataFrame:
    buckets: Dict[date, Dict[str, List[float] | int]] = {}
    for row in _csv_rows(csv_text):
        valid_dt = _parse_valid_datetime(row, meta)
        if valid_dt is None:
            continue
        values = _row_metric_values(row)
        day = valid_dt.date()
        bucket = buckets.setdefault(
            day,
            {"epoch": [], "temp": [], "wind": [], "gust": [], "wind_dir": [], "precip": []},
        )
        bucket["epoch"].append(float(valid_dt.timestamp()))
        for key, value in values.items():
            if not math.isnan(value):
                bucket[key].append(value)

    daily_rows: List[Dict[str, Any]] = []
    for day, values in sorted(buckets.items()):
        temps = values["temp"]
        precip_values = values["precip"]
        if not any(values[key] for key in ("temp", "wind", "gust", "wind_dir", "precip")):
            continue
        daily_rows.append(
            {
                "date": pd.to_datetime(day.isoformat()),
                "epoch": _max(values["epoch"]),
                "temp_mean": _mean(temps),
                "temp_max": _max(temps),
                "temp_min": _min(temps),
                "wind_mean": _mean(values["wind"]),
                "wind_dir_mean": _circular_mean_degrees(values["wind_dir"]),
                "gust_max": _max(values["gust"]),
                "precip_total": _sum(precip_values),
            }
        )
    return _finalize_daily_rows(daily_rows)


async def _fetch_period_csv(
    client: httpx.AsyncClient,
    *,
    network: str,
    station: str,
    meta: Dict[str, Any],
    start: date,
    end: date,
) -> str:
    sts, ets = _period_window(start, end, meta)
    params: List[Tuple[str, str]] = [
        ("station", station),
        ("network", network),
        ("sts", sts),
        ("ets", ets),
        ("tz", str(meta.get("tz") or "UTC")),
        ("format", "onlycomma"),
        ("missing", "empty"),
        ("trace", "0.0001"),
    ]
    params.extend(("data", column) for column in ASOS_DATA_COLUMNS)
    response: Optional[httpx.Response] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.get(
                ASOS_HISTORY_URL,
                params=params,
                headers={"Accept": "text/csv,*/*", "User-Agent": USER_AGENT},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", provider=PROVIDER,
                detail=f"IEM ASOS historical timeout: {exc}",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error", provider=PROVIDER,
                detail=str(exc) or "IEM ASOS historical network error",
                status_code=502,
            ) from exc
        if response.status_code not in _RETRYABLE_STATUS_CODES or attempt >= _MAX_ATTEMPTS - 1:
            break
        retry_after = str(response.headers.get("Retry-After", "")).strip()
        try:
            delay_s = min(10.0, max(0.0, float(retry_after)))
        except ValueError:
            delay_s = _DEFAULT_RETRY_DELAY_S
        await asyncio.sleep(delay_s)

    if response is None:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail="IEM ASOS historical empty response",
            status_code=502,
        )

    if response.status_code == 429:
        raise ProviderError(
            "provider_ratelimit", provider=PROVIDER,
            detail="IEM ASOS historical rate limit (HTTP 429)",
            status_code=429,
        )
    if response.status_code == 422:
        raise ProviderError(
            "provider_bad_request", provider=PROVIDER,
            detail=response.text.strip() or "IEM ASOS historical request too large",
            status_code=422,
        )
    if response.status_code == 503:
        raise ProviderError(
            "provider_unavailable", provider=PROVIDER,
            detail="IEM ASOS historical service unavailable (HTTP 503)",
            status_code=503,
        )
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER,
            detail=f"IEM ASOS historical HTTP {response.status_code}",
            status_code=502,
        )
    return response.text


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    network, station = _station_parts(station_id)
    meta = _station_meta(station_id)
    clipped_periods = clip_period_tuples_to_today(list(periods), today_date=today_date)
    if not clipped_periods:
        return _empty_daily_dataframe()

    chunks: List[pd.DataFrame] = []
    for index, (start, end) in enumerate(clipped_periods):
        if index > 0:
            await asyncio.sleep(_REQUEST_SPACING_S)
        csv_text = await _fetch_period_csv(
            client,
            network=network,
            station=station,
            meta=meta,
            start=start,
            end=end,
        )
        chunk = _aggregate_daily(csv_text, meta)
        if not chunk.empty:
            chunks.append(chunk)
    if not chunks:
        return _empty_daily_dataframe()
    merged = pd.concat(chunks, ignore_index=True)
    return _finalize_daily_rows(merged.to_dict("records"))


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    return await fetch_climo_daily_for_periods(client, station_id, periods)

"""Windy Open Data personal weather station observations."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from server.schemas.errors import ProviderError


PROVIDER = "WINDY"
BASE_URL = "https://stations.windy.com/api/v2/opendata/station"


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _kelvin_to_c(value: Any) -> float:
    raw = _safe_float(value)
    return raw - 273.15 if math.isfinite(raw) and raw > 170.0 else raw


def _pa_to_hpa(value: Any) -> float:
    raw = _safe_float(value)
    if not math.isfinite(raw):
        return raw
    hpa = raw / 100.0 if abs(raw) > 2000.0 else raw
    return hpa if 300.0 <= hpa <= 1100.0 else float("nan")


def _ms_to_kmh(value: Any) -> float:
    raw = _safe_float(value)
    return raw * 3.6 if math.isfinite(raw) else raw


def _non_negative(value: Any) -> float:
    raw = _safe_float(value)
    return max(0.0, raw) if math.isfinite(raw) else raw


def _epoch_seconds(value: Any) -> int:
    raw = _safe_float(value)
    if not math.isfinite(raw) or raw <= 0:
        return 0
    return int(raw / 1000.0) if raw > 100_000_000_000 else int(raw)


def _value_at(data: Dict[str, Any], field: str, index: int) -> Any:
    values = data.get(field)
    return values[index] if isinstance(values, list) and index < len(values) else None


def _rows(payload: Any) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    header = payload.get("header") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(header, dict):
        header = {}
    if not isinstance(data, dict):
        return header, []

    rows = []
    timestamps = data.get("ts") if isinstance(data.get("ts"), list) else []
    elevation = _safe_float(header.get("elev_m"))
    pressure_factor = math.exp(elevation / 8000.0) if math.isfinite(elevation) else 1.0
    for index, raw_timestamp in enumerate(timestamps):
        epoch = _epoch_seconds(raw_timestamp)
        if epoch <= 0:
            continue
        pressure_abs = _pa_to_hpa(_value_at(data, "pressure", index))
        rows.append({
            "epoch": epoch,
            "Tc": _kelvin_to_c(_value_at(data, "temp", index)),
            "RH": _safe_float(_value_at(data, "rh", index)),
            # Td es siempre derivado por el pipeline común desde Tc + RH.
            "Td": float("nan"),
            "p_abs_hpa": pressure_abs,
            "p_hpa": pressure_abs * pressure_factor if math.isfinite(pressure_abs) else float("nan"),
            "wind": _ms_to_kmh(_value_at(data, "wind", index)),
            "gust": _ms_to_kmh(_value_at(data, "wind_gust", index)),
            "wind_dir_deg": _safe_float(_value_at(data, "wind_dir", index)),
            "uv": _non_negative(_value_at(data, "uv", index)),
            "precip_rate": _non_negative(_value_at(data, "precip_1h", index)),
        })
    rows.sort(key=lambda row: row["epoch"])
    return header, rows


def _filter_rows(rows: Iterable[Dict[str, Any]], *, start_epoch: Optional[int] = None) -> list[Dict[str, Any]]:
    return [row for row in rows if start_epoch is None or row["epoch"] >= start_epoch]


def _local_day_start_epoch(now: Optional[datetime], tz_name: str) -> int:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        local_tz = ZoneInfo(str(tz_name or "UTC"))
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    local_now = now_utc.astimezone(local_tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp())


def _precip_cumulative(rows: list[Dict[str, Any]]) -> list[float]:
    """Integrate Windy's rolling one-hour precipitation as a rate."""
    cumulative = 0.0
    values = []
    previous_epoch: Optional[int] = None
    for row in rows:
        rate = _safe_float(row.get("precip_rate"))
        if previous_epoch is not None and math.isfinite(rate):
            interval_hours = min(1.0, max(0.0, (row["epoch"] - previous_epoch) / 3600.0))
            cumulative += max(0.0, rate) * interval_hours
        values.append(cumulative)
        previous_epoch = row["epoch"]
    return values


def _series(header: Dict[str, Any], rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "epochs": [row["epoch"] for row in rows],
        "temps": [row["Tc"] for row in rows],
        "humidities": [row["RH"] for row in rows],
        "dewpts": [row["Td"] for row in rows],
        "pressures": [row["p_hpa"] for row in rows],
        "pressures_abs": [row["p_abs_hpa"] for row in rows],
        "uv_indexes": [row["uv"] for row in rows],
        "solar_radiations": [float("nan") for _ in rows],
        "precips": _precip_cumulative(rows),
        "winds": [row["wind"] for row in rows],
        "gusts": [row["gust"] for row in rows],
        "wind_dirs": [row["wind_dir_deg"] for row in rows],
        "lat": _safe_float(header.get("lat")),
        "lon": _safe_float(header.get("lon")),
        "has_data": bool(rows),
    }


async def fetch_observations(station_id: str, api_key: str, *, client=None, timeout_s: float = 20.0) -> Dict[str, Any]:
    if not api_key:
        raise ProviderError(
            "missing_api_key", provider=PROVIDER,
            detail="Windy server API key is not configured", status_code=500,
        )
    # Windy Open Data IDs are case-sensitive (for example ``nMcOlGzd``).
    station_key = str(station_id or "").strip()
    if not station_key:
        raise ProviderError("station_not_found", provider=PROVIDER, status_code=404)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        try:
            response = await client.get(
                f"{BASE_URL}/{quote(station_key, safe='')}/observation",
                headers={"windy-api-key": api_key, "Accept": "application/json"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", provider=PROVIDER, status_code=504) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error", provider=PROVIDER, detail=str(exc), status_code=502,
            ) from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code in (401, 403):
        raise ProviderError("provider_unauthorized", provider=PROVIDER, status_code=401)
    if response.status_code == 404:
        raise ProviderError("station_not_found", provider=PROVIDER, status_code=404)
    if response.status_code == 429:
        raise ProviderError("provider_ratelimit", provider=PROVIDER, status_code=429)
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER,
            detail=f"Windy HTTP {response.status_code}", status_code=502,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("provider_bad_response", provider=PROVIDER, status_code=502) from exc
    if not isinstance(payload, dict):
        raise ProviderError("provider_bad_response", provider=PROVIDER, status_code=502)
    return payload


def current_from_payload(
    payload: Any,
    *,
    now: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> Dict[str, Any]:
    header, rows = _rows(payload)
    if not rows:
        raise ProviderError(
            "provider_no_data", provider=PROVIDER,
            detail="Windy station has no recent observations", status_code=404,
        )
    latest = dict(rows[-1])
    day_start = _local_day_start_epoch(now, tz_name)
    today_rows = _filter_rows(rows, start_epoch=day_start)
    precip_values = _precip_cumulative(today_rows)
    latest.update({
        "time_utc": datetime.fromtimestamp(latest["epoch"], timezone.utc).isoformat(),
        "time_local": datetime.fromtimestamp(latest["epoch"], timezone.utc).isoformat(),
        "lat": _safe_float(header.get("lat")),
        "lon": _safe_float(header.get("lon")),
        "elevation": _safe_float(header.get("elev_m")),
        "station_name": str(header.get("name") or header.get("id") or "").strip(),
        "precip_total": precip_values[-1] if precip_values else float("nan"),
        "solar_radiation": float("nan"),
    })
    return latest


def today_series_from_payload(
    payload: Any,
    *,
    now: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> Dict[str, Any]:
    header, rows = _rows(payload)
    day_start = _local_day_start_epoch(now, tz_name)
    return _series(header, _filter_rows(rows, start_epoch=day_start))


def recent_series_from_payload(payload: Any, *, days_back: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    header, rows = _rows(payload)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = int((now_utc - timedelta(days=max(1, min(7, int(days_back))))).timestamp())
    return _series(header, _filter_rows(rows, start_epoch=cutoff))


async def fetch_current(station_id: str, api_key: str, *, client=None) -> Dict[str, Any]:
    return current_from_payload(await fetch_observations(station_id, api_key, client=client))


async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client=None,
    tz_name: str = "UTC",
) -> Dict[str, Any]:
    return today_series_from_payload(
        await fetch_observations(station_id, api_key, client=client),
        tz_name=tz_name,
    )


async def fetch_recent_series(station_id: str, api_key: str, *, days_back: int = 7, client=None) -> Dict[str, Any]:
    payload = await fetch_observations(station_id, api_key, client=client)
    return recent_series_from_payload(payload, days_back=days_back)

#!/usr/bin/env python3
"""Validate station alias candidates with same-hour observations."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_station_aliases import DEFAULT_DATABASE
from server.config import get_settings
from server.routers.observations import _resolve_provider_fetchers
from server.schemas.observation import TodaySeriesRequest


IEM_HISTORY_URL = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"
CHECK_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_alias_observation_checks (
    check_pk INTEGER PRIMARY KEY,
    alias_pk INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'conflict', 'inconclusive', 'error')),
    matched_hours INTEGER NOT NULL DEFAULT 0,
    compared_values INTEGER NOT NULL DEFAULT 0,
    agreeing_values INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    FOREIGN KEY (alias_pk) REFERENCES station_aliases(alias_pk) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alias_observation_checks_alias
ON station_alias_observation_checks(alias_pk, checked_at);
"""
TOLERANCES = {"temperature_c": 0.2, "humidity_pct": 1.0, "wind_kmh": 0.5, "wind_dir_deg": 3.0}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _hourly_source(series: dict[str, Any]) -> dict[int, dict[str, float]]:
    keys = {
        "temperature_c": "temps", "humidity_pct": "humidities",
        "wind_kmh": "winds", "wind_dir_deg": "wind_dirs",
    }
    hourly: dict[int, dict[str, float]] = {}
    for index, raw_epoch in enumerate(series.get("epochs", [])):
        try:
            epoch = int(raw_epoch)
        except (TypeError, ValueError):
            continue
        bucket = epoch // 3600
        row = hourly.setdefault(bucket, {"epoch": float(epoch)})
        if epoch >= row["epoch"]:
            row["epoch"] = float(epoch)
            for canonical, source_key in keys.items():
                values = series.get(source_key, [])
                value = _number(values[index]) if isinstance(values, list) and index < len(values) else None
                if value is not None:
                    row[canonical] = value
    return hourly


def _hourly_iem(rows: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    hourly: dict[int, dict[str, float]] = {}
    for item in rows:
        try:
            epoch = _epoch(item.get("utc_valid", ""))
        except (TypeError, ValueError):
            continue
        bucket = epoch // 3600
        row: dict[str, float] = {"epoch": float(epoch)}
        tmpf = _number(item.get("tmpf"))
        if tmpf is not None:
            row["temperature_c"] = (tmpf - 32.0) * 5.0 / 9.0
        for source_key, canonical, factor in (
            ("relh", "humidity_pct", 1.0),
            ("sknt", "wind_kmh", 1.852),
            ("drct", "wind_dir_deg", 1.0),
        ):
            value = _number(item.get(source_key))
            if value is not None:
                row[canonical] = value * factor
        current = hourly.get(bucket)
        if current is None or epoch >= current["epoch"]:
            hourly[bucket] = row
    return hourly


def compare_hourly(source: dict[int, dict[str, float]], iem: dict[int, dict[str, float]]) -> dict[str, Any]:
    comparisons = []
    matched_hours = 0
    for hour in sorted(set(source) & set(iem)):
        matched_hours += 1
        for variable, tolerance in TOLERANCES.items():
            if variable not in source[hour] or variable not in iem[hour]:
                continue
            left, right = source[hour][variable], iem[hour][variable]
            difference = abs(left - right)
            if variable == "wind_dir_deg":
                difference = min(difference, 360.0 - difference)
            comparisons.append({
                "hour_epoch": hour * 3600, "variable": variable,
                "source": round(left, 3), "iem": round(right, 3),
                "difference": round(difference, 3), "agrees": difference <= tolerance,
            })
    agreeing = sum(bool(row["agrees"]) for row in comparisons)
    total = len(comparisons)
    ratio = agreeing / total if total else 0.0
    temperature = [row for row in comparisons if row["variable"] == "temperature_c"]
    temperature_ratio = sum(row["agrees"] for row in temperature) / len(temperature) if temperature else 0.0
    variables = {row["variable"] for row in comparisons}
    strong_temperature = len(temperature) >= 4 and temperature_ratio >= 0.75
    strong_multivariable = total >= 8 and len(variables) >= 2 and ratio >= 0.80
    if matched_hours >= 4 and (strong_temperature or strong_multivariable):
        status = "confirmed"
    elif matched_hours >= 4 and total >= 8 and ratio <= 0.25 and (
        not temperature or temperature_ratio <= 0.25
    ):
        status = "conflict"
    else:
        status = "inconclusive"
    return {
        "status": status, "matched_hours": matched_hours, "compared_values": total,
        "agreeing_values": agreeing, "agreement_ratio": round(ratio, 3),
        "temperature_agreement_ratio": round(temperature_ratio, 3),
        "comparisons": comparisons,
    }


async def _source_series(provider: str, station_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    body = TodaySeriesRequest(provider=provider, station_id=station_id)
    _secret, _current, fetch_series = _resolve_provider_fetchers(body, client, get_settings())
    value = await fetch_series()
    return value if isinstance(value, dict) else {}


async def _iem_rows(
    network: str, station_id: str, dates: set[str], client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    rows = []
    for date in sorted(dates):
        response = await client.get(
            IEM_HISTORY_URL,
            params={"network": network, "station": station_id, "date": date},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows.extend(item for item in payload.get("data", []) if isinstance(item, dict))
    return rows


async def _validate_candidate(
    candidate: sqlite3.Row, client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
) -> tuple[int, dict[str, Any]]:
    async with semaphore:
        try:
            series = await _source_series(candidate["provider"], candidate["source_station_id"], client)
            dates = _dates_for_series(series, candidate["iem_timezone"])
            iem_rows = await _iem_rows(
                candidate["network_code"], candidate["iem_station_id"], dates, client,
            ) if dates else []
            details = compare_hourly(_hourly_source(series), _hourly_iem(iem_rows))
        except Exception as exc:
            details = {
                "status": "error", "matched_hours": 0, "compared_values": 0,
                "agreeing_values": 0, "error": f"{type(exc).__name__}: {exc}",
            }
        return int(candidate["alias_pk"]), details


def _dates_for_series(series: dict[str, Any], tz_name: str) -> set[str]:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dates = set()
    for epoch in series.get("epochs", []):
        try:
            dates.add(datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(tz).date().isoformat())
        except (TypeError, ValueError, OSError):
            pass
    return dates


def _candidates(
    connection: sqlite3.Connection, limit: int, provider: str,
    retry_inconclusive: bool = False, include_secure: bool = False,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    provider_clause = ""
    if provider:
        provider_clause = " AND source.provider = ?"
        params.append(provider.upper())
    params.append(limit)
    pending_clause = (
        "(latest.check_pk IS NULL OR latest.status = 'inconclusive')"
        if retry_inconclusive else "latest.check_pk IS NULL"
    )
    methods = "'inventory_probable', 'inventory_ambiguous'"
    if include_secure:
        methods = "'inventory_secure', 'inventory_probable', 'inventory_ambiguous'"
    return connection.execute(
        f"""
        SELECT a.alias_pk, source.provider, source.station_id AS source_station_id,
               iem.network_code, iem.station_id AS iem_station_id,
               COALESCE(iem.timezone, 'UTC') AS iem_timezone
        FROM station_aliases a
        JOIN stations source ON source.station_pk = a.canonical_station_pk
        JOIN stations iem ON iem.station_pk = a.station_pk
        LEFT JOIN station_alias_observation_checks latest
          ON latest.check_pk = (
              SELECT MAX(c.check_pk) FROM station_alias_observation_checks c
              WHERE c.alias_pk = a.alias_pk
          )
        WHERE a.reviewed = 0 AND a.method IN ({methods})
          AND {pending_clause} {provider_clause}
        ORDER BY CASE a.method
                   WHEN 'inventory_secure' THEN 0
                   WHEN 'inventory_probable' THEN 1
                   ELSE 2
                 END,
                 a.confidence DESC, a.alias_pk
        LIMIT ?
        """,
        params,
    ).fetchall()


async def validate_batch(
    database: Path, *, limit: int = 10, provider: str = "",
    retry_inconclusive: bool = False, concurrency: int = 1,
    include_secure: bool = False,
) -> dict[str, int]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(CHECK_SCHEMA)
    candidates = _candidates(connection, limit, provider, retry_inconclusive, include_secure)
    counts = {"confirmed": 0, "conflict": 0, "inconclusive": 0, "error": 0}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(headers={"User-Agent": "MeteoLabX alias validation"}) as client:
        tasks = [_validate_candidate(candidate, client, semaphore) for candidate in candidates]
        for task in asyncio.as_completed(tasks):
            alias_pk, details = await task
            status = details["status"]
            counts[status] += 1
            connection.execute(
                """
                INSERT INTO station_alias_observation_checks(
                    alias_pk, checked_at, status, matched_hours,
                    compared_values, agreeing_values, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alias_pk, datetime.now(timezone.utc).isoformat(), status,
                    details["matched_hours"], details["compared_values"],
                    details["agreeing_values"], json.dumps(details, separators=(",", ":")),
                ),
            )
            if status in {"confirmed", "conflict"}:
                alias = connection.execute(
                    "SELECT confidence, evidence_json FROM station_aliases WHERE alias_pk = ?",
                    (alias_pk,),
                ).fetchone()
                evidence = json.loads(alias["evidence_json"])
                evidence["observation_comparison"] = details
                confidence = (
                    max(float(alias["confidence"]), 0.995)
                    if status == "confirmed"
                    else min(float(alias["confidence"]), 0.20)
                )
                connection.execute(
                    """
                    UPDATE station_aliases
                    SET method = ?, confidence = ?, evidence_json = ?
                    WHERE alias_pk = ? AND reviewed = 0
                    """,
                    (
                        f"observation_{status}", confidence,
                        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                        alias_pk,
                    ),
                )
            connection.commit()
    connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--provider", default="")
    parser.add_argument("--retry-inconclusive", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--include-secure",
        action="store_true",
        help="También procesa candidatos inventory_secure sin evidencia observacional.",
    )
    args = parser.parse_args()
    counts = asyncio.run(validate_batch(
        args.database, limit=max(1, args.limit), provider=args.provider,
        retry_inconclusive=args.retry_inconclusive, concurrency=max(1, args.concurrency),
        include_secure=args.include_secure,
    ))
    print("Observation validation:", " ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()

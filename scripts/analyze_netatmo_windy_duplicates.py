#!/usr/bin/env python3
"""Find and evaluate conservative Netatmo/Windy duplicate candidates."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
NETATMO_DB = ROOT / "data" / "netatmo_pws_stations.sqlite"
WINDY_DB = ROOT / "data" / "pws_stations.sqlite"
NETATMO_MEASURE_URL = "https://api.netatmo.com/api/getmeasure"
WINDY_URL = "https://stations.windy.com/api/v2/opendata/station"


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.radians
    dlat = rad(lat2 - lat1)
    dlon = rad(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(value)))


def _median_resolution(epochs: list[int]) -> int | None:
    ordered = sorted(set(epoch for epoch in epochs if epoch > 0))
    diffs = [ordered[index] - ordered[index - 1] for index in range(1, len(ordered))]
    diffs = [diff for diff in diffs if diff > 0]
    return int(round(statistics.median(diffs))) if diffs else None


def _temperature_module(raw_json: str) -> str | None:
    try:
        row = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    measures = row.get("measures") if isinstance(row, dict) else None
    if not isinstance(measures, dict):
        return None
    for module_id, measure in measures.items():
        types = measure.get("type") if isinstance(measure, dict) else None
        if isinstance(types, list) and "temperature" in [str(value).lower() for value in types]:
            return str(module_id)
    return None


def _get_json(request: urllib.request.Request, *, opener: Callable[..., Any]) -> dict[str, Any]:
    with opener(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Provider returned invalid JSON")
    return payload


def netatmo_quality(
    station_id: str,
    raw_json: str,
    access_token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[int | None, int]:
    module_id = _temperature_module(raw_json)
    if not module_id:
        return None, 0
    now = datetime.now(timezone.utc)
    query = urllib.parse.urlencode({
        "device_id": station_id,
        "module_id": module_id,
        "scale": "max",
        "type": "temperature,humidity",
        "date_begin": int((now - timedelta(days=1)).timestamp()),
        "date_end": int(now.timestamp()),
        "optimize": "false",
        "real_time": "true",
    })
    request = urllib.request.Request(
        f"{NETATMO_MEASURE_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    payload = _get_json(request, opener=opener)
    body = payload.get("body")
    epochs = [int(value) for value in body.keys()] if isinstance(body, dict) else []
    return _median_resolution(epochs), 2


def windy_quality(
    station_id: str,
    api_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[int | None, int]:
    request = urllib.request.Request(
        f"{WINDY_URL}/{urllib.parse.quote(station_id, safe='')}/observation",
        headers={"windy-api-key": api_key, "Accept": "application/json"},
    )
    payload = _get_json(request, opener=opener)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    epochs = []
    for raw in data.get("ts", []) if isinstance(data.get("ts"), list) else []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        epochs.append(value // 1000 if value > 100_000_000_000 else value)
    sensor_fields = {"temp", "rh", "pressure", "wind", "wind_dir", "wind_gust", "precip_1h", "uv"}
    sensor_count = sum(
        isinstance(data.get(field), list) and any(value is not None for value in data[field])
        for field in sensor_fields
    )
    return _median_resolution(epochs), int(sensor_count)


def preferred_provider(
    netatmo_resolution_s: int | None,
    windy_resolution_s: int | None,
) -> tuple[str, str]:
    if netatmo_resolution_s is None and windy_resolution_s is not None:
        return "WINDY", "Netatmo has no public historical series"
    if windy_resolution_s is None:
        return "NETATMO", "Windy has no historical series"
    if netatmo_resolution_s is not None and windy_resolution_s + 60 < netatmo_resolution_s:
        return "WINDY", "Windy has clearly better temporal resolution"
    return "NETATMO", "Netatmo has equal or better temporal resolution"


def find_candidates(netatmo_db: Path, windy_db: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(netatmo_db) as connection:
        netatmo = connection.execute(
            "SELECT station_id, name, latitude, longitude, raw_json FROM netatmo_stations"
        ).fetchall()
    with sqlite3.connect(windy_db) as connection:
        windy = connection.execute(
            """
            SELECT station_id, name, latitude, longitude, station_type
            FROM pws_stations
            WHERE country = 'ES' AND latitude IS NOT NULL AND longitude IS NOT NULL
              AND LOWER(COALESCE(station_type, '')) LIKE '%netatmo%'
            """
        ).fetchall()
    candidates = []
    for net in netatmo:
        nearby = []
        for wind in windy:
            if abs(net[2] - wind[2]) > 0.003 or abs(net[3] - wind[3]) > 0.003:
                continue
            distance = _distance_m(net[2], net[3], wind[2], wind[3])
            if distance <= 150.0:
                nearby.append((distance, wind))
        if not nearby:
            continue
        distance, wind = min(nearby, key=lambda item: item[0])
        candidates.append({
            "netatmo_station_id": net[0], "netatmo_name": net[1], "raw_json": net[4],
            "windy_station_id": wind[0], "windy_name": wind[1],
            "distance_m": distance,
            "confidence": "high" if distance <= 50 else "probable",
        })
    return candidates


def analyze(
    netatmo_db: Path,
    windy_db: Path,
    *,
    netatmo_access_token: str,
    windy_api_key: str,
) -> list[dict[str, Any]]:
    checked_at = datetime.now(timezone.utc).isoformat()
    results = []
    for index, candidate in enumerate(find_candidates(netatmo_db, windy_db), start=1):
        net_resolution, net_sensors = netatmo_quality(
            candidate["netatmo_station_id"], candidate["raw_json"], netatmo_access_token,
        )
        windy_resolution, windy_sensors = windy_quality(candidate["windy_station_id"], windy_api_key)
        preferred, reason = preferred_provider(net_resolution, windy_resolution)
        results.append({
            **candidate,
            "netatmo_resolution_s": net_resolution,
            "windy_resolution_s": windy_resolution,
            "netatmo_sensor_count": net_sensors,
            "windy_sensor_count": windy_sensors,
            "preferred_provider": preferred,
            "reason": reason,
            "checked_at": checked_at,
        })
        print(
            f"{index}: {candidate['netatmo_name']} / {candidate['windy_name']} "
            f"{candidate['distance_m']:.0f}m net={net_resolution}s windy={windy_resolution}s -> {preferred}",
            flush=True,
        )
    with sqlite3.connect(netatmo_db) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS windy_duplicate_candidates (
                netatmo_station_id TEXT NOT NULL,
                windy_station_id TEXT NOT NULL,
                distance_m REAL NOT NULL,
                confidence TEXT NOT NULL,
                netatmo_resolution_s INTEGER,
                windy_resolution_s INTEGER,
                netatmo_sensor_count INTEGER,
                windy_sensor_count INTEGER,
                preferred_provider TEXT,
                reason TEXT,
                checked_at TEXT,
                PRIMARY KEY(netatmo_station_id, windy_station_id)
            )
            """
        )
        connection.execute("DELETE FROM windy_duplicate_candidates")
        connection.executemany(
            """
            INSERT INTO windy_duplicate_candidates(
                netatmo_station_id, windy_station_id, distance_m, confidence,
                netatmo_resolution_s, windy_resolution_s, netatmo_sensor_count,
                windy_sensor_count, preferred_provider, reason, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(
                row["netatmo_station_id"], row["windy_station_id"], row["distance_m"],
                row["confidence"], row["netatmo_resolution_s"], row["windy_resolution_s"],
                row["netatmo_sensor_count"], row["windy_sensor_count"],
                row["preferred_provider"], row["reason"], row["checked_at"],
            ) for row in results],
        )
        connection.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netatmo-db", type=Path, default=NETATMO_DB)
    parser.add_argument("--windy-db", type=Path, default=WINDY_DB)
    parser.add_argument("--netatmo-access-token", default=os.environ.get("METEOLABX_NETATMO_ACCESS_TOKEN", ""))
    parser.add_argument("--windy-api-key", default=os.environ.get("METEOLABX_WINDY_API_KEY", ""))
    args = parser.parse_args()
    if not args.netatmo_access_token or not args.windy_api_key:
        raise SystemExit("Netatmo access token and Windy API key are required")
    results = analyze(
        args.netatmo_db, args.windy_db,
        netatmo_access_token=args.netatmo_access_token,
        windy_api_key=args.windy_api_key,
    )
    print(f"Saved {len(results)} duplicate evaluations to {args.netatmo_db}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Enrich the Met Office station inventory with sensor availability flags.

The Weather DataHub land-observations endpoint is queried once per geohash.
Sensor presence is inferred from whether any returned observation includes a
field from that sensor family.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METOFFICE_STATIONS_PATH
METOFFICE_API_KEY = str(
    os.getenv("METEOLABX_METOFFICE_API_KEY") or os.getenv("METOFFICE_API_KEY") or ""
).strip()

BASE_URL = os.getenv("METOFFICE_BASE_URL", "https://data.hub.api.metoffice.gov.uk").rstrip("/")
TIMEOUT_SECONDS = 20

SENSOR_FIELDS = {
    "thermometer": ("temperature", "screen_temperature"),
    "hygrometer": ("humidity", "relative_humidity"),
    "barometer": ("mslp", "pressure", "station_pressure"),
    "anemometer": ("wind_speed", "wind_gust", "wind_speed_10m", "wind_gust_10m"),
    "wind_vane": ("wind_direction", "wind_from_direction"),
    "rain_gauge": (
        "precipitation",
        "precipitation_rate",
        "precipitation_amount",
        "rainfall",
        "rainfall_rate",
    ),
    "pyranometer": (
        "solar_radiation",
        "global_radiation",
        "shortwave_radiation",
        "sunshine",
    ),
    "uv": ("uv", "uv_index", "uvi"),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_value(record: Dict[str, Any], fields: Iterable[str]) -> bool:
    for field in fields:
        if field not in record:
            continue
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _observations_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "observations", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
    return []


def _fetch_observations(geohash: str, api_key: str) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/observation-land/1/{quote(str(geohash).strip().lower())}",
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
            "apikey": api_key,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _observations_from_payload(response.json())


def _station_sensors(observations: List[Dict[str, Any]]) -> Dict[str, bool]:
    return {
        sensor_key: any(
            isinstance(row, dict) and _has_value(row, fields)
            for row in observations
        )
        for sensor_key, fields in SENSOR_FIELDS.items()
    }


def _empty_sensors() -> Dict[str, bool]:
    return {sensor_key: False for sensor_key in SENSOR_FIELDS}


def enrich_inventory(
    stations: List[Dict[str, Any]],
    *,
    api_key: str,
    output_path: Path,
    resume: bool,
    save_every: int,
    sleep_seconds: float,
    max_stations: int,
) -> None:
    selected = [
        station
        for station in stations
        if isinstance(station, dict)
        and str(station.get("geohash") or station.get("id") or station.get("source_id") or "").strip()
    ]
    if max_stations > 0:
        selected = selected[:max_stations]

    completed = 0
    total = len(selected)
    for index, station in enumerate(selected, start=1):
        geohash = str(station.get("geohash") or station.get("id") or station.get("source_id") or "").strip().lower()
        if resume and isinstance(station.get("sensors"), dict):
            completed += 1
            continue
        try:
            observations = _fetch_observations(geohash, api_key)
            station["sensors"] = _station_sensors(observations)
        except Exception as exc:
            station["sensors"] = _empty_sensors()
            station["sensor_probe_error"] = str(exc)[:240]
            print(f"[{index}/{total}] {geohash}: error {exc}", flush=True)
        else:
            station.pop("sensor_probe_error", None)
            true_keys = [key for key, value in station["sensors"].items() if value]
            print(f"[{index}/{total}] {geohash}: {','.join(true_keys) or '-'}", flush=True)

        completed += 1
        if save_every > 0 and completed % save_every == 0:
            _save_json(output_path, stations)
        if sleep_seconds > 0:
            time.sleep(float(sleep_seconds))

    _save_json(output_path, stations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Met Office sensor true/false flags to the station inventory."
    )
    parser.add_argument("--api-key", default=str(METOFFICE_API_KEY or ""))
    parser.add_argument("--input", default=str(METOFFICE_STATIONS_PATH))
    parser.add_argument("--output", default=str(METOFFICE_STATIONS_PATH))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--save-every", type=int, default=25)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--max-stations", type=int, default=0)
    args = parser.parse_args()

    api_key = str(args.api_key or "").strip()
    if not api_key:
        print("Missing METOFFICE API key", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    output_path = Path(args.output)
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        print(f"Expected a station list in {input_path}", file=sys.stderr)
        return 2

    enrich_inventory(
        stations,
        api_key=api_key,
        output_path=output_path,
        resume=bool(args.resume),
        save_every=int(args.save_every),
        sleep_seconds=float(args.sleep),
        max_stations=int(args.max_stations),
    )

    counts = {
        sensor_key: sum(
            1
            for station in stations
            if isinstance(station.get("sensors"), dict)
            and bool(station["sensors"].get(sensor_key))
        )
        for sensor_key in SENSOR_FIELDS
    }
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

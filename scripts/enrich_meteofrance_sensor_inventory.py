#!/usr/bin/env python3
"""
Enrich the Meteo-France station inventory with sensor availability flags.

Meteo-France DPObs exposes station observations through `/station/horaire`.
Unlike Meteocat, there is no global endpoint for all regular station IDs, so
this script probes each station and infers sensor presence from returned fields.
It is resumable and saves progress periodically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METEOFRANCE_STATIONS_PATH
METEOFRANCE_API_KEY = str(
    os.getenv("METEOLABX_METEOFRANCE_API_KEY") or os.getenv("METEOFRANCE_API_KEY") or ""
).strip()

BASE_URL = os.getenv(
    "METEOFRANCE_BASE_URL",
    "https://public-api.meteofrance.fr/public/DPObs/v1",
).rstrip("/")
TIMEOUT_SECONDS = 20

SENSOR_FIELDS = {
    "thermometer": ("t", "td", "tx", "tn"),
    "hygrometer": ("u", "ux", "un"),
    "barometer": ("pres", "pmer"),
    "anemometer": ("ff", "fxi", "fxy", "fxi10"),
    "wind_vane": ("dd", "dxi", "dxy", "dxi10"),
    "rain_gauge": ("rr1", "rr_per"),
    "pyranometer": ("ray_glo01", "insolh"),
    "uv": ("uv", "uvi", "uv_indice", "indice_uv"),
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


def _request_station_hour(station_id: str, query_date: str, api_key: str) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/station/horaire",
        params={
            "id_station": str(station_id).strip(),
            "date": query_date,
            "format": "json",
        },
        headers={
            "apikey": api_key,
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _query_dates(hours_back: int) -> List[str]:
    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        (now_utc - timedelta(hours=offset)).isoformat().replace("+00:00", "Z")
        for offset in range(max(0, int(hours_back)) + 1)
    ]


def _station_sensors(station_id: str, api_key: str, dates: List[str]) -> Dict[str, bool]:
    rows: List[Dict[str, Any]] = []
    last_error: Optional[Exception] = None
    for query_date in dates:
        try:
            rows = _request_station_hour(station_id, query_date, api_key)
        except Exception as exc:
            last_error = exc
            continue
        if rows:
            break
    if not rows and last_error is not None:
        raise last_error

    return {
        sensor_key: any(
            isinstance(row, dict) and _has_value(row, fields)
            for row in rows
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
    hours_back: int,
) -> None:
    dates = _query_dates(hours_back)
    completed = 0
    selected = [
        station
        for station in stations
        if isinstance(station, dict) and str(station.get("id_station") or station.get("id") or "").strip()
    ]
    if max_stations > 0:
        selected = selected[:max_stations]

    total = len(selected)
    for index, station in enumerate(selected, start=1):
        station_id = str(station.get("id_station") or station.get("id") or "").strip()
        if resume and isinstance(station.get("sensors"), dict):
            completed += 1
            continue
        try:
            station["sensors"] = _station_sensors(station_id, api_key, dates)
        except Exception as exc:
            station["sensors"] = _empty_sensors()
            station["sensor_probe_error"] = str(exc)[:240]
            print(f"[{index}/{total}] {station_id}: error {exc}")
        else:
            station.pop("sensor_probe_error", None)
            true_keys = [key for key, value in station["sensors"].items() if value]
            print(f"[{index}/{total}] {station_id}: {','.join(true_keys) or '-'}")
        completed += 1

        if save_every > 0 and completed % save_every == 0:
            _save_json(output_path, stations)
        if sleep_seconds > 0:
            time.sleep(float(sleep_seconds))

    _save_json(output_path, stations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Meteo-France sensor true/false flags to the station inventory."
    )
    parser.add_argument("--api-key", default=str(METEOFRANCE_API_KEY or ""))
    parser.add_argument("--input", default=str(METEOFRANCE_STATIONS_PATH))
    parser.add_argument("--output", default=str(METEOFRANCE_STATIONS_PATH))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--save-every", type=int, default=25)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--max-stations", type=int, default=0)
    parser.add_argument("--hours-back", type=int, default=3)
    args = parser.parse_args()

    api_key = str(args.api_key or "").strip()
    if not api_key:
        print("Missing METEOFRANCE API key", file=sys.stderr)
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
        hours_back=int(args.hours_back),
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

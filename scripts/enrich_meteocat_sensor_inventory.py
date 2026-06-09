#!/usr/bin/env python3
"""
Enrich the Meteocat station inventory with sensor availability flags.

The Meteocat `/variables/mesurades/{code}/ultimes` endpoint returns the
stations with recent readings for a measured variable. This script makes one
request per sensor family and stores true/false flags under each station's
`sensors` field.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Set

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METEOCAT_STATIONS_PATH

BASE_URL = "https://api.meteo.cat/xema/v1"
DEFAULT_API_KEY = os.getenv(
    "METEOCAT_API_KEY",
    "rZwBPl5kv05CS7NEgk9wcaqd0FFimA2f9y6ISDa2",
)
TIMEOUT_SECONDS = 20

SENSOR_VARIABLES = {
    "thermometer": 32,  # Temperatura
    "hygrometer": 33,  # Humitat relativa
    "barometer": 34,  # Pressio atmosferica
    "anemometer": 30,  # Velocitat del vent a 10 m (esc.)
    "wind_vane": 31,  # Direccio de vent 10 m
    "rain_gauge": 35,  # Precipitacio
    "pyranometer": 36,  # Irradiancia solar global
    "uv": 39,  # Radiacio UV; Meteocat currently returns no stations for this variable.
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _extract_station_codes(payload: Any) -> Set[str]:
    codes: Set[str] = set()
    if not isinstance(payload, list):
        return codes
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("codi", "")).strip().upper()
        variables = item.get("variables")
        if code and isinstance(variables, list) and variables:
            codes.add(code)
    return codes


def _fetch_sensor_station_codes(sensor_key: str, api_key: str, variable_code: int) -> Set[str]:
    url = f"{BASE_URL}/variables/mesurades/{int(variable_code)}/ultimes"
    response = requests.get(
        url,
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    if sensor_key == "uv" and response.status_code == 400:
        return set()
    response.raise_for_status()
    return _extract_station_codes(response.json())


def enrich_inventory(stations: Iterable[Dict[str, Any]], api_key: str) -> Dict[str, Set[str]]:
    sensor_station_codes: Dict[str, Set[str]] = {}
    for sensor_key, variable_code in SENSOR_VARIABLES.items():
        codes = _fetch_sensor_station_codes(sensor_key, api_key, variable_code)
        sensor_station_codes[sensor_key] = codes
        print(f"{sensor_key}: {len(codes)} stations from variable {variable_code}")

    for station in stations:
        if not isinstance(station, dict):
            continue
        station_code = str(station.get("codi", "")).strip().upper()
        station["sensors"] = {
            sensor_key: station_code in station_codes
            for sensor_key, station_codes in sensor_station_codes.items()
        }

    return sensor_station_codes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Meteocat sensor true/false flags to the station inventory."
    )
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--input", default=str(METEOCAT_STATIONS_PATH))
    parser.add_argument("--output", default=str(METEOCAT_STATIONS_PATH))
    args = parser.parse_args()

    api_key = str(args.api_key or "").strip()
    if not api_key:
        print("Missing METEOCAT API key", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    output_path = Path(args.output)
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        print(f"Expected a station list in {input_path}", file=sys.stderr)
        return 2

    sensor_station_codes = enrich_inventory(stations, api_key)
    inventory_codes = {
        str(station.get("codi", "")).strip().upper()
        for station in stations
        if isinstance(station, dict)
    }
    for sensor_key, codes in sensor_station_codes.items():
        matched = len(codes.intersection(inventory_codes))
        print(f"{sensor_key}: {matched}/{len(inventory_codes)} inventory stations marked true")

    _save_json(output_path, stations)
    print(f"Saved {len(stations)} stations to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

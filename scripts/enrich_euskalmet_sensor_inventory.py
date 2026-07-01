#!/usr/bin/env python3
"""
Enrich the Euskalmet station inventory with sensor availability flags.

Euskalmet needs a station -> measure -> sensor map to request readings. That
map already tells us which weather measure families are available per station,
so this script converts it to the common `sensors` boolean object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import EUSKALMET_SENSOR_MAP_PATH, EUSKALMET_STATIONS_PATH

SENSOR_MEASURES = {
    "thermometer": ("measuresForAir/temperature",),
    "hygrometer": ("measuresForAir/humidity",),
    "barometer": (
        "measuresForAtmosphere/pressure",
        "measuresForAtmosphere/sea_level_pressure",
    ),
    "anemometer": (
        "measuresForWind/mean_speed",
        "measuresForWind/max_speed",
    ),
    "wind_vane": ("measuresForWind/mean_direction",),
    "rain_gauge": ("measuresForWater/precipitation",),
    "pyranometer": ("measuresForSun/irradiance",),
    "uv": (),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _empty_sensors() -> Dict[str, bool]:
    return {sensor_key: False for sensor_key in SENSOR_MEASURES}


def _station_sensors(measure_map: Dict[str, Any]) -> Dict[str, bool]:
    sensors: Dict[str, bool] = {}
    for sensor_key, measure_keys in SENSOR_MEASURES.items():
        sensors[sensor_key] = any(
            bool(str(measure_map.get(measure_key) or "").strip())
            for measure_key in measure_keys
        )
    return sensors


def enrich_inventory(stations: List[Dict[str, Any]], sensor_map: Dict[str, Any]) -> None:
    normalized_map = {
        str(station_id or "").strip().upper(): mapping
        for station_id, mapping in sensor_map.items()
        if isinstance(mapping, dict)
    }
    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("stationId") or "").strip().upper()
        measure_map = normalized_map.get(station_id, {})
        station["sensors"] = _station_sensors(measure_map) if measure_map else _empty_sensors()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Euskalmet sensor true/false flags to the station inventory."
    )
    parser.add_argument("--input", default=str(EUSKALMET_STATIONS_PATH))
    parser.add_argument("--sensor-map", default=str(EUSKALMET_SENSOR_MAP_PATH))
    parser.add_argument("--output", default=str(EUSKALMET_STATIONS_PATH))
    args = parser.parse_args()

    input_path = Path(args.input)
    sensor_map_path = Path(args.sensor_map)
    output_path = Path(args.output)

    stations = _load_json(input_path)
    sensor_map = _load_json(sensor_map_path)
    if not isinstance(stations, list):
        print(f"Expected station list in {input_path}", file=sys.stderr)
        return 2
    if not isinstance(sensor_map, dict):
        print(f"Expected station sensor map in {sensor_map_path}", file=sys.stderr)
        return 2

    enrich_inventory(stations, sensor_map)
    _save_json(output_path, stations)

    counts = {
        sensor_key: sum(
            1
            for station in stations
            if isinstance(station.get("sensors"), dict)
            and bool(station["sensors"].get(sensor_key))
        )
        for sensor_key in SENSOR_MEASURES
    }
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

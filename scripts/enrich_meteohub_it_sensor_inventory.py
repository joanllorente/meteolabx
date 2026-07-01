#!/usr/bin/env python3
"""
Enrich the MeteoHub Italia inventory with sensor availability flags.

MeteoHub does not expose a plain sensor catalog. The local inventory already
contains observed product capabilities built from /api/observations
onlyStations=true queries. This script converts those capabilities to the
common `sensors` object and can optionally refresh the six public product
families in six global calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlencode

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METEOHUB_IT_STATIONS_PATH
from scripts.build_meteohub_inventory import _details_name, _station_key

BASE_URL = "https://meteohub.agenziaitaliameteo.it"
TIMEOUT_SECONDS = 60
LICENSE_GROUP = "CCBY_COMPLIANT"

PRODUCT_TO_SENSOR = {
    "B12101": "thermometer",
    "B13003": "hygrometer",
    "B10004": "barometer",
    "B11002": "anemometer",
    "B11001": "wind_vane",
    "B13011": "rain_gauge",
}

CAPABILITY_TO_SENSOR = {
    "temperature": "thermometer",
    "relative_humidity": "hygrometer",
    "pressure": "barometer",
    "wind_speed": "anemometer",
    "wind_direction": "wind_vane",
    "precipitation": "rain_gauge",
}

SENSOR_KEYS = (
    "thermometer",
    "hygrometer",
    "barometer",
    "anemometer",
    "wind_vane",
    "rain_gauge",
    "pyranometer",
    "uv",
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _empty_sensors() -> Dict[str, bool]:
    return {key: False for key in SENSOR_KEYS}


def _today_query(product_code: str) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        f"reftime: >={today} 00:00,<={today} 23:59;"
        f"license:{LICENSE_GROUP};"
        f"product:{product_code}"
    )


def _observations_url(product_code: str) -> str:
    params = {
        "q": _today_query(product_code),
        "onlyStations": "true",
    }
    return f"{BASE_URL}/api/observations?{urlencode(params)}"


def _station_keys_from_payload(payload: Any) -> set[str]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        stat = item.get("stat")
        if not isinstance(stat, dict):
            continue
        network = str(stat.get("net") or "").strip()
        try:
            lat = float(stat.get("lat"))
            lon = float(stat.get("lon"))
        except Exception:
            continue
        if not network:
            continue
        name = _details_name(stat.get("details")) or f"{network} {lat:.5f},{lon:.5f}"
        out.add(_station_key(network, lat, lon, name))
    return out


def _fetch_product_station_keys(product_code: str) -> set[str]:
    response = requests.get(
        _observations_url(product_code),
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _station_keys_from_payload(response.json())


def _apply_existing_capabilities(station: Dict[str, Any]) -> Dict[str, bool]:
    sensors = _empty_sensors()
    capabilities = station.get("capabilities")
    if isinstance(capabilities, dict):
        for capability, sensor_key in CAPABILITY_TO_SENSOR.items():
            if capabilities.get(capability):
                sensors[sensor_key] = True

    legacy_flags = {
        "has_temperature": "thermometer",
        "has_relative_humidity": "hygrometer",
        "has_pressure": "barometer",
        "has_wind_speed": "anemometer",
        "has_wind_direction": "wind_vane",
        "has_precipitation": "rain_gauge",
    }
    for flag_key, sensor_key in legacy_flags.items():
        if station.get(flag_key):
            sensors[sensor_key] = True

    return sensors


def enrich_inventory(stations: List[Dict[str, Any]], *, refresh: bool) -> None:
    refreshed: Dict[str, set[str]] = {}
    if refresh:
        for product_code, sensor_key in PRODUCT_TO_SENSOR.items():
            station_keys = _fetch_product_station_keys(product_code)
            refreshed[sensor_key] = station_keys
            print(f"{product_code} {sensor_key}: {len(station_keys)} stations", flush=True)

    for station in stations:
        if not isinstance(station, dict):
            continue
        sensors = _apply_existing_capabilities(station)
        station_key = str(station.get("id") or station.get("source_id") or "").strip()
        for sensor_key, station_keys in refreshed.items():
            if station_key in station_keys:
                sensors[sensor_key] = True

        # No public MeteoHub product found for piranometer/UV in the current
        # public observations API; keep them explicit and false.
        sensors["pyranometer"] = bool(sensors.get("pyranometer", False))
        sensors["uv"] = bool(sensors.get("uv", False))
        station["sensors"] = {key: bool(sensors.get(key, False)) for key in SENSOR_KEYS}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add MeteoHub Italia sensor true/false flags to the station inventory."
    )
    parser.add_argument("--input", default=str(METEOHUB_IT_STATIONS_PATH))
    parser.add_argument("--output", default=str(METEOHUB_IT_STATIONS_PATH))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh six classic product families with global onlyStations calls.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        print(f"Expected a station list in {input_path}", file=sys.stderr)
        return 2

    enrich_inventory(stations, refresh=bool(args.refresh))
    _save_json(output_path, stations)

    counts = {
        sensor_key: sum(
            1
            for station in stations
            if isinstance(station.get("sensors"), dict)
            and bool(station["sensors"].get(sensor_key))
        )
        for sensor_key in SENSOR_KEYS
    }
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

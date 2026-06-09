#!/usr/bin/env python3
"""
Enrich the AEMET station inventory with sensor availability flags.

AEMET's `/observacion/convencional/todas` endpoint returns recent observation
records for all stations. Fields that are not measured by a station are omitted
from its records, so sensor presence is inferred from whether any recent record
for that station includes a field for that sensor family.
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

from data_files import AEMET_STATIONS_PATH

BASE_URL = "https://opendata.aemet.es/opendata/api"
DEFAULT_API_KEY = os.getenv(
    "AEMET_API_KEY",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXRlb2xhYnhAZ21haWwuY29tIiwianRpIjoi"
    "NTdkMzE1MjYtMTk4My00YzNiLTgzNjAtYTdkZWJmMmIxMDFhIiwiaXNzIjoiQUVNRVQi"
    "LCJpYXQiOjE3NzAyNDQ1OTEsInVzZXJJZCI6IjU3ZDMxNTI2LTE5ODMtNGMzYi04MzYw"
    "LWE3ZGViZjJiMTAxYSIsInJvbGUiOiIifQ"
    ".GvliQHY3f94N691sU0ExhMHZxbTiGn2BCe-bIA22K8c",
)
TIMEOUT_SECONDS = 90

SENSOR_FIELDS = {
    "thermometer": ("ta", "tamin", "tamax"),
    "hygrometer": ("hr",),
    "barometer": ("pres", "pres_nmar"),
    "anemometer": ("vv", "vmax", "vvu", "vmaxu"),
    "wind_vane": ("dv", "dmax", "dvu", "dmaxu"),
    "rain_gauge": ("prec",),
    "pyranometer": ("inso",),
    "uv": (),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_json_response(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        raw = response.content
        for encoding in ("utf-8", "latin-1"):
            try:
                return json.loads(raw.decode(encoding))
            except Exception:
                continue
        raise


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


def _fetch_all_current_records(api_key: str) -> Any:
    response = requests.get(
        f"{BASE_URL}/observacion/convencional/todas",
        headers={"api_key": api_key, "Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    meta = _decode_json_response(response)
    if not isinstance(meta, dict) or int(meta.get("estado", 0) or 0) != 200:
        raise RuntimeError(f"AEMET metadata error: {meta}")

    data_url = str(meta.get("datos", "")).strip()
    if not data_url:
        raise RuntimeError(f"AEMET metadata response has no datos URL: {meta}")

    data_response = requests.get(data_url, timeout=TIMEOUT_SECONDS)
    data_response.raise_for_status()
    return _decode_json_response(data_response)


def _sensor_station_codes(records: Any) -> Dict[str, Set[str]]:
    sensor_codes: Dict[str, Set[str]] = {sensor_key: set() for sensor_key in SENSOR_FIELDS}
    if not isinstance(records, list):
        return sensor_codes

    for record in records:
        if not isinstance(record, dict):
            continue
        station_id = str(record.get("idema", "")).strip().upper()
        if not station_id:
            continue
        for sensor_key, fields in SENSOR_FIELDS.items():
            if fields and _has_value(record, fields):
                sensor_codes[sensor_key].add(station_id)

    return sensor_codes


def enrich_inventory(inventory: Dict[str, Any], api_key: str) -> Dict[str, Set[str]]:
    stations = inventory.get("estaciones")
    if not isinstance(stations, list):
        raise RuntimeError("Expected AEMET inventory with an 'estaciones' list")

    records = _fetch_all_current_records(api_key)
    sensor_codes = _sensor_station_codes(records)
    print(f"records: {len(records) if isinstance(records, list) else 0}")

    for sensor_key, station_ids in sensor_codes.items():
        print(f"{sensor_key}: {len(station_ids)} stations from current records")

    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("idema", "")).strip().upper()
        station["sensors"] = {
            sensor_key: station_id in station_ids
            for sensor_key, station_ids in sensor_codes.items()
        }

    return sensor_codes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add AEMET sensor true/false flags to the station inventory."
    )
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--input", default=str(AEMET_STATIONS_PATH))
    parser.add_argument("--output", default=str(AEMET_STATIONS_PATH))
    args = parser.parse_args()

    api_key = str(args.api_key or "").strip()
    if not api_key:
        print("Missing AEMET API key", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    output_path = Path(args.output)
    inventory = _load_json(input_path)
    if not isinstance(inventory, dict):
        print(f"Expected an AEMET inventory object in {input_path}", file=sys.stderr)
        return 2

    sensor_codes = enrich_inventory(inventory, api_key)
    stations = inventory.get("estaciones", [])
    inventory_ids = {
        str(station.get("idema", "")).strip().upper()
        for station in stations
        if isinstance(station, dict)
    }
    for sensor_key, station_ids in sensor_codes.items():
        matched = len(station_ids.intersection(inventory_ids))
        print(f"{sensor_key}: {matched}/{len(inventory_ids)} inventory stations marked true")

    _save_json(output_path, inventory)
    print(f"Saved {len(stations)} stations to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

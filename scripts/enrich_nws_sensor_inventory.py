#!/usr/bin/env python3
"""
Enrich the NWS/weather.gov station inventory with sensor availability flags.

weather.gov does not expose a station sensor catalog. Sensor presence is
therefore inferred from non-null fields in each station's latest observation.
Solar radiation and UV are not exposed by the NWS observation schema used by
the app, so they remain false.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import quote

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import NWS_STATIONS_PATH
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "MeteoLabX/1.0 (contact: meteolabx@gmail.com)")

BASE_URL = "https://api.weather.gov"
TIMEOUT_SECONDS = 18

SENSOR_FIELDS = {
    "thermometer": ("temperature", "dewpoint"),
    "hygrometer": ("relativeHumidity",),
    "barometer": ("barometricPressure", "seaLevelPressure"),
    "anemometer": ("windSpeed", "windGust"),
    "wind_vane": ("windDirection",),
    "rain_gauge": (
        "precipitationLastHour",
        "precipitationLast3Hours",
        "precipitationLast6Hours",
    ),
    "pyranometer": (),
    "uv": (),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _empty_sensors() -> Dict[str, bool]:
    return {sensor_key: False for sensor_key in SENSOR_FIELDS}


def _has_value(props: Dict[str, Any], fields: Iterable[str]) -> bool:
    for field in fields:
        raw = props.get(field)
        if isinstance(raw, dict):
            value = raw.get("value")
        else:
            value = raw
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _sensors_from_feature(feature: Dict[str, Any]) -> Dict[str, bool]:
    props = feature.get("properties") if isinstance(feature, dict) else {}
    if not isinstance(props, dict):
        props = {}
    return {
        sensor_key: _has_value(props, fields)
        for sensor_key, fields in SENSOR_FIELDS.items()
    }


def _fetch_latest(station_id: str) -> Tuple[str, Dict[str, bool], str]:
    sid = str(station_id or "").strip().upper()
    if not sid:
        return sid, _empty_sensors(), "empty station id"
    url = f"{BASE_URL}/stations/{quote(sid)}/observations/latest"
    try:
        response = requests.get(
            url,
            headers={
                "Accept": "application/geo+json",
                "User-Agent": NWS_USER_AGENT,
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return sid, _empty_sensors(), str(exc)[:240]
    if not isinstance(payload, dict):
        return sid, _empty_sensors(), "invalid response"
    return sid, _sensors_from_feature(payload), ""


def _selected_stations(stations: List[Dict[str, Any]], *, resume: bool, max_stations: int) -> List[Dict[str, Any]]:
    selected = [
        station
        for station in stations
        if isinstance(station, dict) and str(station.get("id") or "").strip()
    ]
    if resume:
        selected = [station for station in selected if not isinstance(station.get("sensors"), dict)]
    if max_stations > 0:
        selected = selected[:max_stations]
    return selected


def enrich_inventory(
    stations: List[Dict[str, Any]],
    *,
    output_path: Path,
    resume: bool,
    max_workers: int,
    save_every: int,
    max_stations: int,
) -> None:
    by_id = {
        str(station.get("id") or "").strip().upper(): station
        for station in stations
        if isinstance(station, dict)
    }
    selected = _selected_stations(stations, resume=resume, max_stations=max_stations)
    total = len(selected)
    if total == 0:
        _save_json(output_path, stations)
        return

    completed = 0
    errors = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        futures = {
            executor.submit(_fetch_latest, str(station.get("id") or "")): str(station.get("id") or "").strip().upper()
            for station in selected
        }
        for future in as_completed(futures):
            station_id, sensors, error = future.result()
            station = by_id.get(station_id)
            if station is not None:
                station["sensors"] = sensors
                if error:
                    station["sensor_probe_error"] = error
                    errors += 1
                else:
                    station.pop("sensor_probe_error", None)
            completed += 1
            if completed % max(1, int(save_every)) == 0:
                _save_json(output_path, stations)
                elapsed = max(0.001, time.time() - started)
                rate = completed / elapsed
                print(
                    f"{completed}/{total} processed "
                    f"({rate:.1f}/s, errors={errors})",
                    flush=True,
                )

    _save_json(output_path, stations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add NWS sensor true/false flags to the station inventory."
    )
    parser.add_argument("--input", default=str(NWS_STATIONS_PATH))
    parser.add_argument("--output", default=str(NWS_STATIONS_PATH))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--save-every", type=int, default=500)
    parser.add_argument("--max-stations", type=int, default=0)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        print(f"Expected station list in {input_path}", file=sys.stderr)
        return 2

    enrich_inventory(
        stations,
        output_path=output_path,
        resume=bool(args.resume),
        max_workers=int(args.max_workers),
        save_every=int(args.save_every),
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
    errors = sum(1 for station in stations if isinstance(station, dict) and station.get("sensor_probe_error"))
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    print(f"errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

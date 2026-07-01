#!/usr/bin/env python3
"""
Enrich the MeteoGalicia station inventory with sensor availability flags.

The 10-minute endpoint accepts comma-separated station ids, so the whole local
inventory can be probed in a single request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METEOGALICIA_STATIONS_PATH
from server.services.meteogalicia import TENMIN_ENDPOINT

TIMEOUT_SECONDS = 30

SENSOR_CODES = {
    "thermometer": ("TA_",),
    "hygrometer": ("HR_",),
    "barometer": ("PR_", "PRED_"),
    "anemometer": ("VV_AVG_", "VV_RACHA_"),
    "wind_vane": ("DV_AVG_", "DV_CONDICION_"),
    "rain_gauge": ("PP_",),
    "pyranometer": ("RS_", "RD_", "RREF_"),
    "uv": ("BIO_",),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _station_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        stations = payload.get("listaEstacionsMeteo", [])
    else:
        stations = payload
    return stations if isinstance(stations, list) else []


def _empty_sensors() -> Dict[str, bool]:
    return {sensor_key: False for sensor_key in SENSOR_CODES}


def _valid_measure(measure: Dict[str, Any]) -> bool:
    validation = measure.get("lnCodigoValidacion")
    try:
        if int(validation) in (3, 9):
            return False
    except Exception:
        pass

    value = measure.get("valor")
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > -9999


def _measure_sensor(code_raw: Any) -> str:
    code = str(code_raw or "").strip().upper().replace(" ", "")
    for sensor_key, prefixes in SENSOR_CODES.items():
        if any(code.startswith(prefix) for prefix in prefixes):
            return sensor_key
    return ""


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("listUltimos10min", "listaUltimos10min", "ultimos10min"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _fetch_latest_items(station_ids: Iterable[str]) -> List[Dict[str, Any]]:
    params = {"idEst": ",".join(str(station_id).strip() for station_id in station_ids if str(station_id).strip())}
    response = requests.get(
        TENMIN_ENDPOINT,
        params=params,
        headers={"Accept": "application/json", "User-Agent": "MeteoLabX/1.0"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _extract_items(response.json())


def _sensors_by_station(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, bool]]:
    out: Dict[str, Dict[str, bool]] = {}
    for item in items:
        station_id = str(item.get("idEstacion") or "").strip()
        if not station_id:
            continue
        sensors = out.setdefault(station_id, _empty_sensors())
        measures = item.get("listaMedidas", [])
        if not isinstance(measures, list):
            continue
        for measure in measures:
            if not isinstance(measure, dict) or not _valid_measure(measure):
                continue
            sensor_key = _measure_sensor(measure.get("codigoParametro"))
            if sensor_key:
                sensors[sensor_key] = True
    return out


def enrich_inventory(payload: Any, output_path: Path) -> None:
    stations = _station_list(payload)
    station_ids = [str(station.get("idEstacion") or "").strip() for station in stations if isinstance(station, dict)]
    items = _fetch_latest_items(station_ids)
    found = _sensors_by_station(items)

    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("idEstacion") or "").strip()
        station["sensors"] = found.get(station_id, _empty_sensors())
        if station_id not in found:
            station["sensor_probe_error"] = "No station block returned by ultimos10minEstacionsMeteo.action"
        else:
            station.pop("sensor_probe_error", None)

    _save_json(output_path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add MeteoGalicia sensor true/false flags to the station inventory."
    )
    parser.add_argument("--input", default=str(METEOGALICIA_STATIONS_PATH))
    parser.add_argument("--output", default=str(METEOGALICIA_STATIONS_PATH))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = _load_json(input_path)
    stations = _station_list(payload)
    if not stations:
        print(f"Expected station inventory in {input_path}", file=sys.stderr)
        return 2

    enrich_inventory(payload, output_path)

    counts = {
        sensor_key: sum(
            1
            for station in stations
            if isinstance(station.get("sensors"), dict)
            and bool(station["sensors"].get(sensor_key))
        )
        for sensor_key in SENSOR_CODES
    }
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Enrich the Frost station inventory with sensor availability flags.

Frost exposes time-series metadata through /observations/availableTimeSeries.
That endpoint accepts several elements and no source filter, so the local
inventory can be enriched from one metadata request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import FROST_STATIONS_PATH

FROST_BASE_URL = os.getenv("FROST_BASE_URL", "https://frost.met.no").rstrip("/")
FROST_CLIENT_ID = (
    os.getenv("METEOLABX_FROST_CLIENT_ID", "")
    or os.getenv("FROST_CLIENT_ID", "")
).strip()
FROST_CLIENT_SECRET = (
    os.getenv("METEOLABX_FROST_CLIENT_SECRET", "")
    or os.getenv("FROST_CLIENT_SECRET", "")
).strip()
if not FROST_CLIENT_ID or not FROST_CLIENT_SECRET:
    try:
        from dotenv import dotenv_values

        _local_env = dotenv_values(ROOT_DIR / ".env")
        FROST_CLIENT_ID = FROST_CLIENT_ID or str(
            _local_env.get("METEOLABX_FROST_CLIENT_ID") or _local_env.get("FROST_CLIENT_ID") or ""
        ).strip()
        FROST_CLIENT_SECRET = FROST_CLIENT_SECRET or str(
            _local_env.get("METEOLABX_FROST_CLIENT_SECRET") or _local_env.get("FROST_CLIENT_SECRET") or ""
        ).strip()
    except Exception:
        pass


def _request_json(endpoint: str, params: Dict[str, Any]) -> Any:
    if not FROST_CLIENT_ID or not FROST_CLIENT_SECRET:
        raise RuntimeError("Faltan credenciales Frost en el entorno.")
    response = requests.get(
        f"{FROST_BASE_URL}/{endpoint.lstrip('/')}",
        params=params,
        auth=(FROST_CLIENT_ID, FROST_CLIENT_SECRET),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

SENSOR_ELEMENTS = {
    "thermometer": ("air_temperature",),
    "hygrometer": ("relative_humidity",),
    "barometer": (
        "surface_air_pressure",
        "air_pressure",
        "air_pressure_at_sea_level",
        "air_pressure_at_sea_level_qnh",
    ),
    "anemometer": ("wind_speed", "wind_speed_of_gust"),
    "wind_vane": ("wind_from_direction",),
    "rain_gauge": (
        "accumulated(precipitation_amount)",
        "sum(precipitation_amount PT1M)",
        "sum(precipitation_amount PT10M)",
        "sum(precipitation_amount PT1H)",
        "precipitation_amount",
    ),
    "pyranometer": (
        "mean(surface_downwelling_shortwave_flux_in_air PT1M)",
        "mean(surface_downwelling_shortwave_flux_in_air PT10M)",
        "mean(surface_downwelling_shortwave_flux_in_air PT1H)",
        "max(mean(surface_downwelling_shortwave_flux_in_air PT1M) PT1H)",
    ),
    "uv": (),
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _parse_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_current(item: Dict[str, Any], now_utc: datetime) -> bool:
    valid_from = _parse_iso(item.get("validFrom")) or datetime.min.replace(tzinfo=timezone.utc)
    valid_to = _parse_iso(item.get("validTo")) or datetime.max.replace(tzinfo=timezone.utc)
    return valid_from <= now_utc <= valid_to


def _base_source_id(source_id: Any) -> str:
    return str(source_id or "").strip().upper().split(":", 1)[0]


def _empty_sensors() -> Dict[str, bool]:
    return {sensor_key: False for sensor_key in SENSOR_ELEMENTS}


def _element_to_sensor() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sensor_key, elements in SENSOR_ELEMENTS.items():
        for element in elements:
            out[element] = sensor_key
    return out


def _all_query_elements() -> List[str]:
    elements: List[str] = []
    for values in SENSOR_ELEMENTS.values():
        for element in values:
            if element not in elements:
                elements.append(element)
    return elements


def _fetch_available_timeseries(elements: Iterable[str]) -> Dict[str, Any]:
    return _request_json(
        "/observations/availableTimeSeries/v0.jsonld",
        {"elements": ",".join(elements)},
    )


def _sensor_sets_from_payload(payload: Any, *, current_only: bool) -> Dict[str, Dict[str, bool]]:
    element_to_sensor = _element_to_sensor()
    now_utc = datetime.now(timezone.utc)
    out: Dict[str, Dict[str, bool]] = {}
    data = payload.get("data") if isinstance(payload, dict) else []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        if current_only and not _is_current(item, now_utc):
            continue
        source_id = _base_source_id(item.get("sourceId"))
        element_id = str(item.get("elementId") or "").strip()
        sensor_key = element_to_sensor.get(element_id)
        if not source_id or not sensor_key:
            continue
        sensors = out.setdefault(source_id, _empty_sensors())
        sensors[sensor_key] = True
    return out


def _series_inventory_from_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    element_to_sensor = _element_to_sensor()
    now_utc = datetime.now(timezone.utc)
    out: Dict[str, Dict[str, Any]] = {}
    data = payload.get("data") if isinstance(payload, dict) else []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        station_id = _base_source_id(item.get("sourceId"))
        sensor_key = element_to_sensor.get(str(item.get("elementId") or "").strip())
        if not station_id or not sensor_key:
            continue
        entry = out.setdefault(station_id, {
            "all_sensors": _empty_sensors(), "current_sensors": _empty_sensors(),
            "start": None, "end": None, "open": False,
        })
        entry["all_sensors"][sensor_key] = True
        start = _parse_iso(item.get("validFrom"))
        end = _parse_iso(item.get("validTo"))
        if start and (entry["start"] is None or start < entry["start"]):
            entry["start"] = start
        if end is None:
            entry["open"] = True
        elif entry["end"] is None or end > entry["end"]:
            entry["end"] = end
        if _is_current(item, now_utc):
            entry["current_sensors"][sensor_key] = True
    return out


def enrich_inventory(
    stations: List[Dict[str, Any]], *, current_only: bool, minimum_history_days: int = 365,
    payload: Any = None,
) -> Dict[str, Any]:
    if payload is None:
        payload = _fetch_available_timeseries(_all_query_elements())
    series = _series_inventory_from_payload(payload)
    kept: List[Dict[str, Any]] = []
    removed_no_weather: List[str] = []
    removed_short_history: List[str] = []
    now_utc = datetime.now(timezone.utc)
    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = _base_source_id(station.get("id") or station.get("source_id"))
        metadata = series.get(station_id)
        if not metadata or not any(metadata["all_sensors"].values()):
            removed_no_weather.append(station_id)
            continue
        active_now = any(metadata["current_sensors"].values())
        start = metadata["start"]
        effective_end = now_utc if metadata["open"] else metadata["end"]
        history_days = (effective_end - start).days if start and effective_end else 0
        if not active_now and history_days < max(0, minimum_history_days):
            removed_short_history.append(station_id)
            continue
        station["active_now"] = active_now
        station["status"] = "active" if active_now else "historical"
        station["historical"] = not active_now
        station["has_historical"] = history_days >= max(0, minimum_history_days)
        station["series_start"] = start.isoformat().replace("+00:00", "Z") if start else None
        station["series_end"] = None if active_now and metadata["open"] else (
            metadata["end"].isoformat().replace("+00:00", "Z") if metadata["end"] else None
        )
        station["sensors"] = (
            metadata["current_sensors"] if active_now and current_only else metadata["all_sensors"]
        )
        kept.append(station)
    stations[:] = kept
    return {
        "result_count": len(kept),
        "active_count": sum(bool(row.get("active_now")) for row in kept),
        "historical_count": sum(not bool(row.get("active_now")) for row in kept),
        "removed_no_weather_series": sorted(removed_no_weather),
        "removed_short_history": sorted(removed_short_history),
        "minimum_history_days": max(0, minimum_history_days),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Frost sensor true/false flags to the station inventory."
    )
    parser.add_argument("--input", default=str(FROST_STATIONS_PATH))
    parser.add_argument("--output", default=str(FROST_STATIONS_PATH))
    parser.add_argument(
        "--include-historical",
        action="store_true",
        help="Use any known time series instead of only currently valid ones.",
    )
    parser.add_argument("--minimum-history-days", type=int, default=365)
    parser.add_argument("--payload-file", type=Path)
    parser.add_argument(
        "--report", type=Path,
        default=ROOT_DIR / "data" / "frost_series_validation.json",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        print(f"Expected a station list in {input_path}", file=sys.stderr)
        return 2

    payload = _load_json(args.payload_file) if args.payload_file else None
    report = enrich_inventory(
        stations,
        current_only=not bool(args.include_historical),
        minimum_history_days=max(0, args.minimum_history_days),
        payload=payload,
    )
    _save_json(output_path, stations)
    _save_json(args.report, report)

    counts = {
        sensor_key: sum(
            1
            for station in stations
            if isinstance(station.get("sensors"), dict)
            and bool(station["sensors"].get(sensor_key))
        )
        for sensor_key in SENSOR_ELEMENTS
    }
    print(f"Saved {len(stations)} stations to {output_path}")
    print(counts)
    print({
        "result_count": report["result_count"],
        "active_count": report["active_count"],
        "historical_count": report["historical_count"],
        "removed_no_weather_series": len(report["removed_no_weather_series"]),
        "removed_short_history": len(report["removed_short_history"]),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

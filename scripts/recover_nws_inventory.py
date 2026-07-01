#!/usr/bin/env python3
"""
Recover NWS/weather.gov stations that were omitted from the local inventory.

The script rebuilds the station catalog from /stations and compares it with the
local inventory. By default, it probes missing station IDs and adds only stations
with current observations. With --add-all-missing, it adds every missing catalog
station and leaves availability to runtime health checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import NWS_STATIONS_PATH
BASE_URL = "https://api.weather.gov"
TIMEOUT_SECONDS = 60
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "MeteoLabX/1.0 (contact: meteolabx@gmail.com)")

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


def _assumed_catalog_sensors() -> Dict[str, bool]:
    return {
        "thermometer": True,
        "hygrometer": True,
        "barometer": True,
        "anemometer": True,
        "wind_vane": True,
        "rain_gauge": True,
        "pyranometer": False,
        "uv": False,
    }


def _has_value(props: Dict[str, Any], fields: Tuple[str, ...]) -> bool:
    for field in fields:
        raw = props.get(field)
        value = raw.get("value") if isinstance(raw, dict) else raw
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


def _request_json(url: str, params: Dict[str, Any] | None = None, retries: int = 5) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(max(1, int(retries))):
        try:
            response = requests.get(
                url,
                params=params or {},
                headers={
                    "Accept": "application/geo+json",
                    "User-Agent": NWS_USER_AGENT,
                },
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, int(retries)):
                break
            time.sleep(1.0 + attempt)
    raise last_exc or RuntimeError("NWS request failed")


def _station_id_from_feature(feature: Dict[str, Any]) -> str:
    props = feature.get("properties") if isinstance(feature, dict) else {}
    if isinstance(props, dict):
        sid = str(props.get("stationIdentifier") or "").strip().upper()
        if sid:
            return sid
    raw_id = str(feature.get("id") or feature.get("@id") or "").rstrip("/")
    return raw_id.rsplit("/", 1)[-1].strip().upper()


def _normalize_station(feature: Dict[str, Any]) -> Dict[str, Any] | None:
    props = feature.get("properties") if isinstance(feature, dict) else {}
    geometry = feature.get("geometry") if isinstance(feature, dict) else {}
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None

    station_id = _station_id_from_feature(feature)
    coords = geometry.get("coordinates")
    if not station_id or not isinstance(coords, list) or len(coords) < 2:
        return None

    elevation = props.get("elevation")
    elev_value = elevation.get("value") if isinstance(elevation, dict) else None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return None

    try:
        elev = float(elev_value) if elev_value is not None else 0.0
    except (TypeError, ValueError):
        elev = 0.0

    return {
        "id": station_id,
        "name": str(props.get("name") or station_id).strip(),
        "lat": lat,
        "lon": lon,
        "elev": elev,
        "tz": str(props.get("timeZone") or "").strip(),
        "provider": "weathergov",
    }


def fetch_station_catalog(limit: int = 500, max_pages: int = 0) -> List[Dict[str, Any]]:
    features: List[Dict[str, Any]] = []
    next_url = f"{BASE_URL}/stations"
    next_params: Dict[str, Any] | None = {"limit": max(1, min(int(limit), 500))}
    pages = 0
    seen_next_urls: set[str] = set()

    while next_url:
        if next_url in seen_next_urls:
            print(f"catalog pagination repeated at page {pages + 1}; stopping", flush=True)
            break
        seen_next_urls.add(next_url)

        before_count = len(features)
        payload = _request_json(next_url, params=next_params)
        page_features = payload.get("features")
        if isinstance(page_features, list):
            features.extend(item for item in page_features if isinstance(item, dict))

        pages += 1
        added_count = len(features) - before_count
        if added_count <= 0:
            print(f"catalog page {pages} added no stations; stopping at {len(features)}", flush=True)
            break

        if max_pages > 0 and pages >= max_pages:
            break

        pagination = payload.get("pagination") if isinstance(payload, dict) else {}
        next_link = str(pagination.get("next") or "").strip() if isinstance(pagination, dict) else ""
        next_url = next_link
        next_params = None

        if pages % 5 == 0:
            print(f"catalog pages={pages}, stations={len(features)}", flush=True)

    return features


def _probe_station(station: Dict[str, Any]) -> Tuple[str, Dict[str, Any] | None, str]:
    station_id = str(station.get("id") or "").strip().upper()
    if not station_id:
        return station_id, None, "empty station id"

    url = f"{BASE_URL}/stations/{quote(station_id)}/observations"
    try:
        payload = _request_json(url, params={"limit": 1})
    except Exception as exc:
        return station_id, None, str(exc)[:240]

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return station_id, None, "Serie vacia"

    recovered = dict(station)
    recovered["sensors"] = _sensors_from_feature(features[0])
    if not any(recovered["sensors"].values()):
        recovered["sensors"] = _empty_sensors()
    recovered["recovered_from_nws_catalog_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return station_id, recovered, ""


def recover_inventory(
    current_stations: List[Dict[str, Any]],
    catalog_features: List[Dict[str, Any]],
    *,
    output_path: Path,
    max_workers: int,
    save_every: int,
    max_missing: int,
    dry_run: bool,
    add_all_missing: bool,
) -> None:
    existing_ids = {
        str(station.get("id") or "").strip().upper()
        for station in current_stations
        if isinstance(station, dict)
    }
    catalog_by_id: Dict[str, Dict[str, Any]] = {}
    for feature in catalog_features:
        station = _normalize_station(feature)
        if not station:
            continue
        catalog_by_id[str(station["id"]).upper()] = station

    missing = [
        station
        for station_id, station in sorted(catalog_by_id.items())
        if station_id not in existing_ids
    ]
    if max_missing > 0:
        missing = missing[:max_missing]

    print(f"catalog stations: {len(catalog_by_id)}")
    print(f"current inventory: {len(existing_ids)}")
    print(f"missing to {'add' if add_all_missing else 'probe'}: {len(missing)}")
    if dry_run or not missing:
        return

    if add_all_missing:
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        additions = []
        for station in missing:
            added = dict(station)
            added["sensors"] = _assumed_catalog_sensors()
            added["added_from_nws_catalog_at"] = now_iso
            added["sensor_source"] = "nws_catalog_assumed_standard_schema"
            additions.append(added)
        merged = sorted(
            [*current_stations, *additions],
            key=lambda item: str(item.get("id") or ""),
        )
        _save_json(output_path, merged)
        print(f"added: {len(additions)}")
        print(f"saved: {len(merged)} stations to {output_path}")
        return

    recovered_by_id: Dict[str, Dict[str, Any]] = {}
    errors = 0
    completed = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        futures = {
            executor.submit(_probe_station, station): str(station.get("id") or "").strip().upper()
            for station in missing
        }
        for future in as_completed(futures):
            station_id, recovered, error = future.result()
            completed += 1
            if recovered:
                recovered_by_id[station_id] = recovered
            else:
                errors += 1

            if completed % max(1, int(save_every)) == 0:
                merged = sorted(
                    [*current_stations, *recovered_by_id.values()],
                    key=lambda item: str(item.get("id") or ""),
                )
                _save_json(output_path, merged)
                elapsed = max(0.001, time.time() - started)
                print(
                    f"{completed}/{len(missing)} probed "
                    f"({completed / elapsed:.1f}/s, recovered={len(recovered_by_id)}, skipped={errors})",
                    flush=True,
                )

    merged = sorted(
        [*current_stations, *recovered_by_id.values()],
        key=lambda item: str(item.get("id") or ""),
    )
    _save_json(output_path, merged)
    print(f"recovered: {len(recovered_by_id)}")
    print(f"skipped/no-data/errors: {errors}")
    print(f"saved: {len(merged)} stations to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover omitted NWS stations that currently return observations.")
    parser.add_argument("--input", default=str(NWS_STATIONS_PATH))
    parser.add_argument("--output", default=str(NWS_STATIONS_PATH))
    parser.add_argument("--catalog-limit", type=int, default=500)
    parser.add_argument("--catalog-max-pages", type=int, default=0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--save-every", type=int, default=500)
    parser.add_argument("--max-missing", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--add-all-missing",
        action="store_true",
        help="Add every missing /stations catalog entry without probing observations.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    current_stations = _load_json(input_path)
    if not isinstance(current_stations, list):
        print(f"Expected station list in {input_path}", file=sys.stderr)
        return 2

    catalog_features = fetch_station_catalog(limit=int(args.catalog_limit), max_pages=int(args.catalog_max_pages))
    recover_inventory(
        current_stations,
        catalog_features,
        output_path=output_path,
        max_workers=int(args.max_workers),
        save_every=int(args.save_every),
        max_missing=int(args.max_missing),
        dry_run=bool(args.dry_run),
        add_all_missing=bool(args.add_all_missing),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

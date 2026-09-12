#!/usr/bin/env python3
"""Refresh the Météo-France DPObs station inventory safely.

The official ``/liste-stations`` response is the online DPObs catalogue.  New
stations are added, existing sensor metadata is retained, and an absent station
is kept as ``unconfirmed_missing`` unless its ID is explicitly listed as
unavailable after checking DPClim.  Output and report writes are atomic.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "data" / "data_estaciones_meteofrance.json"
DEFAULT_REPORT = ROOT / "data" / "meteofrance_inventory_refresh.json"
URL = "https://public-api.meteofrance.fr/public/DPObs/v1/liste-stations"
SENSOR_KEYS = (
    "thermometer", "hygrometer", "barometer", "anemometer",
    "wind_vane", "rain_gauge", "pyranometer", "uv",
)


def atomic_json(path: Path, payload: Any) -> None:
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


def fetch_catalog(api_key: str, retries: int = 5) -> bytes:
    for attempt in range(retries + 1):
        response = requests.get(
            URL,
            headers={"apikey": api_key, "Accept": "text/csv", "User-Agent": "MeteoLabx/1.0"},
            timeout=60,
        )
        if response.status_code != 429 or attempt >= retries:
            response.raise_for_status()
            return response.content
        time.sleep(15 * (attempt + 1))
    raise RuntimeError("Météo-France catalogue download failed")


def parse_catalog(content: bytes) -> list[dict[str, str]]:
    text = content.decode("iso-8859-1")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    required = {"Id_station", "Nom_usuel", "Latitude", "Longitude", "Date_ouverture", "Pack"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Unexpected Météo-France station catalogue format")
    return rows


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def station_from_row(raw: dict[str, str], old: dict[str, Any] | None = None) -> dict[str, Any]:
    station_id = str(raw.get("Id_station") or "").strip()
    previous = dict(old or {})
    previous.update({
        "id": station_id,
        "id_station": station_id,
        "id_omm": str(raw.get("Id_omm") or "").strip(),
        "name": str(raw.get("Nom_usuel") or station_id).strip(),
        "nom_usuel": str(raw.get("Nom_usuel") or station_id).strip(),
        "lat": _number(raw.get("Latitude")),
        "lon": _number(raw.get("Longitude")),
        "elev": _number(raw.get("Altitude")),
        "altitude": _number(raw.get("Altitude")),
        "date_ouverture": str(raw.get("Date_ouverture") or "").strip(),
        "pack": str(raw.get("Pack") or "").strip(),
        "provider": "METEOFRANCE",
        "source": "Meteo-France DPObs /liste-stations",
        "raw": dict(raw),
        "status": "active",
        "active_now": True,
        "historical": False,
        "has_historical": True,
    })
    sensors = previous.get("sensors")
    if not isinstance(sensors, dict):
        sensors = {key: False for key in SENSOR_KEYS}
    else:
        sensors = {key: bool(sensors.get(key)) for key in SENSOR_KEYS}
    previous["sensors"] = sensors
    if old is None:
        previous["sensor_probe_pending"] = True
    previous.pop("missing_checks", None)
    return previous


def merge_catalog(
    official: list[dict[str, str]],
    existing: list[dict[str, Any]],
    unavailable_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    old = {str(row.get("id_station") or row.get("id") or "").strip(): row for row in existing}
    current = {str(row.get("Id_station") or "").strip(): row for row in official}
    current.pop("", None)
    rows = [station_from_row(raw, old.get(station_id)) for station_id, raw in current.items()]
    missing = sorted(set(old) - set(current))
    removed_unavailable = sorted(set(missing) & unavailable_ids)
    unconfirmed = sorted(set(missing) - unavailable_ids)
    for station_id in unconfirmed:
        row = dict(old[station_id])
        row["status"] = "unconfirmed_missing"
        row["active_now"] = False
        row["missing_checks"] = int(row.get("missing_checks") or 0) + 1
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("id_station") or row.get("id") or ""))
    return rows, {
        "official_count": len(current),
        "previous_count": len(old),
        "result_count": len(rows),
        "added": sorted(set(current) - set(old)),
        "removed_without_dpclim": removed_unavailable,
        "unconfirmed_missing": unconfirmed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument("--api-key", default=os.getenv("METEOLABX_METEOFRANCE_API_KEY") or os.getenv("METEOFRANCE_API_KEY") or "")
    parser.add_argument("--confirmed-unavailable", default="")
    args = parser.parse_args()

    if args.catalog_file:
        content = args.catalog_file.read_bytes()
    else:
        if not str(args.api_key).strip():
            raise SystemExit("Missing METEOLABX_METEOFRANCE_API_KEY")
        content = fetch_catalog(str(args.api_key).strip())
    existing = json.loads(args.input.read_text(encoding="utf-8"))
    unavailable = {item.strip() for item in args.confirmed_unavailable.split(",") if item.strip()}
    rows, report = merge_catalog(parse_catalog(content), existing, unavailable)
    atomic_json(args.output, rows)
    atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

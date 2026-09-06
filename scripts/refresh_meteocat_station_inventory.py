#!/usr/bin/env python3
"""Refresh the complete Meteocat station catalogue, including closed sites."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "data_estaciones_meteocat.json"
CATALOG_URL = "https://api.meteo.cat/xema/v1/estacions/metadades"


def _load_local_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _is_online(station: dict[str, Any]) -> bool:
    """Meteocat status 2 is operational; status 1 is dismantled."""
    statuses = station.get("estats")
    if not isinstance(statuses, list):
        return False
    return any(
        isinstance(status, dict)
        and status.get("codi") == 2
        and status.get("dataFi") in (None, "")
        for status in statuses
    )


def refresh_catalog(api_key: str, output: Path) -> tuple[list[str], list[str], int, int]:
    previous: list[dict[str, Any]] = []
    if output.exists():
        payload = json.loads(output.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            previous = [row for row in payload if isinstance(row, dict)]
    previous_by_code = {str(row.get("codi") or "").strip(): row for row in previous}

    response = requests.get(
        CATALOG_URL,
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Meteocat catalogue response is not a station list")

    stations: list[dict[str, Any]] = []
    for raw_station in payload:
        if not isinstance(raw_station, dict) or not raw_station.get("codi"):
            continue
        station = dict(raw_station)
        code = str(station["codi"]).strip()
        station["online"] = _is_online(station)
        station["has_historical"] = True
        previous_sensors = previous_by_code.get(code, {}).get("sensors")
        if isinstance(previous_sensors, dict):
            station["sensors"] = previous_sensors
        stations.append(station)

    stations.sort(key=lambda row: str(row.get("codi") or "").casefold())
    current_codes = {str(row["codi"]).strip() for row in stations}
    previous_codes = set(previous_by_code)
    added = sorted(current_codes - previous_codes)
    removed = sorted(previous_codes - current_codes)
    online = sum(bool(row["online"]) for row in stations)
    historical_only = len(stations) - online

    output.write_text(
        json.dumps(stations, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return added, removed, online, historical_only


def main() -> int:
    _load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--api-key",
        default=(
            os.getenv("METEOLABX_METEOCAT_API_KEY", "")
            or os.getenv("METEOCAT_API_KEY", "")
        ),
    )
    args = parser.parse_args()
    api_key = str(args.api_key or "").strip()
    if not api_key:
        parser.error("missing METEOLABX_METEOCAT_API_KEY/METEOCAT_API_KEY")

    added, removed, online, historical_only = refresh_catalog(api_key, args.output)
    print(f"Saved {online + historical_only} Meteocat stations to {args.output}")
    print(f"Online: {online}; historical-only: {historical_only}")
    print(f"Added ({len(added)}): {', '.join(added) or '-'}")
    print(f"Removed ({len(removed)}): {', '.join(removed) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

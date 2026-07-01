#!/usr/bin/env python3
"""Build a complete IEM station inventory from its network GeoJSON APIs."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


NETWORKS_URL = "https://mesonet.agron.iastate.edu/geojson/networks.py"
NETWORK_URL = "https://mesonet.agron.iastate.edu/geojson/network.py"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "data_estaciones_iem.json"
USER_AGENT = "MeteoLabX-IEM-Inventory/1.0 (station metadata download)"


def _get_json(url: str, *, params: dict[str, str] | None = None, retries: int = 4) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=(10, 90))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("IEM response is not a JSON object")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (2**attempt))
    raise RuntimeError(f"IEM request failed: {url}?{urlencode(params or {})}: {last_error}")


def fetch_networks() -> list[dict[str, str]]:
    payload = _get_json(NETWORKS_URL)
    networks = []
    for feature in payload.get("features", []):
        if not isinstance(feature, dict):
            continue
        code = str(feature.get("id") or "").strip()
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        if code:
            networks.append({"code": code, "name": str(properties.get("name") or code).strip()})
    return sorted(networks, key=lambda row: row["code"])


def _station_from_feature(feature: dict[str, Any], network: dict[str, str]) -> dict[str, Any] | None:
    properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    station_id = str(properties.get("sid") or feature.get("id") or "").strip()
    if not station_id:
        return None

    coordinates = geometry.get("coordinates") if geometry.get("type") == "Point" else None
    try:
        lon = float(coordinates[0]) if isinstance(coordinates, list) and len(coordinates) >= 2 else None
        lat = float(coordinates[1]) if isinstance(coordinates, list) and len(coordinates) >= 2 else None
    except (TypeError, ValueError):
        lon, lat = None, None

    elevation = properties.get("elevation")
    try:
        elevation = float(elevation) if elevation is not None else None
    except (TypeError, ValueError):
        elevation = None

    attributes = properties.get("attributes")
    return {
        "id": station_id,
        "name": str(properties.get("sname") or station_id).strip(),
        "lat": lat,
        "lon": lon,
        "elev": elevation,
        "tz": str(properties.get("tzname") or "").strip() or None,
        "provider": "iem",
        "network": network["code"],
        "network_name": network["name"],
        "state": str(properties.get("state") or "").strip() or None,
        "country": str(properties.get("country") or "").strip() or None,
        "county": str(properties.get("county") or "").strip() or None,
        "online": bool(properties.get("online")),
        "archive_begin": properties.get("archive_begin"),
        "archive_end": properties.get("archive_end"),
        "time_domain": properties.get("time_domain"),
        "synop": properties.get("synop"),
        "climate_site": properties.get("climate_site"),
        "wfo": properties.get("wfo"),
        "attributes": attributes if isinstance(attributes, dict) else {},
    }


def fetch_network(network: dict[str, str]) -> tuple[str, list[dict[str, Any]], int]:
    payload = _get_json(NETWORK_URL, params={"network": network["code"]})
    stations = []
    for feature in payload.get("features", []):
        if isinstance(feature, dict):
            station = _station_from_feature(feature, network)
            if station is not None:
                stations.append(station)
    return network["code"], stations, int(payload.get("count") or len(stations))


def build_inventory(*, workers: int) -> dict[str, Any]:
    networks = fetch_networks()
    stations: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    reported_counts: dict[str, int] = {}
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_network, network): network for network in networks}
        for future in concurrent.futures.as_completed(futures):
            network = futures[future]
            try:
                code, rows, reported_count = future.result()
                stations.extend(rows)
                reported_counts[code] = reported_count
            except Exception as exc:  # Continue so the output records partial failures.
                failures[network["code"]] = str(exc)
            completed += 1
            if completed % 25 == 0 or completed == len(networks):
                print(
                    f"Networks {completed}/{len(networks)}; stations {len(stations)}; "
                    f"failures {len(failures)}",
                    flush=True,
                )

    stations.sort(key=lambda row: (row["network"], row["id"]))
    online_count = sum(1 for station in stations if station["online"])
    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "networks": NETWORKS_URL,
            "stations": f"{NETWORK_URL}?network=CODIGO_RED",
        },
        "network_count": len(networks),
        "successful_network_count": len(networks) - len(failures),
        "failed_networks": failures,
        "reported_station_count": sum(reported_counts.values()),
        "station_count": len(stations),
        "online_station_count": online_count,
        "offline_station_count": len(stations) - online_count,
        "networks": networks,
        "stations": stations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    inventory = build_inventory(workers=args.workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {inventory['station_count']} stations from "
        f"{inventory['successful_network_count']}/{inventory['network_count']} networks "
        f"to {args.output}",
    )
    if inventory["failed_networks"]:
        print(json.dumps(inventory["failed_networks"], ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

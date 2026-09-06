#!/usr/bin/env python3
"""Merge a fresh IEM download with MeteoLabX's curated IEM inventory.

The raw IEM catalogue contains many ``*_DCP`` hydrology stations.  A DCP is
admitted only when ``currents.json`` exposes at least one weather sensor.
Previously admitted DCP stations are retained as temporarily unavailable when
one refresh misses them, so a transient outage never destroys their history.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT = ROOT / "data" / "data_estaciones_iem.json"
DEFAULT_KILL_LIST = ROOT / "data" / "iem_confirmed_duplicates_removed.json"
DCP_KEEP = {"CA_DCP|DEVC1"}


def station_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("network") or ""), str(row.get("id") or "")


def load_currents(path: Path | None) -> dict[tuple[str, str], Any]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (str(network), str(station)): value
        for network, stations in payload.items()
        if isinstance(stations, dict)
        for station, value in stations.items()
    }


def merge_inventory(
    current: dict[str, Any],
    raw: dict[str, Any],
    *,
    dcp_currents: dict[tuple[str, str], Any],
    sensor_currents: dict[tuple[str, str], Any],
    asos_currents: dict[tuple[str, str], Any],
    asos_networks: set[str],
    killed: set[str],
) -> tuple[dict[str, Any], dict[str, int]]:
    old_rows = {station_key(row): row for row in current.get("stations", [])}
    raw_rows = {station_key(row): row for row in raw.get("stations", [])}
    output: dict[tuple[str, str], dict[str, Any]] = {}
    stats = {
        "raw_dcp_excluded": 0,
        "duplicates_excluded": 0,
        "old_missing_preserved": 0,
        "old_dcp_missing_preserved": 0,
    }

    for key, fresh in raw_rows.items():
        text_key = f"{key[0]}|{key[1]}"
        if text_key in killed:
            stats["duplicates_excluded"] += 1
            continue
        is_dcp = "DCP" in key[0]
        if is_dcp and key not in dcp_currents and text_key not in DCP_KEEP:
            old = old_rows.get(key)
            if not old or not old.get("sensors"):
                stats["raw_dcp_excluded"] += 1
                continue
            row = dict(old)
            row.update(fresh)
            row["online"] = False
            row["status"] = "unconfirmed_missing"
            output[key] = row
            stats["old_dcp_missing_preserved"] += 1
            continue

        row = dict(old_rows.get(key, {}))
        row.update(fresh)
        sensors = dcp_currents.get(key) or sensor_currents.get(key)
        if isinstance(sensors, dict):
            row["sensors"] = sensors
        row.pop("status", None)
        if key[0] in asos_networks:
            value = asos_currents.get(key)
            try:
                seen = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                row["online"] = seen >= datetime.now(timezone.utc) - timedelta(days=30)
            except (TypeError, ValueError):
                row["online"] = False
        output[key] = row

    # A station disappearing from network.py is not proof its archive vanished.
    for key, old in old_rows.items():
        text_key = f"{key[0]}|{key[1]}"
        if key in output or text_key in killed:
            continue
        row = dict(old)
        row["online"] = False
        row["status"] = "unconfirmed_missing"
        output[key] = row
        stats["old_missing_preserved"] += 1

    stations = sorted(output.values(), key=station_key)
    result = dict(raw)
    result.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station_count": len(stations),
        "online_station_count": sum(bool(row.get("online")) for row in stations),
        "offline_station_count": sum(not bool(row.get("online")) for row in stations),
        "stations": stations,
        "curation": {
            "dcp_policy": "weather_sensors_only; previously_valid_missing_stations_preserved",
            "confirmed_duplicates_excluded": stats["duplicates_excluded"],
            "raw_dcp_without_weather_excluded": stats["raw_dcp_excluded"],
        },
    })
    return result, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--output", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--dcp-currents", type=Path, required=True)
    parser.add_argument("--sensor-currents", type=Path)
    parser.add_argument("--asos-currents", type=Path)
    parser.add_argument("--kill-list", type=Path, default=DEFAULT_KILL_LIST)
    args = parser.parse_args()

    current = json.loads(args.current.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    kill_payload = json.loads(args.kill_list.read_text(encoding="utf-8"))
    killed = set((kill_payload.get("stations") or {}).keys())
    asos_payload = (
        json.loads(args.asos_currents.read_text(encoding="utf-8"))
        if args.asos_currents else {}
    )
    merged, stats = merge_inventory(
        current,
        raw,
        dcp_currents=load_currents(args.dcp_currents),
        sensor_currents=load_currents(args.sensor_currents),
        asos_currents=load_currents(args.asos_currents),
        asos_networks=set(asos_payload),
        killed=killed,
    )
    # Atomic replacement: an interruption cannot leave the 70 MB catalogue
    # truncated or destroy the last usable inventory.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{args.output.name}.", suffix=".tmp", dir=args.output.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved {merged['station_count']} curated IEM stations to {args.output}")
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

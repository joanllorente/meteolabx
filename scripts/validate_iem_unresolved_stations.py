#!/usr/bin/env python3
"""Validate active IEM stations that still lack known weather sensors.

Checks ``obhistory.json`` station by station, using recent dates plus an
archived date when available.  Results are checkpointed after every batch and
the script is safe to resume.  Radar and upper-air products are classified as
unsupported without HTTP calls because they are not surface weather stations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import tempfile
import threading
import time
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.enrich_iem_sensor_inventory import _sensors_from_row


INVENTORY = ROOT / "data" / "data_estaciones_iem.json"
CHECKPOINT = ROOT / "data" / "iem_station_validation.json"
URL = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"
DAILY_URL = "https://mesonet.agron.iastate.edu/api/1/daily.json"
UNSUPPORTED_NETWORK_MARKERS = ("NEXRAD", "RAOB")
_local = threading.local()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "MeteoLabX-IEM-Validator/1.0", "Accept": "application/json"})
        _local.session = session
    return session


def _dates(row: dict[str, Any]) -> list[str]:
    today = datetime.now(timezone.utc).date()
    candidates = [today - timedelta(days=1), today - timedelta(days=30), today - timedelta(days=365)]
    archive_end = str(row.get("archive_end") or "")[:10]
    if archive_end:
        try:
            end = date.fromisoformat(archive_end)
            candidates.append(end - timedelta(days=1))
        except ValueError:
            pass
    return list(dict.fromkeys(day.isoformat() for day in candidates))


def _probe(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    network, station = str(row["network"]), str(row["id"])
    key = f"{network}|{station}"
    if any(marker in network.upper() for marker in UNSUPPORTED_NETWORK_MARKERS):
        return key, {"result": "unsupported_product", "checked_at": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    for day in _dates(row):
        for attempt in range(3):
            try:
                daily = "CLIMATE" in network.upper()
                response = _session().get(DAILY_URL if daily else URL,
                    params={"network": network, "station": station, "date": day}, timeout=(10, 45))
                if response.status_code == 404:
                    break
                response.raise_for_status()
                rows = response.json().get("data") or []
                sensors: dict[str, bool] = {}
                for observation in rows:
                    if daily and isinstance(observation, dict):
                        observation = dict(observation)
                        observation["pday"] = observation.get("precip")
                    found = _sensors_from_row(observation) if isinstance(observation, dict) else None
                    if found:
                        sensors = {name: sensors.get(name, False) or value for name, value in found.items()}
                if sensors:
                    return key, {"result": "weather_data", "date": day, "sensors": sensors,
                                 "checked_at": datetime.now(timezone.utc).isoformat()}
                break
            except (requests.RequestException, ValueError) as exc:
                errors.append(type(exc).__name__)
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
    result = "error" if errors else "no_data_sample"
    return key, {"result": result, "errors": errors[-4:], "checked_at": datetime.now(timezone.utc).isoformat()}


def _is_manual(network: str) -> bool:
    return network.endswith("_COOP") or "COCORAHS" in network


def apply_results(inventory: dict[str, Any], results: dict[str, Any]) -> dict[str, int]:
    """Apply only conclusive results; inconclusive/error rows are preserved."""
    kept = []
    stats = {"sensors_added": 0, "unsupported_removed": 0, "no_data_deactivated": 0}
    for row in inventory["stations"]:
        key = f"{row.get('network')}|{row.get('id')}"
        result = results.get(key, {})
        status = result.get("result")
        if status == "weather_data":
            row = dict(row)
            row["sensors"] = result["sensors"]
            row["validation_status"] = "weather_data"
            stats["sensors_added"] += 1
        elif status == "unsupported_product":
            stats["unsupported_removed"] += 1
            continue
        elif status == "no_data_sample":
            row = dict(row)
            row["online"] = False
            row["validation_status"] = "unconfirmed_no_data"
            stats["no_data_deactivated"] += 1
        kept.append(row)
    inventory["stations"] = kept
    inventory["station_count"] = len(kept)
    inventory["online_station_count"] = sum(bool(row.get("online")) for row in kept)
    inventory["offline_station_count"] = len(kept) - inventory["online_station_count"]
    inventory["validation"] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "policy": "unresolved active non-manual stations checked on four dates",
        **stats,
    }
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--max-stations", type=int, default=0)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--apply", action="store_true", help="apply conclusive results to inventory")
    args = parser.parse_args()
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    targets = [row for row in inventory["stations"] if row.get("online") and not row.get("sensors")
               and not _is_manual(str(row.get("network") or ""))]
    results: dict[str, Any] = {}
    if args.checkpoint.exists():
        results = (json.loads(args.checkpoint.read_text(encoding="utf-8")).get("stations") or {})
    pending = [
        row for row in targets
        if f"{row['network']}|{row['id']}" not in results
        or (args.retry_errors and results[f"{row['network']}|{row['id']}"].get("result") == "error")
    ]
    already_checked = len(targets) - len(pending)
    if args.max_stations:
        pending = pending[:args.max_stations]
    print(f"targets={len(targets)} already_checked={already_checked} pending={len(pending)}", flush=True)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(_probe, row) for row in pending]
        for future in concurrent.futures.as_completed(futures):
            key, result = future.result()
            results[key] = result
            done += 1
            if done % 100 == 0 or done == len(pending):
                _atomic_json(args.checkpoint, {"generated_at": datetime.now(timezone.utc).isoformat(), "stations": results})
                counts: dict[str, int] = {}
                for value in results.values():
                    name = value.get("result", "unknown")
                    counts[name] = counts.get(name, 0) + 1
                print(f"{done}/{len(pending)} {counts}", flush=True)
    if args.apply:
        stats = apply_results(inventory, results)
        _atomic_json(INVENTORY, inventory)
        print(f"applied={stats} station_count={inventory['station_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

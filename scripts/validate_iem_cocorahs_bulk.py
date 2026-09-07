#!/usr/bin/env python3
"""Bulk-validate online CoCoRaHS stations on twelve distributed dates."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "data_estaciones_iem.json"
REPORT = ROOT / "data" / "iem_cocorahs_validation.json"
URL = "https://mesonet.agron.iastate.edu/api/1/daily.json"


def atomic_json(path: Path, payload: Any) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sample_dates(today: date, seed: int) -> list[str]:
    rng = random.Random(seed)
    offsets = {1, 7, 30}
    while len(offsets) < 12:
        offsets.add(rng.randint(1, 365))
    return [(today - timedelta(days=offset)).isoformat() for offset in sorted(offsets)]


def fetch(network: str, day: str) -> tuple[str, str, set[str], str]:
    for attempt in range(3):
        try:
            response = requests.get(URL, params={"network": network, "date": day},
                headers={"User-Agent": "MeteoLabX-CoCoRaHS-Bulk-Audit/1.0"}, timeout=(10, 60))
            if response.status_code == 404:
                return network, day, set(), ""
            response.raise_for_status()
            rows = response.json().get("data") or []
            stations = {
                str(row.get("station") or row.get("id") or "")
                for row in rows if isinstance(row, dict) and any(
                    row.get(field) is not None
                    for field in ("precip", "max_tmpf", "min_tmpf", "snow", "snowd")
                )
            }
            return network, day, stations - {""}, ""
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                return network, day, set(), type(exc).__name__
            time.sleep(1.0 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    targets = [row for row in payload["stations"] if row.get("online") and
               "COCORAHS" in str(row.get("network") or "").upper()]
    networks = sorted({str(row["network"]) for row in targets})
    dates = sample_dates(date.today(), args.seed)
    observed: dict[str, set[str]] = {network: set() for network in networks}
    failures = []
    jobs = [(network, day) for network in networks for day in dates]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, *job) for job in jobs]
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            network, day, stations, error = future.result()
            if error:
                failures.append(f"{network}|{day}|{error}")
            else:
                observed[network].update(stations)
            if done % 100 == 0 or done == len(jobs):
                print(f"{done}/{len(jobs)} requests failures={len(failures)}", flush=True)
    if failures:
        raise RuntimeError(f"validation incomplete: {len(failures)} failed requests")
    missing = [row for row in targets if str(row["id"]) not in observed[str(row["network"])]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "dates": dates, "target_count": len(targets),
        "confirmed_count": len(targets) - len(missing), "removed_count": len(missing),
        "stations": [{"network": row["network"], "id": row["id"], "name": row.get("name")}
                     for row in missing],
    }
    atomic_json(REPORT, report)
    print(f"confirmed={report['confirmed_count']} missing={report['removed_count']}")
    if args.apply:
        doomed = {(str(row["network"]), str(row["id"])) for row in missing}
        payload["stations"] = [row for row in payload["stations"]
                               if (str(row["network"]), str(row["id"])) not in doomed]
        payload["station_count"] = len(payload["stations"])
        payload["online_station_count"] = sum(bool(row.get("online")) for row in payload["stations"])
        payload["offline_station_count"] = len(payload["stations"]) - payload["online_station_count"]
        atomic_json(INVENTORY, payload)
        print(f"saved inventory stations={payload['station_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

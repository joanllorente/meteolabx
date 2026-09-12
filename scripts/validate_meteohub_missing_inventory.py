#!/usr/bin/env python3
"""Validate MeteoHub stations marked unconfirmed_missing without rebuilding inventory.

MeteoHub has confirmed that it does not provide historical series. The public
observations endpoint accepts at most a three-day window, so this script checks
whether a station has reappeared recently. It writes an atomic, resumable report
and never modifies the station inventory.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from build_meteohub_inventory import (
    DEFAULT_BASE_URL,
    PRODUCT_SPECS,
    _iter_station_records,
    _observations_url,
    _request_json,
    _station_key,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "data" / "data_estaciones_meteohub_it.json"
DEFAULT_REPORT = ROOT / "data" / "meteohub_missing_validation.json"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _nearby_key(record: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(record.get("network") or "").strip(),
        round(float(record.get("lat")) * 10000),
        round(float(record.get("lon")) * 10000),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    missing = [row for row in inventory if row.get("status") == "unconfirmed_missing"]
    networks = sorted({str(row.get("network") or "") for row in missing})
    end = args.end_date
    start = end - timedelta(days=2)
    jobs = [(network, product) for network in networks for product in PRODUCT_SPECS]

    report: dict[str, Any] = {
        "version": 1,
        "inventory": str(args.inventory.resolve()),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "historical_series": False,
        "total_missing_checked": len(missing),
        "completed_jobs": [],
        "failed_jobs": [],
        "matches": {},
    }
    if args.report.exists():
        old = json.loads(args.report.read_text(encoding="utf-8"))
        if old.get("window_start") == report["window_start"] and old.get("window_end") == report["window_end"]:
            report = old

    completed = set(report.get("completed_jobs") or [])
    found: dict[str, set[str]] = {
        key: set(value) for key, value in (report.get("matches") or {}).items()
    }
    exact = {str(row.get("id")): row for row in missing}
    nearby = {_nearby_key(row): str(row.get("id")) for row in missing}

    def fetch(network: str, product: Any) -> tuple[str, str, Any]:
        job_id = f"{network}:{product.code}"
        url = _observations_url(
            DEFAULT_BASE_URL,
            network_id=network,
            product_code=product.code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            license_group="CCBY_COMPLIANT",
        )
        payload = _request_json(url, timeout=args.timeout, retries=args.retries, retry_sleep=1)
        return job_id, product.code, payload

    pending = [(n, p) for n, p in jobs if f"{n}:{p.code}" not in completed]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch, n, p): (n, p) for n, p in pending}
        for index, future in enumerate(as_completed(futures), 1):
            network, product = futures[future]
            job_id = f"{network}:{product.code}"
            try:
                _, product_code, payload = future.result()
                for record in _iter_station_records(payload):
                    key = _station_key(record["network"], record["lat"], record["lon"], record["name"])
                    station_id = key if key in exact else nearby.get(_nearby_key(record))
                    if station_id:
                        found.setdefault(station_id, set()).add(product_code)
                completed.add(job_id)
            except Exception as exc:
                report.setdefault("failed_jobs", []).append({"job": job_id, "error": str(exc)})
            report["completed_jobs"] = sorted(completed)
            report["matches"] = {key: sorted(value) for key, value in sorted(found.items())}
            _atomic_json(args.report, report)
            if index % 12 == 0 or index == len(pending):
                print(f"{index}/{len(pending)} requests; reappeared={len(found)}; failures={len(report['failed_jobs'])}")

    matched = set(found)
    report["reappeared"] = sorted(matched)
    report["still_unconfirmed"] = sorted(set(exact) - matched)
    report["summary"] = {
        "reappeared": len(matched),
        "still_unconfirmed": len(exact) - len(matched),
        "failed_jobs": len(report.get("failed_jobs") or []),
    }
    _atomic_json(args.report, report)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Saved report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

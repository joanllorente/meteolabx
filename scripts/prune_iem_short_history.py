#!/usr/bin/env python3
"""Remove short IEM historical series and short online-manual COOP series."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "data_estaciones_iem.json"
REPORT = ROOT / "data" / "iem_short_history_removed.json"


def declared_days(row: dict[str, Any], today: date) -> int:
    try:
        start = date.fromisoformat(str(row.get("archive_begin") or "")[:10])
        end = date.fromisoformat(str(row.get("archive_end") or today)[:10])
    except ValueError:
        return 0
    return max(0, (end - start).days + 1)


def prune(payload: dict[str, Any], *, minimum_days: int, today: date) -> list[dict[str, Any]]:
    kept, removed = [], []
    for row in payload["stations"]:
        days = declared_days(row, today)
        network = str(row.get("network") or "").upper()
        is_coop = network == "COOP" or network.endswith("_COOP")
        if (not row.get("online") or is_coop) and days < minimum_days:
            removed.append({
                "network": row.get("network"), "id": row.get("id"),
                "name": row.get("name"), "archive_begin": row.get("archive_begin"),
                "archive_end": row.get("archive_end"), "declared_days": days,
            })
        else:
            kept.append(row)
    payload["stations"] = kept
    payload["station_count"] = len(kept)
    payload["online_station_count"] = sum(bool(row.get("online")) for row in kept)
    payload["offline_station_count"] = len(kept) - payload["online_station_count"]
    payload["history_policy"] = {"minimum_declared_days": minimum_days}
    return removed


def classify_climate_lifecycle(payload: dict[str, Any], active_keys: set[str]) -> int:
    changed = 0
    for row in payload["stations"]:
        if "CLIMATE" not in str(row.get("network") or "").upper():
            continue
        active = f"{row.get('network')}|{row.get('id')}" in active_keys
        status = "online_manual" if active else "historical"
        if bool(row.get("online")) != active or row.get("status") != status:
            changed += 1
        row["online"] = active
        row["status"] = status
    return changed


def classify_manual_metadata(payload: dict[str, Any]) -> int:
    changed = 0
    for row in payload["stations"]:
        network = str(row.get("network") or "").upper()
        manual = (
            "CLIMATE" in network
            or "COCORAHS" in network
            or network == "COOP"
            or network.endswith("_COOP")
        )
        if manual and not row.get("manual"):
            row["manual"] = True
            changed += 1
    return changed


def classify_cocorahs_as_historical(payload: dict[str, Any], *, minimum_days: int, today: date) -> int:
    changed = 0
    for row in payload["stations"]:
        network = str(row.get("network") or "").upper()
        if "COCORAHS" not in network or row.get("online"):
            continue
        if declared_days(row, today) < minimum_days:
            continue
        if row.get("status") != "historical":
            changed += 1
        row["status"] = "historical"
    return changed


def classify_historical_capability(payload: dict[str, Any], *, minimum_days: int, today: date) -> int:
    """Set the flag consumed by both historical filters in the map."""
    changed = 0
    for row in payload["stations"]:
        has_historical = declared_days(row, today) >= minimum_days
        if bool(row.get("has_historical")) != has_historical:
            changed += 1
        row["has_historical"] = has_historical
    return changed


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-days", type=int, default=365)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild-report-from", type=Path,
                        help="raw inventory used only to reconstruct the cumulative removal report")
    args = parser.parse_args()
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    validation_path = ROOT / "data" / "iem_station_validation.json"
    validation = (
        json.loads(validation_path.read_text(encoding="utf-8")).get("stations") or {}
        if validation_path.exists() else {}
    )
    active_climate_keys = {
        key for key, value in validation.items()
        if "CLIMATE" in key.split("|", 1)[0].upper()
        and value.get("result") == "weather_data"
    }
    climate_changed = classify_climate_lifecycle(payload, active_climate_keys)
    manual_changed = classify_manual_metadata(payload)
    cocorahs_changed = classify_cocorahs_as_historical(
        payload, minimum_days=args.minimum_days, today=date.today()
    )
    removed = prune(payload, minimum_days=args.minimum_days, today=date.today())
    capability_changed = classify_historical_capability(
        payload, minimum_days=args.minimum_days, today=date.today()
    )
    print(f"climate_lifecycle={climate_changed} manual_metadata={manual_changed} "
          f"cocorahs_historical={cocorahs_changed} "
          f"historical_capability={capability_changed} remove={len(removed)} "
          f"keep={payload['station_count']}")
    if args.dry_run:
        return 0
    atomic_json(INVENTORY, payload)
    previous = []
    if REPORT.exists():
        try:
            previous = json.loads(REPORT.read_text(encoding="utf-8")).get("stations") or []
        except (ValueError, OSError):
            pass
    if args.rebuild_report_from:
        audit_payload = json.loads(args.rebuild_report_from.read_text(encoding="utf-8"))
        previous.extend(prune(audit_payload, minimum_days=args.minimum_days, today=date.today()))
    accumulated = {
        f"{row.get('network')}|{row.get('id')}": row for row in [*previous, *removed]
    }
    atomic_json(REPORT, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "minimum_declared_days": args.minimum_days,
        "removed_count": len(accumulated), "stations": list(accumulated.values()),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply observation-confirmed IEM online overrides atomically."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "data_estaciones_iem.json"
OVERRIDES = ROOT / "data" / "iem_dcp_liveness_overrides.json"


def apply(payload: dict, keys: set[str]) -> int:
    changed = 0
    for row in payload["stations"]:
        key = f"{row.get('network')}|{row.get('id')}"
        if key not in keys:
            continue
        if not row.get("online"):
            changed += 1
        row["online"] = True
        row.pop("status", None)
    payload["online_station_count"] = sum(bool(row.get("online")) for row in payload["stations"])
    payload["offline_station_count"] = len(payload["stations"]) - payload["online_station_count"]
    return changed


def main() -> int:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    keys = set(json.loads(OVERRIDES.read_text(encoding="utf-8"))["stations"])
    changed = apply(payload, keys)
    fd, temporary = tempfile.mkstemp(prefix=f".{INVENTORY.name}.", suffix=".tmp", dir=INVENTORY.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, INVENTORY)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"online_overrides={len(keys)} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Export station alias review reports from the unified station catalog."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_station_aliases import DEFAULT_DATABASE

DEFAULT_OUTPUT = ROOT / "data" / "station_alias_nws_report.json"


def _station(row: sqlite3.Row, prefix: str) -> dict[str, Any]:
    return {
        "provider": row[f"{prefix}_provider"],
        "network": row[f"{prefix}_network"] or "",
        "id": row[f"{prefix}_id"],
        "name": row[f"{prefix}_name"],
        "latitude": row[f"{prefix}_latitude"],
        "longitude": row[f"{prefix}_longitude"],
        "elevation_m": row[f"{prefix}_elevation_m"],
        "country": row[f"{prefix}_country"],
        "region": row[f"{prefix}_region"],
    }


def _load_details(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"invalid_json": raw}
    return value if isinstance(value, dict) else {}


def _alias_record(row: sqlite3.Row) -> dict[str, Any]:
    details = _load_details(row["latest_details_json"])
    return {
        "alias_pk": row["alias_pk"],
        "method": row["method"],
        "confidence": row["confidence"],
        "reviewed": bool(row["reviewed"]),
        "iem": _station(row, "iem"),
        "source": _station(row, "source"),
        "latest_check": {
            "status": row["latest_status"],
            "checked_at": row["latest_checked_at"],
            "matched_hours": row["latest_matched_hours"],
            "compared_values": row["latest_compared_values"],
            "agreeing_values": row["latest_agreeing_values"],
            "agreement_ratio": details.get("agreement_ratio"),
            "temperature_agreement_ratio": details.get("temperature_agreement_ratio"),
            "error": details.get("error"),
        } if row["latest_status"] else None,
    }


def _records(connection: sqlite3.Connection, provider: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            a.alias_pk, a.method, a.confidence, a.reviewed,
            iem.provider AS iem_provider,
            iem.network_code AS iem_network,
            iem.station_id AS iem_id,
            iem.name AS iem_name,
            iem.latitude AS iem_latitude,
            iem.longitude AS iem_longitude,
            iem.elevation_m AS iem_elevation_m,
            iem.country AS iem_country,
            iem.region AS iem_region,
            source.provider AS source_provider,
            source.network_code AS source_network,
            source.station_id AS source_id,
            source.name AS source_name,
            source.latitude AS source_latitude,
            source.longitude AS source_longitude,
            source.elevation_m AS source_elevation_m,
            source.country AS source_country,
            source.region AS source_region,
            latest.status AS latest_status,
            latest.checked_at AS latest_checked_at,
            latest.matched_hours AS latest_matched_hours,
            latest.compared_values AS latest_compared_values,
            latest.agreeing_values AS latest_agreeing_values,
            latest.details_json AS latest_details_json
        FROM station_aliases a
        JOIN stations iem ON iem.station_pk = a.station_pk
        JOIN stations source ON source.station_pk = a.canonical_station_pk
        LEFT JOIN station_alias_observation_checks latest
          ON latest.check_pk = (
              SELECT MAX(c.check_pk) FROM station_alias_observation_checks c
              WHERE c.alias_pk = a.alias_pk
          )
        WHERE source.provider = ?
        ORDER BY
            CASE
              WHEN latest.status = 'confirmed' THEN 0
              WHEN latest.status = 'conflict' THEN 1
              WHEN latest.status = 'error' THEN 2
              WHEN latest.status = 'inconclusive' THEN 3
              WHEN a.method = 'inventory_secure' THEN 4
              ELSE 5
            END,
            a.confidence DESC,
            a.alias_pk
        """,
        (provider.upper(),),
    ).fetchall()
    return [_alias_record(row) for row in rows]


def _bucket(record: dict[str, Any]) -> str:
    check = record["latest_check"] or {}
    status = check.get("status")
    if status == "confirmed":
        return "observation_confirmed"
    if status == "conflict":
        return "observation_conflict"
    if status == "error":
        return "observation_error"
    if status == "inconclusive":
        return "observation_inconclusive"
    if record["method"] == "inventory_secure":
        return "inventory_secure_unsampled"
    return "unchecked"


def build_report(database: Path, provider: str) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    records = _records(connection, provider)
    connection.close()

    buckets: dict[str, list[dict[str, Any]]] = {
        "observation_confirmed": [],
        "observation_conflict": [],
        "observation_error": [],
        "observation_inconclusive": [],
        "inventory_secure_unsampled": [],
        "unchecked": [],
    }
    for record in records:
        buckets[_bucket(record)].append(record)

    summary = {
        "provider": provider.upper(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_aliases": len(records),
        "counts": {key: len(value) for key, value in buckets.items()},
    }
    summary["actionable"] = {
        "safe_to_group": summary["counts"]["observation_confirmed"],
        "block_grouping": summary["counts"]["observation_conflict"],
        "needs_more_evidence": (
            summary["counts"]["observation_inconclusive"]
            + summary["counts"]["observation_error"]
            + summary["counts"]["unchecked"]
        ),
        "inventory_secure_candidates": summary["counts"]["inventory_secure_unsampled"],
    }
    return {"summary": summary, "buckets": buckets}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--provider", default="NWS")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = build_report(args.database, args.provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    counts = report["summary"]["counts"]
    print(f"Alias report written: {args.output}")
    print(" ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()

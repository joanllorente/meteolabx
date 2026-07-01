#!/usr/bin/env python3
"""Apply logical station visibility decisions from reviewed alias evidence."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_station_aliases import DEFAULT_DATABASE


SCHEMA = """
CREATE TABLE IF NOT EXISTS station_visibility_overrides (
    station_pk INTEGER PRIMARY KEY,
    hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    reason TEXT NOT NULL DEFAULT '',
    preferred_station_pk INTEGER,
    source_alias_pk INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (station_pk) REFERENCES stations(station_pk) ON DELETE CASCADE,
    FOREIGN KEY (preferred_station_pk) REFERENCES stations(station_pk),
    FOREIGN KEY (source_alias_pk) REFERENCES station_aliases(alias_pk)
);

CREATE INDEX IF NOT EXISTS idx_station_visibility_hidden
ON station_visibility_overrides(hidden);
"""


def apply_confirmed_iem_duplicates(
    database: Path,
    *,
    provider: str,
    reason: str = "confirmed_duplicate_prefer_source_provider",
) -> int:
    """Hide IEM aliases confirmed as duplicates of the source provider."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO station_visibility_overrides(
                station_pk, hidden, reason, preferred_station_pk,
                source_alias_pk, updated_at
            )
            SELECT
                iem.station_pk,
                1,
                ?,
                source.station_pk,
                a.alias_pk,
                ?
            FROM station_aliases a
            JOIN stations iem ON iem.station_pk = a.station_pk
            JOIN stations source ON source.station_pk = a.canonical_station_pk
            WHERE iem.provider = 'IEM'
              AND source.provider = ?
              AND a.method = 'observation_confirmed'
              AND a.reviewed = 0
            ON CONFLICT(station_pk) DO UPDATE SET
                hidden = excluded.hidden,
                reason = excluded.reason,
                preferred_station_pk = excluded.preferred_station_pk,
                source_alias_pk = excluded.source_alias_pk,
                updated_at = excluded.updated_at
            WHERE station_visibility_overrides.hidden = 0
               OR station_visibility_overrides.reason = excluded.reason
               OR station_visibility_overrides.source_alias_pk = excluded.source_alias_pk
            """,
            (reason, now, provider.upper()),
        )
        connection.commit()
        return int(cursor.rowcount if cursor.rowcount is not None else 0)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--provider", required=True)
    parser.add_argument(
        "--reason",
        default="confirmed_duplicate_prefer_source_provider",
    )
    args = parser.parse_args()

    changed = apply_confirmed_iem_duplicates(
        args.database, provider=args.provider, reason=args.reason,
    )
    print(f"Visibility overrides applied: provider={args.provider.upper()} changed={changed}")


if __name__ == "__main__":
    main()

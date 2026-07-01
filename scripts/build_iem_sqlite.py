#!/usr/bin/env python3
"""Import the raw IEM JSON inventory into an indexed SQLite catalog."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "data_estaciones_iem.json"
DEFAULT_OUTPUT = ROOT / "data" / "iem_stations.sqlite"
SCHEMA_VERSION = "1"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE iem_networks (
    network_code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE iem_stations (
    station_pk INTEGER PRIMARY KEY,
    network_code TEXT NOT NULL,
    station_id TEXT NOT NULL,
    name TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    elevation_m REAL,
    timezone TEXT,
    provider TEXT NOT NULL DEFAULT 'iem',
    state TEXT,
    country TEXT,
    county TEXT,
    online INTEGER NOT NULL CHECK (online IN (0, 1)),
    archive_begin TEXT,
    archive_end TEXT,
    time_domain TEXT,
    synop TEXT,
    climate_site TEXT,
    wfo TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (network_code) REFERENCES iem_networks(network_code),
    UNIQUE (network_code, station_id)
);

CREATE INDEX idx_iem_stations_online ON iem_stations(online);
CREATE INDEX idx_iem_stations_country ON iem_stations(country);
CREATE INDEX idx_iem_stations_state ON iem_stations(state);
CREATE INDEX idx_iem_stations_station_id ON iem_stations(station_id);
CREATE INDEX idx_iem_stations_lat_lon ON iem_stations(latitude, longitude);

CREATE VIRTUAL TABLE iem_station_rtree USING rtree(
    station_pk,
    min_latitude, max_latitude,
    min_longitude, max_longitude
);

CREATE VIEW iem_online_stations AS
SELECT * FROM iem_stations WHERE online = 1;
"""


INSERT_STATION = """
INSERT INTO iem_stations (
    network_code, station_id, name, latitude, longitude, elevation_m,
    timezone, provider, state, country, county, online, archive_begin,
    archive_end, time_domain, synop, climate_site, wfo, attributes_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _station_values(row: dict[str, Any]) -> tuple[Any, ...]:
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    return (
        str(row.get("network") or "").strip(),
        str(row.get("id") or "").strip(),
        str(row.get("name") or row.get("id") or "").strip(),
        _number(row.get("lat")),
        _number(row.get("lon")),
        _number(row.get("elev")),
        _text(row.get("tz")),
        _text(row.get("provider")) or "iem",
        _text(row.get("state")),
        _text(row.get("country")),
        _text(row.get("county")),
        int(bool(row.get("online"))),
        _text(row.get("archive_begin")),
        _text(row.get("archive_end")),
        _text(row.get("time_domain")),
        _text(row.get("synop")),
        _text(row.get("climate_site")),
        _text(row.get("wfo")),
        json.dumps(attributes, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _batches(rows: list[dict[str, Any]], size: int = 5_000) -> Iterable[list[tuple[Any, ...]]]:
    for start in range(0, len(rows), size):
        yield [_station_values(row) for row in rows[start:start + size]]


def build_database(input_path: Path, output_path: Path) -> dict[str, int]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    networks = payload.get("networks")
    stations = payload.get("stations")
    if not isinstance(networks, list) or not isinstance(stations, list):
        raise ValueError("IEM inventory must contain network and station lists")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)

    try:
        connection = sqlite3.connect(temporary_path)
        try:
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT INTO iem_networks(network_code, name) VALUES (?, ?)",
                [
                    (str(row.get("code") or "").strip(), str(row.get("name") or row.get("code") or "").strip())
                    for row in networks if isinstance(row, dict)
                ],
            )
            for batch in _batches([row for row in stations if isinstance(row, dict)]):
                connection.executemany(INSERT_STATION, batch)

            connection.execute(
                """
                INSERT INTO iem_station_rtree(
                    station_pk, min_latitude, max_latitude, min_longitude, max_longitude
                )
                SELECT station_pk, latitude, latitude, longitude, longitude
                FROM iem_stations
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            )
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "inventory_generated_at": str(payload.get("generated_at") or ""),
                "inventory_source_networks": str((payload.get("source") or {}).get("networks") or ""),
                "inventory_source_stations": str((payload.get("source") or {}).get("stations") or ""),
                "inventory_reported_station_count": str(payload.get("reported_station_count") or ""),
            }
            connection.executemany(
                "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)", metadata.items(),
            )
            connection.commit()
            connection.execute("ANALYZE")
            connection.execute("PRAGMA optimize")
            connection.commit()

            counts = {
                "networks": connection.execute("SELECT COUNT(*) FROM iem_networks").fetchone()[0],
                "stations": connection.execute("SELECT COUNT(*) FROM iem_stations").fetchone()[0],
                "online": connection.execute("SELECT COUNT(*) FROM iem_online_stations").fetchone()[0],
                "spatial": connection.execute("SELECT COUNT(*) FROM iem_station_rtree").fetchone()[0],
                "integrity_errors": len(connection.execute("PRAGMA integrity_check").fetchall()) - 1,
            }
        finally:
            connection.close()
        os.replace(temporary_path, output_path)
        return counts
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    counts = build_database(args.input, args.output)
    print(
        f"Saved {counts['stations']} stations in {counts['networks']} networks "
        f"({counts['online']} online, {counts['spatial']} spatially indexed) to {args.output}"
    )


if __name__ == "__main__":
    main()

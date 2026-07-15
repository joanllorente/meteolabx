#!/usr/bin/env python3
"""Download Windy Open Data PWS stations into a separate SQLite catalog."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "data" / "pws_stations.sqlite"
API_URL = "https://stations.windy.com/api/v2/opendata/station"
SCHEMA_VERSION = "1"


SCHEMA = """
CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE pws_stations (
    station_pk INTEGER PRIMARY KEY,
    station_id TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'WINDY',
    name TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    country TEXT,
    elevation_m REAL,
    temp_height_m REAL,
    wind_height_m REAL,
    station_type TEXT,
    operator_name TEXT,
    operator_url TEXT,
    share_option TEXT,
    online INTEGER NOT NULL CHECK (online IN (0, 1)),
    last_observation_time TEXT,
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json))
);

CREATE INDEX idx_pws_stations_online ON pws_stations(online);
CREATE INDEX idx_pws_stations_name ON pws_stations(name);
CREATE INDEX idx_pws_stations_type ON pws_stations(station_type);
CREATE INDEX idx_pws_stations_country ON pws_stations(country);
CREATE INDEX idx_pws_stations_lat_lon ON pws_stations(latitude, longitude);

CREATE VIRTUAL TABLE pws_station_rtree USING rtree(
    station_pk,
    min_latitude, max_latitude,
    min_longitude, max_longitude
);

CREATE VIEW pws_online_stations AS
SELECT * FROM pws_stations WHERE online = 1;
"""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _station_values(row: dict[str, Any]) -> tuple[Any, ...]:
    station_id = str(row.get("id") or "").strip()
    if not station_id:
        raise ValueError("Windy station without an id")
    latitude = _number(row.get("lat"))
    longitude = _number(row.get("lon"))
    country = None
    if latitude is not None and longitude is not None:
        from server.services.stations import country_for_point

        country = country_for_point(latitude, longitude)
    return (
        station_id,
        str(row.get("name") or station_id).strip(),
        latitude,
        longitude,
        country,
        _number(row.get("elev_m")),
        _number(row.get("agl_temp")),
        _number(row.get("agl_wind")),
        _text(row.get("station_type")),
        _text(row.get("operator_text")),
        _text(row.get("operator_url")),
        _text(row.get("share_option")),
        int(bool(row.get("is_online"))),
        _text(row.get("last_observation_time")),
        _compact_json(row),
    )


def fetch_inventory(
    api_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not api_key.strip():
        raise ValueError("A Windy API key is required")

    stations: list[dict[str, Any]] = []
    page = 0
    last_page: int | None = None
    reported_total: int | None = None

    while last_page is None or page <= last_page:
        request = urllib.request.Request(
            f"{API_URL}?page={page}",
            headers={"windy-api-key": api_key, "Accept": "application/json"},
        )
        try:
            with opener(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Windy returned HTTP {exc.code} for catalog page {page}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not download Windy catalog page {page}: {exc.reason}") from exc

        rows = payload.get("data") if isinstance(payload, dict) else None
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not isinstance(pagination, dict):
            raise ValueError(f"Unexpected Windy response on catalog page {page}")

        response_page = int(pagination.get("page", -1))
        if response_page != page:
            raise ValueError(f"Windy returned page {response_page} while page {page} was requested")
        last_page = int(pagination.get("totalPages", response_page))
        reported_total = int(pagination.get("totalItems", len(rows)))
        stations.extend(row for row in rows if isinstance(row, dict))
        page += 1

    station_ids = [str(row.get("id") or "").strip() for row in stations]
    if len(station_ids) != len(set(station_ids)):
        raise ValueError("Windy catalog contains duplicate station ids")
    if reported_total is None or len(stations) != reported_total:
        raise ValueError(
            f"Incomplete Windy catalog: downloaded {len(stations)}, expected {reported_total}"
        )
    return stations, {"pages": page, "reported_total": reported_total}


def build_database(
    stations: list[dict[str, Any]],
    output_path: Path,
    *,
    pages: int,
    reported_total: int,
    downloaded_at: str | None = None,
) -> dict[str, int]:
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
                """
                INSERT INTO pws_stations (
                    station_id, name, latitude, longitude, country, elevation_m,
                    temp_height_m, wind_height_m, station_type, operator_name,
                    operator_url, share_option, online, last_observation_time, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_station_values(row) for row in stations],
            )
            connection.execute(
                """
                INSERT INTO pws_station_rtree(
                    station_pk, min_latitude, max_latitude, min_longitude, max_longitude
                )
                SELECT station_pk, latitude, latitude, longitude, longitude
                FROM pws_stations
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            )
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "provider": "WINDY",
                "source_url": API_URL,
                "downloaded_at": downloaded_at or datetime.now(timezone.utc).isoformat(),
                "downloaded_pages": str(pages),
                "reported_station_count": str(reported_total),
            }
            connection.executemany(
                "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)", metadata.items()
            )
            connection.commit()
            connection.execute("ANALYZE")
            connection.execute("PRAGMA optimize")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            counts = {
                "stations": connection.execute("SELECT COUNT(*) FROM pws_stations").fetchone()[0],
                "online": connection.execute("SELECT COUNT(*) FROM pws_online_stations").fetchone()[0],
                "spatial": connection.execute("SELECT COUNT(*) FROM pws_station_rtree").fetchone()[0],
            }
        finally:
            connection.close()
        temporary_path.chmod(0o644)
        os.replace(temporary_path, output_path)
        return counts
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--api-key",
        default=(
            os.environ.get("METEOLABX_WINDY_API_KEY", "")
            or os.environ.get("WINDY_API_KEY", "")
        ),
        help="Windy API key; defaults to METEOLABX_WINDY_API_KEY or WINDY_API_KEY",
    )
    args = parser.parse_args()

    stations, inventory = fetch_inventory(args.api_key)
    counts = build_database(
        stations,
        args.output,
        pages=inventory["pages"],
        reported_total=inventory["reported_total"],
    )
    print(
        f"Saved {counts['stations']} Windy PWS stations "
        f"({counts['online']} online, {counts['spatial']} spatially indexed) "
        f"from {inventory['pages']} pages to {args.output}"
    )


if __name__ == "__main__":
    main()

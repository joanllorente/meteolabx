#!/usr/bin/env python3
"""Import the existing provider station inventories into one SQLite file."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "stations.sqlite"
SCHEMA_VERSION = "2"

PROVIDER_FILES = {
    "AEMET": DATA / "data_estaciones_aemet.json",
    "EUSKALMET": DATA / "data_estaciones_euskalmet.json",
    "FROST": DATA / "data_estaciones_frost.json",
    "METEOCAT": DATA / "data_estaciones_meteocat.json",
    "METEOFRANCE": DATA / "data_estaciones_meteofrance.json",
    "METEOGALICIA": DATA / "data_estaciones_meteogalicia.json",
    "METEOHUB_IT": DATA / "data_estaciones_meteohub_it.json",
    "METOFFICE": DATA / "data_estaciones_metoffice.json",
    "NWS": DATA / "data_estaciones_nws.json",
    "POEM": DATA / "data_estaciones_poem.json",
    "IEM": DATA / "data_estaciones_iem.json",
}

LIST_KEYS = ("estaciones", "stations", "listaEstacionsMeteo")
ID_KEYS = (
    "id", "source_id", "idema", "stationId", "codi", "id_station",
    "idEstacion", "codigo", "geohash",
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE inventory_sources (
    provider TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    payload_type TEXT NOT NULL,
    list_key TEXT,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    source_metadata_json TEXT NOT NULL
);

CREATE TABLE station_inventory_records (
    record_pk INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal >= 0),
    source_station_id TEXT,
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    FOREIGN KEY (provider) REFERENCES inventory_sources(provider),
    UNIQUE (provider, source_ordinal)
);

CREATE INDEX idx_station_inventory_provider
ON station_inventory_records(provider);

CREATE INDEX idx_station_inventory_source_id
ON station_inventory_records(provider, source_station_id);

CREATE TABLE stations (
    station_pk INTEGER PRIMARY KEY,
    source_record_pk INTEGER NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    network_code TEXT NOT NULL DEFAULT '',
    station_id TEXT NOT NULL,
    name TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    elevation_m REAL,
    timezone TEXT,
    country TEXT,
    region TEXT,
    locality TEXT,
    online INTEGER CHECK (online IN (0, 1)),
    has_historical INTEGER NOT NULL DEFAULT 0 CHECK (has_historical IN (0, 1)),
    manual INTEGER NOT NULL DEFAULT 0 CHECK (manual IN (0, 1)),
    FOREIGN KEY (source_record_pk) REFERENCES station_inventory_records(record_pk),
    UNIQUE (provider, network_code, station_id)
);

CREATE TABLE station_sensors (
    station_pk INTEGER PRIMARY KEY,
    thermometer INTEGER CHECK (thermometer IN (0, 1)),
    hygrometer INTEGER CHECK (hygrometer IN (0, 1)),
    barometer INTEGER CHECK (barometer IN (0, 1)),
    anemometer INTEGER CHECK (anemometer IN (0, 1)),
    wind_vane INTEGER CHECK (wind_vane IN (0, 1)),
    rain_gauge INTEGER CHECK (rain_gauge IN (0, 1)),
    pyranometer INTEGER CHECK (pyranometer IN (0, 1)),
    uv INTEGER CHECK (uv IN (0, 1)),
    FOREIGN KEY (station_pk) REFERENCES stations(station_pk) ON DELETE CASCADE
);

CREATE INDEX idx_stations_provider ON stations(provider);
CREATE INDEX idx_stations_station_id ON stations(provider, station_id);
CREATE INDEX idx_stations_country ON stations(country);
CREATE INDEX idx_stations_online ON stations(online);
CREATE INDEX idx_stations_has_historical ON stations(has_historical);
CREATE INDEX idx_stations_lat_lon ON stations(latitude, longitude);

CREATE VIRTUAL TABLE station_rtree USING rtree(
    station_pk,
    min_latitude, max_latitude,
    min_longitude, max_longitude
);

CREATE TABLE station_aliases (
    alias_pk INTEGER PRIMARY KEY,
    station_pk INTEGER NOT NULL,
    canonical_station_pk INTEGER NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    method TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(evidence_json)),
    FOREIGN KEY (station_pk) REFERENCES stations(station_pk),
    FOREIGN KEY (canonical_station_pk) REFERENCES stations(station_pk),
    UNIQUE (station_pk, canonical_station_pk),
    CHECK (station_pk <> canonical_station_pk)
);

CREATE TABLE station_alias_observation_checks (
    check_pk INTEGER PRIMARY KEY,
    alias_pk INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'conflict', 'inconclusive', 'error')),
    matched_hours INTEGER NOT NULL DEFAULT 0,
    compared_values INTEGER NOT NULL DEFAULT 0,
    agreeing_values INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    FOREIGN KEY (alias_pk) REFERENCES station_aliases(alias_pk) ON DELETE CASCADE
);

CREATE INDEX idx_alias_observation_checks_alias
ON station_alias_observation_checks(alias_pk, checked_at);

CREATE TABLE station_visibility_overrides (
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

CREATE INDEX idx_station_visibility_hidden
ON station_visibility_overrides(hidden);

CREATE VIEW connectable_stations AS
SELECT s.*
FROM stations s
LEFT JOIN station_visibility_overrides v USING(station_pk)
WHERE s.provider <> 'IEM'
  AND COALESCE(v.hidden, 0) = 0;
"""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _split_payload(payload: Any) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None, {}
    if not isinstance(payload, dict):
        raise ValueError("Inventory root must be a JSON array or object")
    for key in LIST_KEYS:
        rows = payload.get(key)
        if isinstance(rows, list):
            metadata = {name: value for name, value in payload.items() if name != key}
            return [row for row in rows if isinstance(row, dict)], key, metadata
    raise ValueError("Inventory object does not contain a recognized station list")


def _source_station_id(row: dict[str, Any]) -> str | None:
    for key in ID_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return None


DEFAULT_TIMEZONES = {
    "AEMET": "Europe/Madrid", "EUSKALMET": "Europe/Madrid",
    "FROST": "Europe/Oslo", "METEOCAT": "Europe/Madrid",
    "METEOFRANCE": "Europe/Paris", "METEOGALICIA": "Europe/Madrid",
    "METEOHUB_IT": "Europe/Rome", "METOFFICE": "Europe/London",
    "POEM": "Europe/Madrid",
}
SENSOR_KEYS = (
    "thermometer", "hygrometer", "barometer", "anemometer",
    "wind_vane", "rain_gauge", "pyranometer", "uv",
)
HISTORICAL_PROVIDER_IDS = {"AEMET", "METEOCAT", "METEOFRANCE", "METEOGALICIA"}
IEM_HISTORICAL_NETWORK_MARKERS = ("ASOS", "AWOS", "METAR")

# Redes IEM de observadores MANUALES: COOP (cooperativos del NWS, máx/mín una
# vez al día) y CoCoRaHS (voluntarios con pluviómetro, una lectura diaria).
# El resto de proveedores del catálogo son redes automáticas.
IEM_MANUAL_NETWORK_MARKERS = ("_COOP", "COCORAHS")


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _nested_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("SPANISH") or value.get("BASQUE") or "").strip() or None
    return str(value).strip() if value not in (None, "") else None


def _iem_network_has_historical(network: Any) -> bool:
    network_code = str(network or "").strip().upper()
    return any(marker in network_code for marker in IEM_HISTORICAL_NETWORK_MARKERS)


def _iem_network_is_manual(network: Any) -> bool:
    network_code = str(network or "").strip().upper()
    return any(marker in network_code for marker in IEM_MANUAL_NETWORK_MARKERS)


def _normalized_station(provider: str, row: dict[str, Any]) -> tuple[Any, ...] | None:
    coordinates = row.get("coordenades") if isinstance(row.get("coordenades"), dict) else {}
    station_id = {
        "AEMET": row.get("idema"), "EUSKALMET": row.get("stationId"),
        "METEOCAT": row.get("codi"), "METEOFRANCE": row.get("id_station"),
        "METEOGALICIA": row.get("idEstacion"), "METOFFICE": row.get("geohash") or row.get("id"),
        "POEM": row.get("codigo"),
    }.get(provider, row.get("id") or row.get("source_id"))
    station_id = str(station_id or "").strip()
    if not station_id:
        return None
    name = {
        "AEMET": row.get("nombre"), "EUSKALMET": row.get("displayName") or _nested_name(row.get("name")),
        "METEOCAT": row.get("nom"), "METEOGALICIA": row.get("estacion"),
        "METOFFICE": row.get("display_name") or row.get("station_name") or row.get("name"),
        "POEM": row.get("nombre"),
    }.get(provider, row.get("name") or row.get("nom_usuel"))
    network = str(
        row.get("network")
        or ((row.get("xarxa") or {}).get("codi") if isinstance(row.get("xarxa"), dict) else "")
        or ""
    ).strip()
    latitude = _number(coordinates.get("latitud") if coordinates else _first(row, "lat", "latitude"))
    longitude = _number(coordinates.get("longitud") if coordinates else _first(row, "lon", "longitude"))
    elevation = _number(_first(row, "elev", "altitude", "altitude_m", "altitud", "alt"))
    timezone = str(_first(row, "tz", "olson_time_zone") or DEFAULT_TIMEZONES.get(provider, "")).strip() or None
    country = _first(row, "country_code", "country")
    region = _first(row, "provincia", "province", "region", "state", "county")
    locality = _first(row, "municipality", "municipi", "concello", "area")
    region = _nested_name(region)
    locality = _nested_name(locality)
    online_raw = _first(row, "online", "active_now")
    online = int(bool(online_raw)) if online_raw is not None else None
    has_historical = int(
        provider in HISTORICAL_PROVIDER_IDS
        or (provider == "IEM" and _iem_network_has_historical(network))
    )
    manual = int(provider == "IEM" and _iem_network_is_manual(network))
    return (
        provider, network, station_id, str(name or station_id).strip(), latitude,
        longitude, elevation, timezone, _nested_name(country), region, locality, online,
        has_historical, manual,
    )


def build_database(
    output_path: Path,
    *,
    provider_files: dict[str, Path] | None = None,
) -> dict[str, Any]:
    sources = provider_files or PROVIDER_FILES
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    provider_counts: dict[str, int] = {}

    try:
        connection = sqlite3.connect(temporary_path)
        try:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )
            connection.execute(
                "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)",
                ("contains_iem", str("IEM" in sources).lower()),
            )

            for provider, source_path in sources.items():
                payload = json.loads(source_path.read_text(encoding="utf-8"))
                rows, list_key, metadata = _split_payload(payload)
                provider_counts[provider] = len(rows)
                connection.execute(
                    """
                    INSERT INTO inventory_sources(
                        provider, source_file, payload_type, list_key,
                        record_count, source_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider,
                        source_path.name,
                        "array" if isinstance(payload, list) else "object",
                        list_key,
                        len(rows),
                        _compact_json(metadata),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO station_inventory_records(
                        provider, source_ordinal, source_station_id, raw_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (provider, ordinal, _source_station_id(row), _compact_json(row))
                        for ordinal, row in enumerate(rows)
                    ),
                )

                record_pks = {
                    ordinal: record_pk
                    for record_pk, ordinal in connection.execute(
                        """
                        SELECT record_pk, source_ordinal
                        FROM station_inventory_records
                        WHERE provider = ?
                        ORDER BY source_ordinal
                        """,
                        (provider,),
                    )
                }
                for ordinal, row in enumerate(rows):
                    normalized = _normalized_station(provider, row)
                    if normalized is None:
                        continue
                    cursor = connection.execute(
                        """
                        INSERT INTO stations(
                            source_record_pk, provider, network_code, station_id,
                            name, latitude, longitude, elevation_m, timezone,
                            country, region, locality, online, has_historical, manual
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (record_pks[ordinal], *normalized),
                    )
                    station_pk = int(cursor.lastrowid)
                    sensors = row.get("sensors") if isinstance(row.get("sensors"), dict) else None
                    if sensors is not None:
                        connection.execute(
                            """
                            INSERT INTO station_sensors(
                                station_pk, thermometer, hygrometer, barometer,
                                anemometer, wind_vane, rain_gauge, pyranometer, uv
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                station_pk,
                                *(int(bool(sensors.get(key))) if key in sensors else None for key in SENSOR_KEYS),
                            ),
                        )

            connection.commit()
            connection.execute(
                """
                INSERT INTO station_rtree(
                    station_pk, min_latitude, max_latitude, min_longitude, max_longitude
                )
                SELECT station_pk, latitude, latitude, longitude, longitude
                FROM stations
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            )
            connection.commit()
            connection.execute("ANALYZE")
            connection.execute("PRAGMA optimize")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            imported = connection.execute("SELECT COUNT(*) FROM station_inventory_records").fetchone()[0]
            normalized_count = connection.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
            connectable_count = connection.execute("SELECT COUNT(*) FROM connectable_stations").fetchone()[0]
            spatial_count = connection.execute("SELECT COUNT(*) FROM station_rtree").fetchone()[0]
        finally:
            connection.close()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"SQLite validation failed: integrity={integrity!r}, foreign_keys={foreign_key_errors!r}"
            )
        os.replace(temporary_path, output_path)
        return {
            "providers": len(provider_counts),
            "records": imported,
            "normalized": normalized_count,
            "connectable": connectable_count,
            "spatial": spatial_count,
            "provider_counts": provider_counts,
        }
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_database(args.output)
    print(
        f"Saved {result['records']} raw and {result['normalized']} normalized station records "
        f"from {result['providers']} providers to {args.output}"
    )
    for provider, count in result["provider_counts"].items():
        print(f"  {provider:14} {count:6}")


if __name__ == "__main__":
    main()

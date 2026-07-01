import json
import sqlite3

import pytest

import scripts.validate_station_alias_observations as validator
from scripts.validate_station_alias_observations import compare_hourly, validate_batch


def test_compare_hourly_confirms_repeated_matching_measurements():
    source = {hour: {"temperature_c": 20.0, "humidity_pct": 60.0} for hour in range(4)}
    iem = {hour: {"temperature_c": 20.1, "humidity_pct": 60.5} for hour in range(4)}
    result = compare_hourly(source, iem)
    assert result["status"] == "confirmed"
    assert result["matched_hours"] == 4


def test_compare_hourly_rejects_repeated_temperature_conflict():
    source = {hour: {"temperature_c": 20.0, "humidity_pct": 60.0} for hour in range(4)}
    iem = {hour: {"temperature_c": 25.0, "humidity_pct": 80.0} for hour in range(4)}
    result = compare_hourly(source, iem)
    assert result["status"] == "conflict"


def test_compare_hourly_needs_four_hours():
    source = {hour: {"temperature_c": 20.0} for hour in range(3)}
    iem = {hour: {"temperature_c": 20.0} for hour in range(3)}
    assert compare_hourly(source, iem)["status"] == "inconclusive"


def test_compare_hourly_accepts_abundant_multivariable_evidence():
    source = {
        hour: {"temperature_c": 20.0, "humidity_pct": 60.0, "wind_kmh": 5.0}
        for hour in range(4)
    }
    iem = {
        hour: {"temperature_c": 20.1, "humidity_pct": 60.5, "wind_kmh": 5.2}
        for hour in range(4)
    }
    assert compare_hourly(source, iem)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_validate_batch_processes_candidates_concurrently(tmp_path, monkeypatch):
    database = tmp_path / "stations.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE stations (
            station_pk INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            station_id TEXT NOT NULL,
            network_code TEXT,
            name TEXT,
            timezone TEXT
        );
        CREATE TABLE station_aliases (
            alias_pk INTEGER PRIMARY KEY,
            station_pk INTEGER NOT NULL,
            canonical_station_pk INTEGER NOT NULL,
            method TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO stations VALUES
            (1, 'IEM', 'IEM1', 'TEST_NET', 'IEM Station 1', 'UTC'),
            (2, 'NWS', 'SRC1', NULL, 'Source Station 1', 'UTC'),
            (3, 'IEM', 'IEM2', 'TEST_NET', 'IEM Station 2', 'UTC'),
            (4, 'NWS', 'SRC2', NULL, 'Source Station 2', 'UTC');
        INSERT INTO station_aliases VALUES
            (10, 1, 2, 'inventory_probable', 0.8, '{}', 0),
            (11, 3, 4, 'inventory_secure', 0.95, '{}', 0);
        """
    )
    connection.close()

    async def fake_source_series(provider, station_id, client):
        base = 1767225600
        return {
            "epochs": [base + hour * 3600 for hour in range(4)],
            "temps": [20.0, 20.0, 20.0, 20.0],
            "humidities": [60.0, 60.0, 60.0, 60.0],
        }

    async def fake_iem_rows(network, station_id, dates, client):
        return [
            {"utc_valid": f"2026-01-01T0{hour}:00:00+00:00", "tmpf": 68.0, "relh": 60.0}
            for hour in range(4)
        ]

    monkeypatch.setattr(validator, "_source_series", fake_source_series)
    monkeypatch.setattr(validator, "_iem_rows", fake_iem_rows)

    counts = await validate_batch(
        database, limit=2, provider="NWS", concurrency=2, include_secure=True,
    )

    assert counts == {"confirmed": 2, "conflict": 0, "inconclusive": 0, "error": 0}
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT COUNT(*) FROM station_alias_observation_checks WHERE status = 'confirmed'"
    ).fetchone()[0] == 2
    evidence = connection.execute(
        "SELECT evidence_json FROM station_aliases WHERE alias_pk = 10"
    ).fetchone()[0]
    assert "observation_comparison" in json.loads(evidence)
    connection.close()


@pytest.mark.asyncio
async def test_validate_batch_skips_secure_candidates_by_default(tmp_path, monkeypatch):
    database = tmp_path / "stations.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE stations (
            station_pk INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            station_id TEXT NOT NULL,
            network_code TEXT,
            name TEXT,
            timezone TEXT
        );
        CREATE TABLE station_aliases (
            alias_pk INTEGER PRIMARY KEY,
            station_pk INTEGER NOT NULL,
            canonical_station_pk INTEGER NOT NULL,
            method TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO stations VALUES
            (1, 'IEM', 'IEM1', 'TEST_NET', 'IEM Station 1', 'UTC'),
            (2, 'AEMET', 'SRC1', NULL, 'Source Station 1', 'UTC');
        INSERT INTO station_aliases VALUES
            (10, 1, 2, 'inventory_secure', 0.95, '{}', 0);
        """
    )
    connection.close()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("secure candidate should not be fetched by default")

    monkeypatch.setattr(validator, "_source_series", fail_if_called)

    counts = await validate_batch(database, limit=2, provider="AEMET", concurrency=2)

    assert counts == {"confirmed": 0, "conflict": 0, "inconclusive": 0, "error": 0}

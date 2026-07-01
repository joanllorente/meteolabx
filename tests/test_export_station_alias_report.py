import sqlite3

from scripts.export_station_alias_report import build_report


def test_build_report_buckets_aliases_by_latest_evidence(tmp_path):
    database = tmp_path / "stations.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE stations (
            station_pk INTEGER PRIMARY KEY,
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
            online INTEGER
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
        CREATE TABLE station_alias_observation_checks (
            check_pk INTEGER PRIMARY KEY,
            alias_pk INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            status TEXT NOT NULL,
            matched_hours INTEGER NOT NULL DEFAULT 0,
            compared_values INTEGER NOT NULL DEFAULT 0,
            agreeing_values INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO stations VALUES
            (1, 'IEM', 'AA_NET', 'AAA', 'IEM A', 1, 2, 3, 'UTC', 'US', 'AA', '', 1),
            (2, 'NWS', '', 'SRC_A', 'Source A', 1, 2, 3, 'UTC', 'US', 'AA', '', 1),
            (3, 'IEM', 'BB_NET', 'BBB', 'IEM B', 4, 5, 6, 'UTC', 'US', 'BB', '', 1),
            (4, 'NWS', '', 'SRC_B', 'Source B', 4, 5, 6, 'UTC', 'US', 'BB', '', 1),
            (5, 'IEM', 'CC_NET', 'CCC', 'IEM C', 7, 8, 9, 'UTC', 'US', 'CC', '', 1),
            (6, 'NWS', '', 'SRC_C', 'Source C', 7, 8, 9, 'UTC', 'US', 'CC', '', 1);
        INSERT INTO station_aliases VALUES
            (10, 1, 2, 'observation_confirmed', 0.995, '{}', 0),
            (11, 3, 4, 'inventory_probable', 0.7, '{}', 0),
            (12, 5, 6, 'inventory_secure', 0.95, '{}', 0);
        INSERT INTO station_alias_observation_checks VALUES
            (100, 10, '2026-01-01T00:00:00+00:00', 'inconclusive', 1, 1, 1, '{}'),
            (101, 10, '2026-01-01T01:00:00+00:00', 'confirmed', 4, 8, 8, '{"agreement_ratio":1.0}'),
            (102, 11, '2026-01-01T01:00:00+00:00', 'conflict', 4, 8, 1, '{"agreement_ratio":0.125}');
        """
    )
    connection.close()

    report = build_report(database, "NWS")

    assert report["summary"]["total_aliases"] == 3
    assert report["summary"]["counts"]["observation_confirmed"] == 1
    assert report["summary"]["counts"]["observation_conflict"] == 1
    assert report["summary"]["counts"]["inventory_secure_unsampled"] == 1
    confirmed = report["buckets"]["observation_confirmed"][0]
    assert confirmed["latest_check"]["agreement_ratio"] == 1.0

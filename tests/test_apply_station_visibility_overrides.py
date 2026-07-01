import sqlite3

from scripts.apply_station_visibility_overrides import apply_confirmed_iem_duplicates


def test_apply_confirmed_iem_duplicates_hides_iem_copy(tmp_path):
    database = tmp_path / "stations.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE stations (
            station_pk INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            station_id TEXT NOT NULL
        );
        CREATE TABLE station_aliases (
            alias_pk INTEGER PRIMARY KEY,
            station_pk INTEGER NOT NULL,
            canonical_station_pk INTEGER NOT NULL,
            method TEXT NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO stations VALUES
            (1, 'IEM', 'IEM_COPY'),
            (2, 'FROST', 'SN12345'),
            (3, 'IEM', 'IEM_PENDING'),
            (4, 'FROST', 'SN99999');
        INSERT INTO station_aliases VALUES
            (10, 1, 2, 'observation_confirmed', 0),
            (11, 3, 4, 'inventory_secure', 0);
        """
    )
    connection.close()

    changed = apply_confirmed_iem_duplicates(database, provider="FROST")

    assert changed == 1
    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT station_pk, hidden, preferred_station_pk, source_alias_pk
        FROM station_visibility_overrides
        """
    ).fetchall()
    assert rows == [(1, 1, 2, 10)]
    connection.close()

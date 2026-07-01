import json
import sqlite3

import pytest

from scripts.build_iem_sqlite import build_database


def _inventory():
    return {
        "generated_at": "2026-06-22T00:00:00Z",
        "source": {"networks": "networks", "stations": "stations"},
        "reported_station_count": 2,
        "networks": [{"code": "XX_TEST", "name": "Test Network"}],
        "stations": [
            {
                "id": "24", "name": "Alpha", "network": "XX_TEST",
                "lat": 41.0, "lon": 2.0, "elev": 10, "online": True,
                "attributes": {"WMO_ID": "12345"},
            },
            {
                "id": "25", "name": "Beta", "network": "XX_TEST",
                "lat": 42.0, "lon": 3.0, "elev": None, "online": False,
            },
        ],
    }


def test_build_iem_sqlite_preserves_identity_and_spatial_index(tmp_path):
    source = tmp_path / "inventory.json"
    target = tmp_path / "inventory.sqlite"
    source.write_text(json.dumps(_inventory()), encoding="utf-8")

    counts = build_database(source, target)

    assert counts == {
        "networks": 1, "stations": 2, "online": 1,
        "spatial": 2, "integrity_errors": 0,
    }
    with sqlite3.connect(target) as connection:
        row = connection.execute(
            "SELECT network_code, station_id, attributes_json FROM iem_stations WHERE station_id = '24'"
        ).fetchone()
        assert row == ("XX_TEST", "24", '{"WMO_ID":"12345"}')
        assert connection.execute("SELECT COUNT(*) FROM iem_online_stations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM iem_station_rtree WHERE min_latitude >= 40.9 AND max_latitude <= 41.1"
        ).fetchone()[0] == 1


def test_build_iem_sqlite_rejects_duplicate_network_station_identity(tmp_path):
    payload = _inventory()
    payload["stations"].append(dict(payload["stations"][0]))
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(sqlite3.IntegrityError):
        build_database(source, tmp_path / "inventory.sqlite")

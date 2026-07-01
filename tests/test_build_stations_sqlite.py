import json
import sqlite3

from scripts.build_stations_sqlite import build_database


def test_build_unified_catalog_preserves_raw_data_and_provider_identity(tmp_path):
    aemet = tmp_path / "aemet.json"
    iem = tmp_path / "iem.json"
    target = tmp_path / "stations.sqlite"
    aemet.write_text(json.dumps([
        {
            "idema": "24", "nombre": "Original", "lat": 41.0,
            "lon": 2.0, "alt": 10,
            "sensors": {"thermometer": True, "rain_gauge": False},
        }
    ]), encoding="utf-8")
    iem.write_text(json.dumps({
        "generated_at": "2026-06-22T00:00:00Z",
        "stations": [
            {
                "id": "24", "name": "IEM copy", "network": "ES__ASOS",
                "lat": 41.001, "lon": 2.001, "online": True,
                "attributes": {"WMO_ID": "12345"},
            },
            {
                "id": "25", "name": "AWOS network", "network": "YY_AWOS",
                "lat": 42.0, "lon": 3.0, "online": False,
            },
            {
                "id": "26", "name": "Other network", "network": "ZZ_TEST",
                "lat": 43.0, "lon": 4.0, "online": False,
            },
        ],
    }), encoding="utf-8")

    result = build_database(target, provider_files={"AEMET": aemet, "IEM": iem})

    assert result["records"] == 4
    assert result["normalized"] == 4
    assert result["connectable"] == 1
    assert result["spatial"] == 4
    with sqlite3.connect(target) as connection:
        identities = connection.execute(
            "SELECT provider, network_code, station_id FROM stations ORDER BY station_pk"
        ).fetchall()
        assert identities == [
            ("AEMET", "", "24"),
            ("IEM", "ES__ASOS", "24"),
            ("IEM", "YY_AWOS", "25"),
            ("IEM", "ZZ_TEST", "26"),
        ]
        historical_flags = connection.execute(
            "SELECT provider, network_code, station_id, has_historical FROM stations ORDER BY station_pk"
        ).fetchall()
        assert historical_flags == [
            ("AEMET", "", "24", 1),
            ("IEM", "ES__ASOS", "24", 1),
            ("IEM", "YY_AWOS", "25", 1),
            ("IEM", "ZZ_TEST", "26", 0),
        ]
        assert connection.execute(
            "SELECT value FROM catalog_metadata WHERE key = 'contains_iem'"
        ).fetchone()[0] == "true"
        assert connection.execute(
            "SELECT thermometer, rain_gauge FROM station_sensors"
        ).fetchone() == (1, 0)
        raw = connection.execute(
            "SELECT raw_json FROM station_inventory_records WHERE provider = 'IEM' LIMIT 1"
        ).fetchone()[0]
        assert json.loads(raw)["attributes"] == {"WMO_ID": "12345"}
        assert connection.execute("SELECT COUNT(*) FROM station_rtree").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM station_aliases").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM station_visibility_overrides"
        ).fetchone()[0] == 0


def test_build_catalog_without_iem_marks_metadata(tmp_path):
    source = tmp_path / "aemet.json"
    source.write_text(json.dumps([{
        "idema": "X1", "nombre": "Test", "lat": 41, "lon": 2,
    }]), encoding="utf-8")
    target = tmp_path / "stations.sqlite"

    build_database(target, provider_files={"AEMET": source})

    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT value FROM catalog_metadata WHERE key = 'contains_iem'"
        ).fetchone()[0] == "false"

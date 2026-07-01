import json
import sqlite3

from scripts.build_station_aliases import build_aliases
from scripts.build_stations_sqlite import build_database


def _build_fixture(tmp_path):
    source = tmp_path / "source.json"
    iem = tmp_path / "iem.json"
    database = tmp_path / "stations.sqlite"
    source.write_text(json.dumps([
        {"idema": "24", "nombre": "Barcelona Aeroport", "lat": 41.0, "lon": 2.0, "alt": 5},
        {"idema": "LECO", "nombre": "La Coruna", "lat": 43.302, "lon": -8.377, "alt": 90, "attributes": {"ICAO": "LECO"}},
    ]), encoding="utf-8")
    iem.write_text(json.dumps({"stations": [
        {"id": "24", "name": "Unrelated", "network": "XX", "country": "ES", "lat": 41.0001, "lon": 2.0001},
        {"id": "LECO", "name": "La Coruna Airport", "network": "ES__ASOS", "country": "ES", "lat": 43.302, "lon": -8.377, "attributes": {"ICAO": "LECO"}},
    ]}), encoding="utf-8")
    build_database(database, provider_files={"AEMET": source, "IEM": iem})
    return database


def test_alias_candidates_do_not_trust_short_colliding_station_ids(tmp_path):
    database = _build_fixture(tmp_path)
    report = build_aliases(database, tmp_path / "report.json")

    assert report["candidates"] == 2
    assert report["iem_stations_with_multiple_original_candidates"] == 0
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT method, evidence_json, reviewed FROM station_aliases ORDER BY alias_pk"
        ).fetchall()
    evidence = [json.loads(row[1]) for row in rows]
    short_id = next(item for item in evidence if item["source"]["station_id"] == "24")
    assert short_id["shared_identifiers"] == []
    assert short_id["classification"] != "secure"
    icao = next(item for item in evidence if item["source"]["station_id"] == "LECO")
    assert icao["classification"] == "secure"
    assert all(row[2] == 0 for row in rows)


def test_alias_rebuild_preserves_reviewed_candidates(tmp_path):
    database = _build_fixture(tmp_path)
    build_aliases(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE station_aliases SET reviewed = 1 WHERE alias_pk = 1")
        connection.commit()

    build_aliases(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reviewed FROM station_aliases WHERE alias_pk = 1"
        ).fetchone()[0] == 1


def test_nws_catalog_membership_allows_stations_outside_us(tmp_path):
    nws = tmp_path / "nws.json"
    iem = tmp_path / "iem.json"
    database = tmp_path / "stations.sqlite"
    nws.write_text(json.dumps([{
        "id": "TEST", "name": "Border station", "lat": 49.0, "lon": -123.0,
    }]), encoding="utf-8")
    iem.write_text(json.dumps({"stations": [{
        "id": "TEST", "name": "Border station", "network": "CA_ASOS",
        "country": "CA", "lat": 49.0, "lon": -123.0,
    }]}), encoding="utf-8")
    build_database(database, provider_files={"NWS": nws, "IEM": iem})

    report = build_aliases(database)

    assert report["candidates"] == 1
    assert report["geographically_incompatible_pairs_rejected"] == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM station_aliases").fetchone()[0] == 1

import sqlite3

from scripts.build_windy_pws_sqlite import build_database


def test_build_database_creates_separate_pws_catalog(tmp_path):
    output = tmp_path / "pws_stations.sqlite"
    rows = [
        {
            "id": "abc123",
            "name": "Garden Station",
            "lat": 41.5,
            "lon": 2.1,
            "elev_m": 120,
            "agl_temp": 2,
            "agl_wind": 10,
            "station_type": "Davis Vantage Pro 2",
            "operator_text": "Amateur Observer",
            "operator_url": "https://example.test",
            "share_option": "public",
            "is_online": True,
            "last_observation_time": "2026-07-11T10:00:00.000Z",
        },
        {
            "id": "offline1",
            "name": "Old Station",
            "lat": None,
            "lon": None,
            "is_online": False,
        },
    ]

    counts = build_database(
        rows,
        output,
        pages=1,
        reported_total=2,
        downloaded_at="2026-07-11T12:00:00+00:00",
    )

    assert counts == {"stations": 2, "online": 1, "spatial": 1}
    with sqlite3.connect(output) as connection:
        station = connection.execute(
            """
            SELECT station_id, provider, name, online, station_type
            FROM pws_stations WHERE station_id = 'abc123'
            """
        ).fetchone()
        metadata = dict(connection.execute("SELECT key, value FROM catalog_metadata"))
        raw_json = connection.execute(
            "SELECT raw_json FROM pws_stations WHERE station_id = 'abc123'"
        ).fetchone()[0]

    assert station == ("abc123", "WINDY", "Garden Station", 1, "Davis Vantage Pro 2")
    assert metadata["reported_station_count"] == "2"
    assert metadata["downloaded_pages"] == "1"
    assert '"operator_text":"Amateur Observer"' in raw_json

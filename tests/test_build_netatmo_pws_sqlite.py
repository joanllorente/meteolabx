import io
import json
import sqlite3

from scripts import build_netatmo_pws_sqlite as builder


SAMPLE = {
    "_id": "70:ee:50:00:00:01",
    "place": {
        "location": [2.17, 41.38],
        "country": "ES",
        "city": "Barcelona",
        "timezone": "Europe/Madrid",
        "altitude": 12,
    },
    "measures": {
        "outdoor": {"type": ["temperature", "humidity"], "res": {"1783800000": [27.5, 62]}},
        "main": {"type": ["pressure"], "res": {"1783800000": [1014.2]}},
        "wind": {
            "wind_strength": 14, "gust_strength": 22, "wind_angle": 180,
            "wind_timeutc": 1783800000,
        },
        "rain": {
            "rain_live": 0.2, "rain_60min": 0.4, "rain_24h": 1.2,
            "rain_timeutc": 1783800000,
        },
    },
}


def test_normalize_station_extracts_values_and_sensors() -> None:
    row = builder.normalize_station(SAMPLE)
    assert row is not None
    assert row["temperature_c"] == 27.5
    assert row["humidity_pct"] == 62
    assert row["pressure_hpa"] == 1014.2
    assert row["wind_kmh"] == 14
    assert row["rain_24h_mm"] == 1.2
    assert row["thermometer"] is True
    assert row["rain_gauge"] is True


def test_fetch_inventory_deduplicates_overlapping_tiles() -> None:
    payload = json.dumps({"status": "ok", "body": [SAMPLE]}).encode()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    stations, metadata = builder.fetch_inventory(
        "token",
        bbox=(41.0, 2.0, 42.0, 3.0),
        tile_size=0.5,
        opener=lambda *args, **kwargs: Response(payload),
        pause_s=0,
    )
    assert len(stations) == 1
    assert metadata["requests"] == 4


def test_build_database_is_atomic_and_spatially_indexed(tmp_path) -> None:
    output = tmp_path / "netatmo.sqlite"
    row = builder.normalize_station(SAMPLE)
    assert row is not None
    counts = builder.build_database([row], output, requests=1, downloaded_at="2026-07-11T20:00:00Z")
    assert counts == {"stations": 1, "spatial": 1}
    with sqlite3.connect(output) as connection:
        saved = connection.execute(
            "SELECT station_id, country, thermometer, rain_gauge FROM netatmo_stations"
        ).fetchone()
        assert saved == (SAMPLE["_id"], "ES", 1, 1)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_adaptive_inventory_splits_dense_tiles_and_uses_cache(tmp_path) -> None:
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        payload = {"status": "ok", "body": [SAMPLE] * (1000 if len(calls) == 1 else 1)}
        return Response(json.dumps(payload).encode())

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    _, metadata = builder.fetch_inventory(
        "token",
        bbox=(40.0, 1.0, 42.0, 3.0),
        tile_size=2.0,
        country="",
        opener=opener,
        pause_s=0,
        adaptive=True,
        split_threshold=1000,
        min_tile_size=1.0,
        cache_dir=tmp_path,
    )
    assert metadata["requests"] == 5
    assert metadata["tiles"] == 5

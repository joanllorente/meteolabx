from scripts import enrich_metoffice_elevation as elevation


def test_enrich_metoffice_elevation_fills_missing_only_by_default():
    stations = [
        {"id": "a", "lat": 51.5, "lon": -0.1, "elev": None, "altitude": None},
        {"id": "b", "lat": 52.0, "lon": -1.0, "elev": 25, "altitude": 25},
    ]
    calls = []

    def fake_fetcher(coords):
        calls.append(list(coords))
        return [44.0]

    stats = elevation.enrich_stations_with_elevation(
        stations,
        fetcher=fake_fetcher,
        batch_size=10,
    )

    assert stats["updated"] == 1
    assert stats["skipped_existing"] == 1
    assert calls == [[(51.5, -0.1)]]
    assert stations[0]["elev"] == 44
    assert stations[0]["altitude"] == 44
    assert stations[1]["elev"] == 25


def test_enrich_metoffice_elevation_overwrites_existing_when_requested():
    stations = [
        {"id": "a", "lat": 51.5, "lon": -0.1, "elev": 25, "altitude": 25},
        {"id": "b", "lat": None, "lon": -1.0, "elev": None, "altitude": None},
    ]

    stats = elevation.enrich_stations_with_elevation(
        stations,
        fetcher=lambda coords: [44.2],
        batch_size=10,
        overwrite=True,
    )

    assert stats["updated"] == 1
    assert stats["skipped_coordinates"] == 1
    assert stations[0]["elev"] == 44.2
    assert stations[0]["altitude"] == 44.2
    assert stations[0]["elevation_source"] == "open-meteo"


def test_open_meteo_batch_response_is_normalized(monkeypatch):
    captured = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"elevation": [15, 83.4]}

    monkeypatch.setattr(elevation, "_request_json", fake_request_json)

    values = elevation._fetch_open_meteo_elevation_batch(
        [(51.5074, -0.1278), (52.0, -1.0)],
        endpoint="https://example.test/v1/elevation",
        timeout=1,
        retries=0,
        retry_sleep=0,
    )

    assert values == [15.0, 83.4]
    assert captured["url"].startswith("https://example.test/v1/elevation?")
    assert "latitude=51.50740%2C52.00000" in captured["url"]
    assert "longitude=-0.12780%2C-1.00000" in captured["url"]

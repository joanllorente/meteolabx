from types import SimpleNamespace

from scripts import build_metoffice_inventory as inventory


def test_nearest_request_uses_metoffice_two_decimal_coordinates(monkeypatch):
    captured = {}

    def fake_request_json(url, **_kwargs):
        captured["url"] = url
        return {"geohash": "gcj8ds"}

    monkeypatch.setattr(inventory, "_request_json", fake_request_json)

    inventory._fetch_nearest(
        49.175,
        -8.825,
        base_url="https://example.test",
        nearest_path="/observation-land/1/nearest",
        api_key="key",
        lat_param="lat",
        lon_param="lon",
        timeout=1,
        retries=0,
        retry_sleep=0,
    )

    assert captured["url"] == "https://example.test/observation-land/1/nearest?lat=49.18&lon=-8.83"


def test_metoffice_nearest_cache_key_uses_api_coordinate_precision():
    assert inventory._cache_key(49.175, -8.825) == "nearest:49.18,-8.83"
    assert inventory._legacy_cache_key(49.175, -8.825) == "nearest:49.17500,-8.82500"


def test_metoffice_inventory_default_country_filter_removes_nearest_spillover():
    allowed = inventory._normalize_allowed_countries(inventory.DEFAULT_ALLOWED_COUNTRIES)

    assert inventory._station_country_allowed({"country": "England"}, allowed) is True
    assert inventory._station_country_allowed({"country": "Channel Islands"}, allowed) is True
    assert inventory._station_country_allowed({"country": "British Isles"}, allowed) is True
    assert inventory._station_country_allowed({"country": "Norway"}, allowed) is False
    assert inventory._station_country_allowed({"country": "France"}, allowed) is False


def test_metoffice_inventory_country_filter_can_be_disabled():
    assert inventory._normalize_allowed_countries(["all"]) is None
    assert inventory._station_country_allowed({"country": "Norway"}, None) is True


def test_metoffice_inventory_display_names_disambiguate_repeated_areas():
    stations = [
        {"area": "Cumbria", "country": "England", "geohash": "gctevv"},
        {"area": "Cumbria", "country": "England", "geohash": "gctqjs"},
        {"area": "Jersey", "country": "Channel Islands", "geohash": "gbwryn"},
    ]

    inventory._apply_display_names(stations)

    assert stations[0]["display_name"] == "gctevv - Cumbria, England"
    assert stations[1]["display_name"] == "gctqjs - Cumbria, England"
    assert stations[2]["display_name"] == "Jersey, Channel Islands"


def test_metoffice_station_names_are_extracted_from_official_page_payload():
    html = """
    <script>
    window.metoffice = window.metoffice || {};
    window.metoffice.synopticClimateStation = {
      locations: {"features":[
        {"properties":{"alt":25,"id":3772,"name":"Heathrow","type":"auto","country":"England"},
         "geometry":{"type":"Point","coordinates":{"first":51.47895,"second":-0.45158}},
         "type":"Feature"}
      ]},
      mode: 'current'
    };
    </script>
    """

    locations = inventory._extract_synoptic_station_locations(html)

    assert locations == [
        {
            "name": "Heathrow",
            "country": "England",
            "lat": 51.47895,
            "lon": -0.45158,
            "station_type": "auto",
            "station_id": 3772,
            "altitude_m": 25,
        }
    ]


def test_metoffice_station_names_match_nearby_geohashes_before_display_name_fallback():
    stations = [
        {
            "geohash": "gcpsvg",
            "area": "Greater London",
            "country": "England",
            "lat": 51.479187,
            "lon": -0.444946,
        },
        {
            "geohash": "gctevv",
            "area": "Cumbria",
            "country": "England",
            "lat": 54.126892,
            "lon": -3.257446,
        },
    ]
    named_locations = [
        {"name": "Heathrow", "country": "England", "lat": 51.47895, "lon": -0.45158, "station_type": "auto", "station_id": 3772},
        {"name": "Walney Island", "country": "England", "lat": 54.12474, "lon": -3.25657, "station_type": "auto", "station_id": 1234},
    ]

    matched = inventory._apply_named_station_matches(stations, named_locations, max_distance_km=2.0)
    inventory._apply_display_names(stations)

    assert matched == 2
    assert stations[0]["station_name"] == "Heathrow"
    assert stations[0]["display_name"] == "Heathrow"
    assert stations[1]["display_name"] == "Walney Island"


def test_metoffice_inventory_can_drop_unmatched_station_name_fallbacks():
    stations = [
        {"geohash": "gf5yws", "station_name": "Bealach Na Ba No 2"},
        {"geohash": "gfsb5g", "area": "Highland", "country": "Scotland"},
    ]

    removed = inventory._drop_unmatched_station_names(stations)

    assert removed == 1
    assert stations == [{"geohash": "gf5yws", "station_name": "Bealach Na Ba No 2"}]


def test_metoffice_official_probe_candidates_skip_existing_and_manual_by_default():
    stations = [{"geohash": "gcpsvg", "station_name": "Heathrow"}]
    named_locations = [
        {"name": "Heathrow", "country": "England", "station_type": "auto"},
        {"name": "Northolt", "country": "England", "station_type": "auto"},
        {"name": "A Manual Site", "country": "England", "station_type": "man"},
        {"name": "Wind Site", "country": "Scotland", "station_type": "wind"},
        {"name": "Norway Site", "country": "Norway", "station_type": "auto"},
    ]
    allowed_countries = inventory._normalize_allowed_countries(inventory.DEFAULT_ALLOWED_COUNTRIES)

    candidates = inventory._official_probe_candidates(
        named_locations,
        stations,
        allowed_types=inventory._normalize_probe_types(inventory.DEFAULT_OFFICIAL_PROBE_TYPES),
        allowed_countries=allowed_countries,
    )

    assert [candidate["name"] for candidate in candidates] == ["Northolt", "Wind Site"]


def test_metoffice_official_probe_candidates_skip_existing_name_variants():
    stations = [{"geohash": "gbwryn", "station_name": "St. Catherines Pt."}]
    named_locations = [
        {"name": "St Catherines Pt", "country": "England", "station_type": "auto"},
        {"name": "Northolt", "country": "England", "station_type": "auto"},
    ]

    candidates = inventory._official_probe_candidates(
        named_locations,
        stations,
        allowed_types=inventory._normalize_probe_types(["auto"]),
        allowed_countries=inventory._normalize_allowed_countries(inventory.DEFAULT_ALLOWED_COUNTRIES),
    )

    assert [candidate["name"] for candidate in candidates] == ["Northolt"]


def test_metoffice_official_probe_limit_zero_spends_no_api_calls(monkeypatch):
    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("No debería llamar al API con límite cero")

    monkeypatch.setattr(inventory, "_fetch_nearest", fail_fetch)
    args = SimpleNamespace(
        official_probe_types=["auto", "wind"],
        official_probe_limit=0,
        official_probe_verify=False,
        name_match_max_km=12.0,
    )

    requests_made, summary = inventory._probe_official_missing_coordinates(
        {},
        [{"name": "Northolt", "country": "England", "station_type": "auto", "lat": 51.548, "lon": -0.418}],
        cache={},
        args=args,
        api_key="key",
        allowed_countries=inventory._normalize_allowed_countries(inventory.DEFAULT_ALLOWED_COUNTRIES),
        max_api_requests=20,
        requests_made=0,
    )

    assert requests_made == 0
    assert summary["candidates"] == 1
    assert summary["attempted"] == 0


def test_metoffice_official_probe_too_far_reports_existing_geohash(monkeypatch):
    def fake_fetch_nearest(*_args, **_kwargs):
        return {
            "geohash": "gcpsvg",
            "area": "Greater London",
            "country": "England",
        }

    monkeypatch.setattr(inventory, "_fetch_nearest", fake_fetch_nearest)
    args = SimpleNamespace(
        official_probe_types=["auto"],
        official_probe_limit=1,
        official_probe_verify=False,
        name_match_max_km=12.0,
        base_url="https://example.test",
        nearest_path="/observation-land/1/nearest",
        lat_param="lat",
        lon_param="lon",
        timeout=1,
        retries=0,
        retry_sleep=0,
        sleep=0,
    )

    requests_made, summary = inventory._probe_official_missing_coordinates(
        {"gcpsvg": {"geohash": "gcpsvg", "station_name": "Heathrow", "country": "England"}},
        [
            {
                "name": "Remote Test Site",
                "country": "England",
                "station_type": "auto",
                "lat": 55.0,
                "lon": -5.0,
            }
        ],
        cache={},
        args=args,
        api_key="key",
        allowed_countries=inventory._normalize_allowed_countries(inventory.DEFAULT_ALLOWED_COUNTRIES),
        max_api_requests=20,
        requests_made=0,
    )

    assert requests_made == 1
    assert summary["attempted"] == 1
    assert summary["too_far"] == 1
    assert summary["too_far_existing_geohash"] == 1
    assert summary["too_far_new_geohash"] == 0

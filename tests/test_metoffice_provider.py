import json

from providers.metoffice_provider import MetOfficeProvider


def test_metoffice_provider_uses_inventory_display_name(tmp_path):
    stations_path = tmp_path / "metoffice.json"
    stations_path.write_text(
        json.dumps(
            [
                {
                    "geohash": "gctevv",
                    "area": "Cumbria",
                    "country": "England",
                    "display_name": "gctevv - Cumbria, England",
                    "lat": 54.1269,
                    "lon": -3.2574,
                    "elev": None,
                }
            ]
        ),
        encoding="utf-8",
    )

    provider = MetOfficeProvider(stations_path=str(stations_path))
    results = provider.search_nearby_stations(54.1, -3.2, max_results=1)

    assert len(results) == 1
    assert results[0].name == "gctevv - Cumbria, England"

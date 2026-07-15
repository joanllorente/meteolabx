from unittest.mock import patch

from providers.registry import search_nearby_stations


def test_windy_search_uses_complete_country_catalog() -> None:
    with patch(
        "utils.api_client.fetch_station_catalog_via_api",
        return_value={"count": 0, "stations": []},
    ) as fetch_catalog:
        search_nearby_stations(
            40.4168,
            -3.7038,
            max_results=5000,
            provider_ids=["WINDY"],
            countries=["ES"],
        )

    assert fetch_catalog.call_args.kwargs["provider_ids"] == ["WINDY"]
    assert fetch_catalog.call_args.kwargs["countries"] == ["ES"]

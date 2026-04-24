from tabs import map as map_tab


def test_split_map_provider_options_for_iberia_keeps_regional_providers_near():
    near, far = map_tab.split_map_provider_options(41.3710, 2.1280)

    assert "AEMET" in near
    assert "METEOCAT" in near
    assert "METEOFRANCE" in near
    assert "NWS" in far
    assert "FROST" in far


def test_split_map_provider_options_for_us_makes_nws_near():
    near, far = map_tab.split_map_provider_options(39.9526, -75.1652)

    assert "NWS" in near
    assert "AEMET" in far


def test_regional_catalog_spec_exposes_full_nws_catalog():
    spec = map_tab.regional_catalog_spec("NWS")

    assert spec is not None
    assert spec["max_results"] == 38000
    assert spec["lon"] == -98.5795


def test_provider_is_near_center_matches_country_regions():
    assert map_tab.provider_is_near_center("METEOFRANCE", 43.6045, 1.4440) is True
    assert map_tab.provider_is_near_center("FROST", 60.3913, 5.3221) is True
    assert map_tab.provider_is_near_center("NWS", 41.3710, 2.1280) is False

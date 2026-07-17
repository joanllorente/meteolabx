from pathlib import Path

from tabs import map as map_tab
from utils import geo


def test_map_region_helpers_share_the_lightweight_geo_implementation():
    assert map_tab.is_iberia_map_center is geo.is_iberia_map_center
    assert map_tab.is_france_map_center is geo.is_france_map_center
    assert map_tab.is_italy_map_center is geo.is_italy_map_center
    assert map_tab.is_norway_map_center is geo.is_norway_map_center


def test_precipitation_label_rows_keep_zero_and_station_identity():
    rows = map_tab._precipitation_label_rows([
        {
            "lat": 41.4, "lon": 2.1, "amount": 0.0,
            "provider": "METEOCAT", "station_id": "X1", "name": "Test",
        },
    ])

    assert rows == [{
        "lat": 41.4, "lon": 2.1, "amount": 0.0,
        "observed_at": None, "time": "", "provider": "METEOCAT",
        "station_id": "X1", "name": "Test", "country": "", "idx": 0,
    }]


def test_split_map_provider_options_for_iberia_keeps_regional_providers_near():
    near, far = map_tab.split_map_provider_options(41.3710, 2.1280)

    assert "AEMET" in near
    assert "METEOCAT" in near
    assert "METEOFRANCE" in near
    assert "NWS" in far
    assert "FROST" in far
    assert "METOFFICE" in far
    assert "METEOHUB_IT" in far


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
    assert map_tab.provider_is_near_center("METOFFICE", 51.5072, -0.1276) is True
    assert map_tab.provider_is_near_center("METEOHUB_IT", 41.9028, 12.4964) is True
    assert map_tab.provider_is_near_center("NWS", 41.3710, 2.1280) is False


def test_iem_map_fallback_policy_excludes_official_countries_and_us():
    # Países con proveedor dedicado en el mapa: sin fallback IEM.
    for country in ("ES", "FR", "IT", "NO", "US", "PT", "AT", "SE", "CA"):
        assert map_tab.country_uses_iem_map_fallback(country) is False

    assert map_tab.country_uses_iem_map_fallback("GB") is True
    assert map_tab.country_uses_iem_map_fallback("DE") is True


def test_provider_country_filter_keeps_official_country_scope():
    assert map_tab.provider_country_filter("AEMET", ["ES", "PT"]) == ["ES"]
    assert map_tab.provider_country_filter("METEOFRANCE", ["ES", "FR"]) == ["FR"]
    assert map_tab.provider_country_filter("IEM", ["ES", "PT"]) == ["ES", "PT"]


def test_regional_providers_are_batched_for_selected_countries():
    providers = map_tab.regional_provider_ids_for_countries(
        list(map_tab.ALL_MAP_PROVIDER_OPTIONS),
        ["ES"],
    )

    assert providers == ("AEMET", "EUSKALMET", "METEOCAT", "METEOGALICIA", "POEM")
    assert map_tab.regional_catalog_result_limit(providers) == 1960


def test_regional_provider_batch_excludes_unselected_official_countries():
    batches = map_tab.regional_provider_batches(
        list(map_tab.ALL_MAP_PROVIDER_OPTIONS),
        ["FR", "GB"],
    )

    assert batches == (
        ("FR", ("METEOFRANCE",)),
        ("GB", ("METOFFICE",)),
    )


def test_map_country_default_only_applies_before_filter_is_initialized():
    assert map_tab.map_country_default_enabled("ES", ("ES",), False) is True
    assert map_tab.map_country_default_enabled("ES", ("ES",), True) is False
    assert map_tab.map_country_default_enabled("GB", ("ES",), False) is False


def test_map_country_scope_is_automatic_for_value_maps(monkeypatch):
    assert map_tab.automatic_map_countries_for_center(41.3710, 2.1280) == ("ES", "PT")

    from server.services import stations

    monkeypatch.setattr(stations, "country_for_point", lambda lat, lon: "DE")
    assert map_tab.automatic_map_countries_for_center(52.52, 13.405) == ("DE",)


def test_map_filter_popovers_are_limited_to_station_mode():
    source = Path(map_tab.__file__).read_text(encoding="utf-8")

    assert map_tab.map_filter_controls_visible("stations") is True
    assert map_tab.map_filter_controls_visible("temperature") is False
    assert map_tab.map_filter_controls_visible("wind") is False
    assert map_tab.map_filter_controls_visible("precipitation") is False
    assert 'st.container(key="map_country_overlay")' in source
    assert 'st.container(key="map_sensor_overlay")' in source
    assert "if map_filter_controls_visible(view_mode):" in source
    assert 'st.session_state["map_sensor_filter"] = []' not in source
    assert source.index('st.container(key="map_sensor_overlay")') < source.index(
        'view_mode = st.radio('
    )
    assert 'left: 1.5px;' in source
    assert 'top: 119.5px;' in source
    assert 'top: 190px;' in source
    assert "if not nearest and not show_scalar_field:" in source


def test_country_multiselect_callback_normalizes_filter_state(monkeypatch):
    import streamlit as st

    state = {"picker": ["fr", "ES", ""]}
    monkeypatch.setattr(st, "session_state", state)

    map_tab._handle_map_country_selection_change("picker")

    assert state[map_tab.MAP_COUNTRY_FILTER_INITIALIZED_KEY] is True
    assert state["map_country_filter"] == ["ES", "FR"]


def test_legacy_country_codes_have_human_display_names():
    assert map_tab.country_display_name("AN") == "Antillas Neerlandesas"
    assert map_tab.country_display_name("KA") == "Islas Carolinas (Palau/Micronesia)"
    assert map_tab.country_display_name("RQ") == "Puerto Rico"
    assert map_tab.country_display_name("TU") == "Turquía"
    assert map_tab.country_display_name("TN") == "Túnez"


def test_country_colors_are_distinct_and_rgba():
    es_color = map_tab.country_color("ES")
    fr_color = map_tab.country_color("FR")
    tn_color = map_tab.country_color("TN")

    assert es_color != fr_color
    assert tn_color != [180, 180, 180, 190]
    assert len(tn_color) == 4
    assert all(0 <= channel <= 255 for channel in tn_color)


def test_map_country_counts_fallback_uses_local_inventory():
    counts = map_tab._fallback_map_country_counts(())

    assert counts["ES"] > 0
    assert counts["US"] > 0
    assert len(counts) > 100


def test_deck_frozen_view_state_only_jumps_on_signature_change(monkeypatch):
    import streamlit as st

    fake_state = {}
    monkeypatch.setattr(st, "session_state", fake_state)

    captured = {"latitude": 40.0, "longitude": -3.0, "zoom": 6.0}
    sig_a = ("stations", "style", 40.0, -3.0, 140, 111)

    frozen_1 = map_tab._deck_frozen_view_state(captured, sig_a)
    assert frozen_1["latitude"] == 40.0 and frozen_1["zoom"] == 6.0

    # Rerun solo-viewport: la cámara capturada cambió (pan del usuario) pero la
    # firma no → el initial_view_state renderizado NO debe moverse (evita el
    # repintado del deck en cada gesto).
    captured_panned = {"latitude": 42.5, "longitude": -8.9, "zoom": 11.0}
    frozen_2 = map_tab._deck_frozen_view_state(captured_panned, sig_a)
    assert frozen_2 == frozen_1

    # Cambio real de contenido (filtro/tema): la firma cambia → salta a la
    # cámara capturada para preservar el pan/zoom del usuario.
    sig_b = ("stations", "style", 40.0, -3.0, 140, 222)
    frozen_3 = map_tab._deck_frozen_view_state(captured_panned, sig_b)
    assert frozen_3["latitude"] == 42.5
    assert frozen_3["longitude"] == -8.9
    assert frozen_3["zoom"] == 11.0

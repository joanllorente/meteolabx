from pathlib import Path
from types import SimpleNamespace

from tabs import map as map_tab


def test_map_view_state_stays_stable_when_filters_change(monkeypatch):
    fake_st = SimpleNamespace(session_state={})
    monkeypatch.setattr(map_tab, "st", fake_st)

    initial = map_tab._map_session_view_state(40.4168, -3.7038, 6.3)
    initial["latitude"] = 41.0
    initial["longitude"] = -2.0
    initial["zoom"] = 8.4

    after_filter_change = map_tab._map_session_view_state(40.4168, -3.7038, 5.5)

    assert after_filter_change["latitude"] == 41.0
    assert after_filter_change["longitude"] == -2.0
    assert after_filter_change["zoom"] == 8.4


def test_map_view_state_resets_when_logical_center_changes(monkeypatch):
    fake_st = SimpleNamespace(session_state={})
    monkeypatch.setattr(map_tab, "st", fake_st)

    first = map_tab._map_session_view_state(40.4168, -3.7038, 6.3)
    first["latitude"] = 41.0
    first["longitude"] = -2.0
    first["zoom"] = 8.4

    moved = map_tab._map_session_view_state(43.0, -8.0, 7.3)

    assert moved["latitude"] == 43.0
    assert moved["longitude"] == -8.0
    assert moved["zoom"] == 7.3


def test_coerce_map_viewport_state_accepts_valid_browser_camera():
    viewport = map_tab._coerce_map_viewport_state(
        {"latitude": "41.3874", "longitude": "2.1686", "zoom": "11.25"}
    )

    assert viewport == {
        "latitude": 41.3874,
        "longitude": 2.1686,
        "zoom": 11.25,
    }


def test_coerce_map_viewport_state_rejects_invalid_browser_camera():
    assert map_tab._coerce_map_viewport_state(
        {"latitude": 120, "longitude": 2.1686, "zoom": 11}
    ) is None
    assert map_tab._coerce_map_viewport_state(
        {"latitude": 41.3874, "longitude": 2.1686, "zoom": 30}
    ) is None


def test_sync_map_view_state_from_browser_keeps_user_camera(monkeypatch):
    fake_st = SimpleNamespace(session_state={})
    monkeypatch.setattr(map_tab, "st", fake_st)
    monkeypatch.setattr(
        map_tab,
        "get_map_viewport",
        lambda key: {"latitude": 41.5, "longitude": -1.8, "zoom": 9.2},
    )

    map_tab._map_session_view_state(40.4168, -3.7038, 6.3)
    map_tab._sync_map_view_state_from_browser(40.4168, -3.7038, "light")

    assert fake_st.session_state["map_view_state"]["latitude"] == 41.5
    assert fake_st.session_state["map_view_state"]["longitude"] == -1.8
    assert fake_st.session_state["map_view_state"]["zoom"] == 9.2


def test_sync_map_view_state_from_browser_ignores_stale_center(monkeypatch):
    fake_st = SimpleNamespace(session_state={})
    monkeypatch.setattr(map_tab, "st", fake_st)
    monkeypatch.setattr(
        map_tab,
        "get_map_viewport",
        lambda key: {"latitude": 41.5, "longitude": -1.8, "zoom": 9.2},
    )

    map_tab._map_session_view_state(40.4168, -3.7038, 6.3)
    map_tab._sync_map_view_state_from_browser(43.0, -8.0, "light")

    assert fake_st.session_state["map_view_state"]["latitude"] == 40.4168
    assert fake_st.session_state["map_view_state"]["longitude"] == -3.7038
    assert fake_st.session_state["map_view_state"]["zoom"] == 6.3


def test_temperature_labels_keep_all_nearby_stations():
    rows = map_tab._temperature_label_rows([
        {
            "lat": 41.41864, "lon": 2.12379, "t": 27.2,
            "provider": "METEOCAT", "station_id": "D5",
            "name": "Barcelona - Observatori Fabra",
        },
        {
            "lat": 41.3839, "lon": 2.16775, "t": 28.1,
            "provider": "METEOCAT", "station_id": "X4",
            "name": "Barcelona - el Raval",
        },
        {
            "lat": 41.374998, "lon": 2.173886, "t": 28.4,
            "provider": "AEMET", "station_id": "0201X",
            "name": "BARCELONA DRASSANES",
        },
    ])

    assert [row["station_id"] for row in rows] == ["D5", "X4", "0201X"]
    assert [row["idx"] for row in rows] == [0, 1, 2]


def test_temperature_field_is_inserted_below_vector_water_and_labels():
    frontend = (
        Path(map_tab.__file__).resolve().parents[1]
        / "components"
        / "temperature_clusters_frontend"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert 'sourceLayer === "water"' in frontend
    assert 'layerId === "water"' in frontend
    assert 'layer.type === "symbol"' in frontend
    assert "map.addLayer(" in frontend
    assert "beforeId" in frontend
    assert '"raster-fade-duration": 0' in frontend


def test_temperature_legend_and_clusters_start_at_minus_twenty():
    frontend = (
        Path(map_tab.__file__).resolve().parents[1]
        / "components"
        / "temperature_clusters_frontend"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert map_tab._TEMP_FIELD_LEGEND_STOPS[0] == (-20, "#621692")
    assert map_tab._TEMP_FIELD_LEGEND_TICKS[0] == -20
    assert map_tab._TEMP_FIELD_PALETTE_VERSION == 2
    assert map_tab._TEMP_FIELD_ALGORITHM_VERSION == 5
    assert "[-20, [98, 22, 146]]" in frontend
    assert "[-45, [98, 22, 146]]" not in frontend


def test_map_wheel_zoom_uses_controlled_sensitivity():
    class FakeView:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    views = map_tab._map_deck_views(SimpleNamespace(View=FakeView))

    assert len(views) == 1
    assert views[0].kwargs["controller"]["scrollZoom"] == {
        "speed": 0.003,
        "smooth": False,
    }
    assert views[0].kwargs["controller"]["dragRotate"] is False


def test_temperature_clusters_debounce_continuous_zoom():
    frontend = (
        Path(map_tab.__file__).resolve().parents[1]
        / "components"
        / "temperature_clusters_frontend"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "let clusterSettleTimer = null" in frontend
    assert 'nextMap.on("move", hideDuringMove)' in frontend
    assert "if (interactionActive || renderFrame !== null) return" in frontend
    assert "interactionActive = false" in frontend
    assert "}, 120);" in frontend


def test_trackpad_zoom_does_not_scroll_the_page_or_stop_on_value_labels():
    root = Path(map_tab.__file__).resolve().parents[1] / "components"
    viewport = (root / "map_viewport_frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    clusters = (
        root / "temperature_clusters_frontend" / "index.html"
    ).read_text(encoding="utf-8")

    assert "function guardMapWheel(event)" in viewport
    assert "if (event.cancelable) event.preventDefault();" in viewport
    assert 'passive: false' in viewport
    assert "function relayWheelToMap(event)" in clusters
    assert 'button.addEventListener("wheel", relayWheelToMap' in clusters
    assert 'underlying.dispatchEvent(new WheelEvent("wheel"' in clusters


def test_temperature_clusters_support_tooltip_and_station_selection():
    frontend = (
        Path(map_tab.__file__).resolve().parents[1]
        / "components"
        / "temperature_clusters_frontend"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "showStationTooltip" in frontend
    assert "row.tmax" in frontend
    assert "row.tmin" in frontend
    assert 'type: "station"' in frontend
    assert "provider: String(row.provider" in frontend
    assert "componentIsConnected" in frontend
    assert "window.frameElement.isConnected" in frontend
    assert ".mlbx-temp-station" in frontend
    assert "pointer-events: auto" in frontend
    assert "z-index: 20" in frontend


def test_temperature_mode_avoids_duplicate_native_pick_layer():
    source = Path(map_tab.__file__).read_text(encoding="utf-8")

    assert 'id="temperature-stations-pick-layer"' not in source
    assert "temp_pick_rows" not in source
    assert "temp_max_short" in source
    assert "temp_min_short" in source
    assert "selectable=not show_scalar_field" in source


def test_selected_station_card_does_not_repeat_temperatures():
    source = Path(map_tab.__file__).read_text(encoding="utf-8")

    assert "selected_temps_html" not in source
    # Se conservan en el snapshot que recibe la ficha flotante del componente.
    assert "field_labels" in source
    assert 'row.get("tmax")' in source
    assert 'row.get("tmin")' in source


def test_map_fragment_does_not_dim_stale_content_during_selection():
    source = Path(map_tab.__file__).read_text(encoding="utf-8")

    assert '[data-stale="true"]' in source
    assert "opacity: 1 !important" in source
    assert "transition: none !important" in source


def test_wind_labels_require_speed_and_direction():
    rows = map_tab._wind_label_rows([
        {
            "lat": 41.4, "lon": 2.1, "speed": 18.5, "direction": 370,
            "gust": 31.0, "provider": "METEOCAT", "station_id": "X1",
            "name": "Barcelona",
        },
        {
            "lat": 42.0, "lon": 2.0, "speed": 25.0,
            "provider": "AEMET", "station_id": "X2", "name": "Sin rumbo",
        },
    ])

    assert len(rows) == 1
    assert rows[0]["speed"] == 18.5
    assert rows[0]["direction"] == 10.0
    assert rows[0]["idx"] == 0


def test_wind_mode_uses_fixed_size_arrows_and_scalar_background():
    frontend = (
        Path(map_tab.__file__).resolve().parents[1]
        / "components"
        / "temperature_clusters_frontend"
        / "index.html"
    ).read_text(encoding="utf-8")
    source = Path(map_tab.__file__).read_text(encoding="utf-8")

    assert 'options=["stations", "temperature", "wind", "precipitation"]' in source
    assert '"temperature" if show_temp_field else' in source
    assert '"wind" if show_wind_field else "precipitation"' in source
    assert ".mlbx-wind-station" in frontend
    assert "width: 34px" in frontend
    assert 'arrow.textContent = "↑"' in frontend
    assert "Number(direction) + 180" in frontend
    assert "zoom >= 16" in frontend

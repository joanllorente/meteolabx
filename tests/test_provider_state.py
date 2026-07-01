from utils import provider_state


def test_display_provider_station_id_shortens_meteohub_composite_id():
    raw_id = "dpcn-lombardia|46.29690|10.50656|ponte-di-legno-case-pirli"

    assert provider_state.display_provider_station_id("METEOHUB_IT", raw_id) == "dpcn-lombardia"
    assert provider_state.display_provider_station_id("METEOCAT", raw_id) == raw_id


def test_apply_wu_station_state_and_disconnect_clears_runtime(patch_streamlit, fake_session_state, monkeypatch):
    fake_session_state.update({
        "aemet_station_id": "3195",
        "provider_station_id": "3195",
        "wu_sensor_presence": {"temp": True},
        "wu_station_calibration": {"barometer_offset": 1.2},
    })
    patch_streamlit(provider_state)
    monkeypatch.setattr(provider_state, "clear_provider_runtime_cache", lambda provider_id: None)

    ok = provider_state.apply_wu_station_state("TEST123", "apikey", "39", connected=True)

    assert ok is True
    assert fake_session_state["connection_type"] == "WU"
    assert fake_session_state["wu_connected_station"] == "TEST123"
    assert fake_session_state["wu_connected_api_key"] == "apikey"
    assert fake_session_state["wu_connected_z"] == "39"
    assert fake_session_state["connected"] is True
    assert fake_session_state["_connection_loading"]["provider"] == "WU"
    assert "aemet_station_id" not in fake_session_state
    assert "provider_station_id" not in fake_session_state

    provider_state.disconnect_active_station()

    assert fake_session_state["connected"] is False
    assert fake_session_state["connection_type"] is None
    assert "_connection_loading" not in fake_session_state
    assert "wu_connected_station" not in fake_session_state
    assert "wu_sensor_presence" not in fake_session_state
    assert "wu_station_calibration" not in fake_session_state


def test_wu_runtime_station_has_priority_over_visible_input():
    state = {
        "connection_type": "WU",
        "connected": True,
        "active_station": "VISIBLE-LAST",
        "wu_connected_station": "CLICKED-FAVORITE",
    }

    assert provider_state.get_provider_station_id(state, "WU") == "CLICKED-FAVORITE"


def test_wu_connection_snapshot_uses_runtime_station_over_visible_input():
    state = {
        "connection_type": "WU",
        "connected": True,
        "active_station": "IROSES18",
        "wu_connected_station": "ILHOSP26",
        "station_lat": 41.371,
        "station_lon": 2.128,
        "station_elevation": 39,
    }

    snapshot = provider_state.build_connection_snapshot(state)

    assert snapshot is not None
    assert snapshot.station_id == "ILHOSP26"
    assert snapshot.station_name == "ILHOSP26"


def test_provider_snapshot_prefers_catalog_altitude_over_runtime_altitude(patch_streamlit, fake_session_state):
    patch_streamlit(provider_state)

    ok = provider_state.apply_provider_station_state(
        "AEMET",
        "9434",
        "ZARAGOZA AEROPUERTO",
        41.660556,
        -1.004167,
        249.0,
        connected=True,
    )
    assert ok is True

    # Observation effects may later update runtime altitude keys; the header
    # should still show the selected catalog altitude.
    fake_session_state["aemet_station_alt"] = 39.0
    fake_session_state["provider_station_alt"] = 39.0
    fake_session_state["station_elevation"] = 39.0

    snapshot = provider_state.build_connection_snapshot(fake_session_state)

    assert snapshot is not None
    assert snapshot.elevation_m == 249.0


def test_weatherlink_widget_keys_are_not_restored_as_runtime(patch_streamlit, fake_session_state):
    fake_session_state.update(
        {
            "connection_type": "WEATHERLINK",
            "weatherlink_input_api_key": "typed-key",
            "weatherlink_input_api_secret": "typed-secret",
            "weatherlink_input_altitude": "25",
            "weatherlink_station_selector": "old-station",
            "weatherlink_station_id": "old-station",
            "weatherlink_api_key": "old-runtime-key",
            "weatherlink_api_secret": "old-runtime-secret",
        }
    )
    patch_streamlit(provider_state)

    snapshot = provider_state._capture_connection_state()

    assert "weatherlink_input_api_key" not in snapshot
    assert "weatherlink_input_api_secret" not in snapshot
    assert "weatherlink_input_altitude" not in snapshot
    assert "weatherlink_station_selector" not in snapshot

    ok = provider_state.apply_weatherlink_station_state(
        {
            "station_id": "new-station",
            "station_name": "New Station",
            "latitude": 41.0,
            "longitude": 2.0,
            "elevation": 25,
        },
        "new-runtime-key",
        "new-runtime-secret",
        "25",
        connected=True,
    )

    assert ok is True
    assert fake_session_state["weatherlink_input_api_key"] == "typed-key"
    assert fake_session_state["weatherlink_input_api_secret"] == "typed-secret"
    assert fake_session_state["weatherlink_input_altitude"] == "25"
    assert fake_session_state["weatherlink_station_selector"] == "old-station"

    provider_state.restore_connection_state_from_loading_payload(fake_session_state["_connection_loading"])

    assert fake_session_state["weatherlink_input_api_key"] == "typed-key"
    assert fake_session_state["weatherlink_input_api_secret"] == "typed-secret"
    assert fake_session_state["weatherlink_input_altitude"] == "25"
    assert fake_session_state["weatherlink_station_selector"] == "old-station"

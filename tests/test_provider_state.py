from utils import provider_state


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

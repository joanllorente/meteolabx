import json
from types import SimpleNamespace

from config import (
    LS_APIKEY,
    LS_AUTOCONNECT,
    LS_AUTOCONNECT_TARGET,
    LS_STATION,
    LS_WEATHERLINK_APIKEY,
    LS_WEATHERLINK_APISECRET,
    LS_WEATHERLINK_STATION,
    LS_WEATHERLINK_Z,
    LS_WU_CALIBRATIONS,
    LS_WU_FORGOTTEN,
    LS_Z,
)
from utils import provider_state, storage
from utils.state_keys import AUTOCONNECT_ATTEMPTED


def _patch_storage(monkeypatch, patch_streamlit):
    patch_streamlit(storage)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: SimpleNamespace(getItem=lambda *args, **kwargs: None))


def _pending_writes(fake_session_state):
    return fake_session_state.get("_mlx_local_storage_pending_writes", {})


def test_wu_credentials_and_autoconnect_target_roundtrip(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    target = {
        "kind": "WU",
        "station": "IWU123",
        "api_key": "secret-key",
        "z": "42",
    }

    storage.set_local_storage(LS_STATION, "IWU123", "save")
    storage.set_local_storage(LS_APIKEY, "secret-key", "save")
    storage.set_local_storage(LS_Z, "42", "save")
    storage.set_local_storage(LS_AUTOCONNECT, "1", "save")
    storage.set_local_storage(LS_WU_FORGOTTEN, "0", "save")
    storage.set_stored_autoconnect_target(target)

    assert storage.get_stored_station() == "IWU123"
    assert storage.get_stored_apikey() == "secret-key"
    assert storage.get_stored_z() == "42"
    assert storage.get_stored_autoconnect() is True
    assert storage.get_local_storage_value(LS_WU_FORGOTTEN) == "0"
    assert storage.get_stored_autoconnect_target() == target
    assert fake_session_state["_mlx_session_autoconnect_enabled"] is True
    assert fake_session_state["_mlx_session_autoconnect_target"] == target

    pending = _pending_writes(fake_session_state)
    assert pending[LS_STATION] == "IWU123"
    assert pending[LS_APIKEY] == "secret-key"
    assert pending[LS_AUTOCONNECT] == "1"
    assert json.loads(pending[LS_AUTOCONNECT_TARGET]) == target


def test_weatherlink_credentials_read_from_snapshot_without_legacy_component(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    patch_streamlit(storage)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: None)
    fake_session_state["_mlx_local_storage_snapshot_ready"] = True
    fake_session_state["_mlx_local_storage_snapshot"] = {
        LS_WEATHERLINK_APIKEY: "weatherlink-key",
        LS_WEATHERLINK_APISECRET: "weatherlink-secret",
        LS_WEATHERLINK_Z: "39",
        LS_WEATHERLINK_STATION: "374964",
    }

    assert storage.get_local_storage_value(LS_WEATHERLINK_APIKEY) == "weatherlink-key"
    assert storage.get_local_storage_value(LS_WEATHERLINK_APISECRET) == "weatherlink-secret"
    assert storage.get_local_storage_value(LS_WEATHERLINK_Z) == "39"
    assert storage.get_local_storage_value(LS_WEATHERLINK_STATION) == "374964"


def test_forget_marks_all_wu_credentials_autoconnect_and_calibrations(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    storage.set_local_storage(LS_STATION, "IWU123", "save")
    storage.set_local_storage(LS_APIKEY, "secret-key", "save")
    storage.set_local_storage(LS_Z, "42", "save")
    storage.set_local_storage(LS_AUTOCONNECT, "1", "save")
    storage.set_stored_autoconnect_target({"kind": "WU", "station": "IWU123", "api_key": "secret-key", "z": "42"})
    storage.set_stored_wu_station_calibration("IWU123", {"rain_gauge": 1.2})

    storage.forget_local_storage_keys()

    pending = _pending_writes(fake_session_state)
    assert pending[LS_STATION] == storage._FORGET_MARKER
    assert pending[LS_APIKEY] == storage._FORGET_MARKER
    assert pending[LS_Z] == storage._FORGET_MARKER
    assert pending[LS_AUTOCONNECT_TARGET] == storage._FORGET_MARKER
    assert pending[LS_WU_CALIBRATIONS] == storage._FORGET_MARKER
    assert pending[LS_AUTOCONNECT] == "0"
    assert pending[LS_WU_FORGOTTEN] == "1"

    assert storage.get_stored_station() is None
    assert storage.get_stored_apikey() is None
    assert storage.get_stored_z() is None
    assert storage.get_stored_autoconnect() is False
    assert storage.get_stored_autoconnect_target() is None
    assert storage.get_stored_wu_calibrations() == {}


def test_provider_autoconnect_replaces_target_and_can_be_disabled(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)
    patch_streamlit(provider_state)

    station = {
        "provider_id": "METEOCAT",
        "station_id": "Z6",
        "name": "Sasseuva",
        "lat": 42.7,
        "lon": 0.7,
        "elevation_m": 2228,
        "station_tz": "Europe/Madrid",
    }

    assert provider_state.persist_provider_autoconnect_target(station) is True

    target = storage.get_stored_autoconnect_target()
    assert target["kind"] == "PROVIDER"
    assert target["provider_id"] == "METEOCAT"
    assert target["station_id"] == "Z6"
    assert target["station_name"] == "Sasseuva"
    assert target["elevation_m"] == 2228
    assert storage.get_stored_autoconnect() is True
    assert fake_session_state[AUTOCONNECT_ATTEMPTED] is False

    provider_state.disable_provider_autoconnect("autoconnect_toggle_")

    assert storage.get_stored_autoconnect() is False
    assert storage.get_stored_autoconnect_target() is None
    assert fake_session_state["_mlx_session_autoconnect_enabled"] is False
    assert "_mlx_session_autoconnect_target" not in fake_session_state


def test_switching_between_provider_and_wu_keeps_runtime_state_consistent(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    patch_streamlit(provider_state)
    monkeypatch.setattr(provider_state, "clear_provider_runtime_cache", lambda provider_id: None)

    provider_ok = provider_state.apply_station_selection(
        {
            "provider_id": "METEOCAT",
            "station_id": "Z6",
            "name": "Sasseuva",
            "lat": 42.7,
            "lon": 0.7,
            "elevation_m": 2228,
            "station_tz": "Europe/Madrid",
        },
        connected=True,
    )

    assert provider_ok is True
    assert fake_session_state["connected"] is True
    assert fake_session_state["connection_type"] == "METEOCAT"
    assert fake_session_state["provider_station_id"] == "Z6"
    assert fake_session_state["meteocat_station_id"] == "Z6"
    assert fake_session_state["_connection_loading"]["provider"] == "METEOCAT"

    fake_session_state["meteocat_cache_current"] = {"old": True}
    fake_session_state["provider_station_id"] = "Z6"

    wu_ok = provider_state.apply_wu_station_state("IWU123", "secret-key", "42", connected=True)

    assert wu_ok is True
    assert fake_session_state["connected"] is True
    assert fake_session_state["connection_type"] == "WU"
    assert fake_session_state["wu_connected_station"] == "IWU123"
    assert fake_session_state["wu_connected_api_key"] == "secret-key"
    assert fake_session_state["wu_connected_z"] == "42"
    assert fake_session_state["_connection_loading"]["provider"] == "WU"
    assert "provider_station_id" not in fake_session_state
    assert "meteocat_station_id" not in fake_session_state
    assert "meteocat_cache_current" not in fake_session_state


def test_local_storage_snapshot_unwraps_objects_and_ignores_object_object(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    storage.hydrate_local_storage_snapshot(
        {
            LS_STATION: {LS_STATION: "IWU123"},
            LS_APIKEY: {LS_APIKEY: "[object Object]"},
            LS_Z: {"wrapped": "123"},
            LS_AUTOCONNECT: True,
            LS_AUTOCONNECT_TARGET: {
                LS_AUTOCONNECT_TARGET: json.dumps(
                    {"kind": "WU", "station": "IWU123", "api_key": "secret-key", "z": "123"}
                )
            },
        }
    )

    assert storage.local_storage_snapshot_ready() is True
    assert storage.get_stored_station() == "IWU123"
    assert storage.get_stored_apikey() is None
    assert storage.get_stored_z() == "123"
    assert storage.get_stored_autoconnect() is True
    assert storage.get_stored_autoconnect_target() == {
        "kind": "WU",
        "station": "IWU123",
        "api_key": "secret-key",
        "z": "123",
    }


def test_corrupt_autoconnect_target_is_ignored_without_touching_credentials(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)
    storage.hydrate_local_storage_snapshot(
        {
            LS_STATION: "IWU123",
            LS_APIKEY: "secret-key",
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: "{bad-json",
        }
    )

    assert storage.get_stored_station() == "IWU123"
    assert storage.get_stored_apikey() == "secret-key"
    assert storage.get_stored_autoconnect() is True
    assert storage.get_stored_autoconnect_target() is None


def test_wu_calibrations_are_scoped_by_station_and_zero_removes_only_that_station(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    storage.set_stored_wu_station_calibration("iwu123", {"wind_vane": 15, "rain_gauge": 0})
    storage.set_stored_wu_station_calibration("IWU456", {"barometer": -2})

    assert storage.get_stored_wu_station_calibration("IWU123") == {"wind_vane": 15, "rain_gauge": 0}
    assert storage.get_stored_wu_station_calibration("iwu456") == {"barometer": -2}

    storage.set_stored_wu_station_calibration("IWU123", {"wind_vane": 0, "rain_gauge": 0})

    assert storage.get_stored_wu_station_calibration("IWU123") == {}
    assert storage.get_stored_wu_station_calibration("IWU456") == {"barometer": -2}


def test_provider_autoconnect_replaces_wu_target_without_deleting_saved_wu_credentials(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)
    patch_streamlit(provider_state)
    fake_session_state["auto_connect_wu_device"] = True
    fake_session_state["_wu_autoconnect_toggle_changed"] = True

    storage.set_local_storage(LS_STATION, "IWU123", "save")
    storage.set_local_storage(LS_APIKEY, "secret-key", "save")
    storage.set_local_storage(LS_Z, "42", "save")
    storage.set_local_storage(LS_AUTOCONNECT, "1", "save")
    storage.set_stored_autoconnect_target({"kind": "WU", "station": "IWU123", "api_key": "secret-key", "z": "42"})

    provider_state.persist_provider_autoconnect_target(
        {
            "provider_id": "AEMET",
            "station_id": "3195",
            "name": "Madrid, Retiro",
            "lat": 40.4,
            "lon": -3.7,
            "elevation_m": 667,
        }
    )

    target = storage.get_stored_autoconnect_target()
    assert target["kind"] == "PROVIDER"
    assert target["provider_id"] == "AEMET"
    assert target["station_id"] == "3195"
    assert storage.get_stored_station() == "IWU123"
    assert storage.get_stored_apikey() == "secret-key"
    assert storage.get_stored_z() == "42"
    assert fake_session_state["auto_connect_wu_device"] is True
    assert "_wu_autoconnect_toggle_changed" not in fake_session_state
    assert fake_session_state["_wu_autoconnect_ui_target_kind"] == "PROVIDER"
    assert fake_session_state["_wu_autoconnect_ui_last_value"] is False
    assert fake_session_state["_provider_autoconnect_takeover_pending"] is True
    assert fake_session_state["_provider_autoconnect_takeover_grace"] == 1


def test_pending_local_storage_writes_are_consumed_once(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    storage.queue_local_storage_writes({LS_STATION: "IWU123", LS_AUTOCONNECT: "1"})

    assert storage.consume_local_storage_writes() == {
        LS_STATION: "IWU123",
        LS_AUTOCONNECT: "1",
    }
    assert storage.consume_local_storage_writes() == {}


def test_local_storage_reads_use_write_cache_before_empty_component_instance(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    patch_streamlit(storage)
    target = {
        "kind": "PROVIDER",
        "provider_id": "METEOCAT",
        "station_id": "Z6",
        "station_name": "Sasseuva",
    }
    fake_session_state["_mlx_local_storage_key"] = "mlx_storage_test"
    fake_session_state[storage._WRITE_CACHE_KEY] = {
        LS_AUTOCONNECT: "1",
        LS_AUTOCONNECT_TARGET: json.dumps(target, separators=(",", ":")),
    }
    fake_session_state["_mlx_local_storage_snapshot"] = {
        LS_AUTOCONNECT: "",
        LS_AUTOCONNECT_TARGET: "",
    }
    monkeypatch.setattr(
        storage,
        "_get_local_storage",
        lambda: SimpleNamespace(getItem=lambda *args, **kwargs: ""),
    )

    assert storage.get_stored_autoconnect() is True
    assert storage.get_stored_autoconnect_target() == target


def test_passive_empty_snapshot_does_not_become_authoritative_cache(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)

    storage.hydrate_local_storage_snapshot(
        {
            LS_AUTOCONNECT: "",
            LS_AUTOCONNECT_TARGET: "",
        }
    )

    assert storage._WRITE_CACHE_KEY not in fake_session_state
    assert storage.get_stored_autoconnect() is False
    assert storage.get_stored_autoconnect_target() is None


def test_authoritative_provider_write_survives_later_empty_snapshot(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)
    target = {
        "kind": "PROVIDER",
        "provider_id": "METEOCAT",
        "station_id": "Z6",
        "station_name": "Sasseuva",
    }

    storage.set_stored_autoconnect_target(target)
    storage.set_local_storage(LS_AUTOCONNECT, "1", "save")
    storage.hydrate_local_storage_snapshot(
        {
            LS_AUTOCONNECT: "",
            LS_AUTOCONNECT_TARGET: "",
        }
    )

    assert storage.get_stored_autoconnect() is True
    assert storage.get_stored_autoconnect_target() == target


def test_local_storage_writes_create_session_cache_for_immediate_rerun(
    patch_streamlit,
    fake_session_state,
    monkeypatch,
):
    _patch_storage(monkeypatch, patch_streamlit)
    target = {
        "kind": "PROVIDER",
        "provider_id": "METEOCAT",
        "station_id": "Z6",
        "station_name": "Sasseuva",
    }

    storage.set_stored_autoconnect_target(target)
    storage.set_local_storage(LS_AUTOCONNECT, "1", "save")

    session_key = fake_session_state["_mlx_local_storage_key"]
    assert json.loads(fake_session_state[session_key][LS_AUTOCONNECT_TARGET]) == target
    assert fake_session_state[session_key][LS_AUTOCONNECT] == "1"
    assert json.loads(fake_session_state[storage._WRITE_CACHE_KEY][LS_AUTOCONNECT_TARGET]) == target
    assert fake_session_state[storage._WRITE_CACHE_KEY][LS_AUTOCONNECT] == "1"
    assert storage.get_stored_autoconnect() is True
    assert storage.get_stored_autoconnect_target() == target


def test_provider_autoconnect_widget_state_can_be_cleared_when_wu_becomes_target(
    patch_streamlit,
    fake_session_state,
):
    patch_streamlit(provider_state)
    fake_session_state.update(
        {
            "autoconnect_toggle_METEOCAT_Z6": True,
            "map_autoconnect_toggle_Meteocat_Z6": True,
            "_provider_autoconnect_toggle_changed": "autoconnect_toggle_METEOCAT_Z6",
            "_map_provider_autoconnect_toggle_changed": "map_autoconnect_toggle_Meteocat_Z6",
            "auto_connect_wu_device": True,
        }
    )

    provider_state.clear_provider_autoconnect_widget_state()

    assert "autoconnect_toggle_METEOCAT_Z6" not in fake_session_state
    assert "map_autoconnect_toggle_Meteocat_Z6" not in fake_session_state
    assert "_provider_autoconnect_toggle_changed" not in fake_session_state
    assert "_map_provider_autoconnect_toggle_changed" not in fake_session_state
    assert fake_session_state["auto_connect_wu_device"] is True

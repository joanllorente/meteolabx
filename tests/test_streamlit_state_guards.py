from types import SimpleNamespace

from utils import provider_state
from utils.state_keys import ACTIVE_KEY, ACTIVE_STATION, ACTIVE_Z, AUTOCONNECT_ATTEMPTED


class GuardedSessionState(dict):
    def __init__(self, *args, locked_keys=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.locked_keys = set(locked_keys)

    def __setitem__(self, key, value):
        if key in self.locked_keys:
            raise AssertionError(f"{key} should not be mutated after widget instantiation")
        return super().__setitem__(key, value)

    def pop(self, key, default=None):
        if key in self.locked_keys:
            raise AssertionError(f"{key} should not be popped after widget instantiation")
        return super().pop(key, default)


def _patch_provider_state(monkeypatch, session_state):
    monkeypatch.setattr(provider_state, "st", SimpleNamespace(session_state=session_state))
    monkeypatch.setattr(provider_state, "clear_provider_runtime_cache", lambda provider_id: None)


def test_apply_wu_station_state_does_not_mutate_rendered_input_widgets(monkeypatch):
    session_state = GuardedSessionState(
        {
            ACTIVE_STATION: "VISIBLE-STATION",
            ACTIVE_KEY: "VISIBLE-KEY",
            ACTIVE_Z: "12",
        },
        locked_keys={ACTIVE_STATION, ACTIVE_KEY, ACTIVE_Z},
    )
    _patch_provider_state(monkeypatch, session_state)

    assert provider_state.apply_wu_station_state("IWU123", "secret-key", "42", connected=True) is True

    assert session_state[ACTIVE_STATION] == "VISIBLE-STATION"
    assert session_state[ACTIVE_KEY] == "VISIBLE-KEY"
    assert session_state[ACTIVE_Z] == "12"
    assert session_state["wu_connected_station"] == "IWU123"
    assert session_state["wu_connected_api_key"] == "secret-key"
    assert session_state["wu_connected_z"] == "42"


def test_apply_provider_station_state_does_not_mutate_rendered_wu_input_widgets(monkeypatch):
    session_state = GuardedSessionState(
        {
            ACTIVE_STATION: "IWU123",
            ACTIVE_KEY: "secret-key",
            ACTIVE_Z: "42",
        },
        locked_keys={ACTIVE_STATION, ACTIVE_KEY, ACTIVE_Z},
    )
    _patch_provider_state(monkeypatch, session_state)

    assert provider_state.apply_station_selection(
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
    ) is True

    assert session_state[ACTIVE_STATION] == "IWU123"
    assert session_state[ACTIVE_KEY] == "secret-key"
    assert session_state[ACTIVE_Z] == "42"
    assert session_state["connection_type"] == "METEOCAT"
    assert session_state["provider_station_id"] == "Z6"


def test_disconnect_active_station_does_not_clear_rendered_input_widgets(monkeypatch):
    session_state = GuardedSessionState(
        {
            ACTIVE_STATION: "IWU123",
            ACTIVE_KEY: "secret-key",
            ACTIVE_Z: "42",
            "connected": True,
            "connection_type": "WU",
            "wu_connected_station": "IWU123",
            "wu_connected_api_key": "secret-key",
            "wu_connected_z": "42",
        },
        locked_keys={ACTIVE_STATION, ACTIVE_KEY, ACTIVE_Z},
    )
    _patch_provider_state(monkeypatch, session_state)

    provider_state.disconnect_active_station()

    assert session_state[ACTIVE_STATION] == "IWU123"
    assert session_state[ACTIVE_KEY] == "secret-key"
    assert session_state[ACTIVE_Z] == "42"
    assert session_state["connected"] is False
    assert session_state["connection_type"] is None
    assert "wu_connected_station" not in session_state


def test_disable_provider_autoconnect_does_not_mutate_rendered_toggle(monkeypatch):
    session_state = GuardedSessionState(
        {
            "auto_connect_wu_device": True,
            "provider_autoconnect_toggle_METEOCAT": True,
        },
        locked_keys={"auto_connect_wu_device", "provider_autoconnect_toggle_METEOCAT"},
    )
    _patch_provider_state(monkeypatch, session_state)
    calls = []
    monkeypatch.setattr(provider_state, "set_local_storage", lambda *args: calls.append(("ls", args)))
    monkeypatch.setattr(provider_state, "set_stored_autoconnect_target", lambda target: calls.append(("target", target)))

    provider_state.disable_provider_autoconnect("provider_autoconnect_toggle_")

    assert session_state["auto_connect_wu_device"] is True
    assert session_state["provider_autoconnect_toggle_METEOCAT"] is True
    assert session_state[AUTOCONNECT_ATTEMPTED] is False
    assert calls

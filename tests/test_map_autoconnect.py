from types import SimpleNamespace

from tabs import map as map_tab


def test_map_autoconnect_stale_true_toggle_is_synced_off(monkeypatch):
    toggle_key = "map_autoconnect_toggle_Meteocat_Z6"
    session_state = {toggle_key: True}
    monkeypatch.setattr(map_tab, "st", SimpleNamespace(session_state=session_state))

    changed = map_tab._sync_map_autoconnect_toggle(toggle_key, False)

    assert changed is False
    assert session_state[toggle_key] is False


def test_map_autoconnect_changed_toggle_is_not_overwritten(monkeypatch):
    toggle_key = "map_autoconnect_toggle_Meteocat_Z6"
    session_state = {
        toggle_key: True,
        map_tab.MAP_AUTOCONNECT_CHANGED_KEY: toggle_key,
    }
    monkeypatch.setattr(map_tab, "st", SimpleNamespace(session_state=session_state))

    changed = map_tab._sync_map_autoconnect_toggle(toggle_key, False)

    assert changed is True
    assert session_state[toggle_key] is True

    map_tab._clear_map_autoconnect_toggle_changed(toggle_key)
    assert map_tab.MAP_AUTOCONNECT_CHANGED_KEY not in session_state


def test_map_autoconnect_callback_persists_target(monkeypatch):
    toggle_key = "map_autoconnect_toggle_Meteocat_Z6"
    station = {"provider_id": "METEOCAT", "station_id": "Z6", "name": "Sasseuva"}
    session_state = {toggle_key: True}
    calls = []
    monkeypatch.setattr(map_tab, "st", SimpleNamespace(session_state=session_state))

    map_tab._handle_map_autoconnect_toggle_change(
        toggle_key,
        station,
        "Sasseuva",
        False,
        lambda selected: calls.append(selected) or True,
        lambda prefix: None,
        lambda key, **kwargs: key if not kwargs else f"{key}:{kwargs}",
    )

    assert calls == [station]
    assert session_state[map_tab.MAP_AUTOCONNECT_CHANGED_KEY] == toggle_key
    assert session_state["_map_provider_autoconnect_flash_kind"] == "success"


def test_map_autoconnect_callback_disables_current_target(monkeypatch):
    toggle_key = "map_autoconnect_toggle_Meteocat_Z6"
    session_state = {toggle_key: False}
    calls = []
    monkeypatch.setattr(map_tab, "st", SimpleNamespace(session_state=session_state))

    map_tab._handle_map_autoconnect_toggle_change(
        toggle_key,
        {"provider_id": "METEOCAT", "station_id": "Z6"},
        "Sasseuva",
        True,
        lambda selected: True,
        lambda prefix: calls.append(prefix),
        lambda key, **kwargs: key,
    )

    assert calls == ["map_autoconnect_toggle_"]
    assert session_state[map_tab.MAP_AUTOCONNECT_CHANGED_KEY] == toggle_key
    assert session_state["_map_provider_autoconnect_flash_kind"] == "info"

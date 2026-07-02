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

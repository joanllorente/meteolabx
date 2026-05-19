from types import SimpleNamespace

import pytest

from components import station_selector


class _RerunCalled(Exception):
    pass


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {
            "show_results": True,
            "search_lat": 41.39,
            "search_lon": 2.17,
        }
        self.success_messages = []
        self.info_messages = []

    def markdown(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def success(self, message):
        self.success_messages.append(message)

    def info(self, message):
        self.info_messages.append(message)

    def button(self, *args, **kwargs):
        return False

    def expander(self, *args, **kwargs):
        return _Context()

    def text_input(self, *args, **kwargs):
        return kwargs.get("value", "")

    def number_input(self, *args, **kwargs):
        return kwargs.get("value", 0.0)

    def container(self):
        return _Context()

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return tuple(_Context() for _ in range(count))

    def toggle(self, *args, **kwargs):
        key = kwargs.get("key")
        return bool(self.session_state.get(key, False))

    def metric(self, *args, **kwargs):
        return None

    def rerun(self):
        raise _RerunCalled()


def test_provider_autoconnect_selection_reruns_to_refresh_sidebar_wu_toggle(monkeypatch):
    fake_st = _FakeStreamlit()
    station = SimpleNamespace(
        provider_id="METEOCAT",
        provider_name="Meteocat",
        station_id="Z6",
        name="Sasseuva",
        elevation_m=2228.0,
        distance_km=1.2,
    )
    calls = []
    toggle_key = f"autoconnect_toggle_{station.provider_id}_{station.station_id}"
    fake_st.session_state[toggle_key] = True
    fake_st.session_state[station_selector.PROVIDER_AUTOCONNECT_CHANGED_KEY] = toggle_key

    monkeypatch.setattr(station_selector, "st", fake_st)
    monkeypatch.setattr(station_selector, "t", lambda key, **kwargs: key if not kwargs else f"{key}:{kwargs}")
    monkeypatch.setattr(station_selector, "ensure_geo_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(station_selector, "consume_browser_geolocation", lambda *args, **kwargs: None)
    monkeypatch.setattr(station_selector, "default_search_coords", lambda *args, **kwargs: (41.39, 2.17))
    monkeypatch.setattr(station_selector, "search_nearby_stations", lambda *args, **kwargs: [station])
    monkeypatch.setattr(station_selector, "get_stored_autoconnect", lambda: False)
    monkeypatch.setattr(station_selector, "get_stored_autoconnect_target", lambda: {})
    monkeypatch.setattr(
        station_selector,
        "persist_provider_autoconnect_target",
        lambda selected: calls.append(selected) or True,
    )

    with pytest.raises(_RerunCalled):
        station_selector.render_station_selector()

    assert calls == [station]
    assert fake_st.session_state["_provider_autoconnect_flash"].startswith(
        "station_selector.autoconnect_saved"
    )
    assert fake_st.session_state["_provider_autoconnect_flash_kind"] == "success"


def test_provider_autoconnect_callback_persists_target(monkeypatch):
    fake_st = _FakeStreamlit()
    station = SimpleNamespace(
        provider_id="METEOCAT",
        provider_name="Meteocat",
        station_id="Z6",
        name="Sasseuva",
        elevation_m=2228.0,
        distance_km=1.2,
    )
    calls = []
    toggle_key = f"autoconnect_toggle_{station.provider_id}_{station.station_id}"
    fake_st.session_state[toggle_key] = True

    monkeypatch.setattr(station_selector, "st", fake_st)
    monkeypatch.setattr(station_selector, "t", lambda key, **kwargs: key if not kwargs else f"{key}:{kwargs}")
    monkeypatch.setattr(
        station_selector,
        "persist_provider_autoconnect_target",
        lambda selected: calls.append(selected) or True,
    )

    station_selector._handle_provider_autoconnect_toggle_change(toggle_key, station, False)

    assert calls == [station]
    assert fake_st.session_state["_provider_autoconnect_flash_kind"] == "success"


def test_stale_provider_autoconnect_toggle_true_does_not_overwrite_wu_target(monkeypatch):
    fake_st = _FakeStreamlit()
    station = SimpleNamespace(
        provider_id="METEOCAT",
        provider_name="Meteocat",
        station_id="Z6",
        name="Sasseuva",
        elevation_m=2228.0,
        distance_km=1.2,
    )
    toggle_key = f"autoconnect_toggle_{station.provider_id}_{station.station_id}"
    fake_st.session_state[toggle_key] = True
    calls = []

    monkeypatch.setattr(station_selector, "st", fake_st)
    monkeypatch.setattr(station_selector, "t", lambda key, **kwargs: key if not kwargs else f"{key}:{kwargs}")
    monkeypatch.setattr(station_selector, "ensure_geo_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(station_selector, "consume_browser_geolocation", lambda *args, **kwargs: None)
    monkeypatch.setattr(station_selector, "default_search_coords", lambda *args, **kwargs: (41.39, 2.17))
    monkeypatch.setattr(station_selector, "search_nearby_stations", lambda *args, **kwargs: [station])
    monkeypatch.setattr(station_selector, "get_stored_autoconnect", lambda: True)
    monkeypatch.setattr(
        station_selector,
        "get_stored_autoconnect_target",
        lambda: {"kind": "WU", "station": "ILHOSP26", "api_key": "secret-key", "z": "39"},
    )
    monkeypatch.setattr(
        station_selector,
        "persist_provider_autoconnect_target",
        lambda selected: calls.append(selected) or True,
    )

    station_selector.render_station_selector()

    assert calls == []
    assert fake_st.session_state[toggle_key] is False


def test_provider_autoconnect_callback_disables_current_target(monkeypatch):
    toggle_key = "autoconnect_toggle_METEOCAT_Z6"
    fake_st = _FakeStreamlit()
    fake_st.session_state[toggle_key] = False
    calls = []
    monkeypatch.setattr(station_selector, "st", fake_st)
    monkeypatch.setattr(station_selector, "t", lambda key, **kwargs: key)
    monkeypatch.setattr(
        station_selector,
        "disable_provider_autoconnect",
        lambda prefix: calls.append(prefix),
    )

    station_selector._handle_provider_autoconnect_toggle_change(toggle_key, {}, True)

    assert calls == ["autoconnect_toggle_"]
    assert fake_st.session_state["_provider_autoconnect_flash_kind"] == "info"

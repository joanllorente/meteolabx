from utils.usage_tracking import track_usage_section_transition, usage_section_state


def _track(state, sent, tab, *, connected=False, mode="stations"):
    return track_usage_section_transition(
        state,
        active_tab=tab,
        connected=connected,
        map_view_mode=mode,
        sender=sent.append,
    )


def test_initial_automatic_ranking_is_not_counted_but_later_entry_is():
    state = {}
    sent = []

    assert _track(state, sent, "ranking") is False
    assert _track(state, sent, "ranking") is False
    assert _track(state, sent, "map", mode="stations") is True
    assert _track(state, sent, "ranking") is True

    assert sent == ["map.stations", "ranking"]


def test_station_tabs_require_connection_and_count_when_it_becomes_active():
    state = {}
    sent = []

    assert _track(state, sent, "observation", connected=False) is False
    assert _track(state, sent, "observation", connected=True) is True
    assert _track(state, sent, "observation", connected=True) is False
    assert _track(state, sent, "trends", connected=True) is True
    assert _track(state, sent, "historical", connected=False) is False
    assert _track(state, sent, "historical", connected=True) is True

    assert sent == ["observation", "trends", "historical"]


def test_each_map_view_counts_only_its_own_real_transition():
    state = {}
    sent = []

    assert _track(state, sent, "map", mode="stations") is True
    assert _track(state, sent, "map", mode="stations") is False
    assert _track(state, sent, "map", mode="temperature") is True
    assert _track(state, sent, "map", mode="wind") is True
    assert _track(state, sent, "map", mode="precipitation") is True
    assert _track(state, sent, "map", mode="stations") is True

    assert sent == [
        "map.stations",
        "map.temperature",
        "map.wind",
        "map.precipitation",
        "map.stations",
    ]


def test_unknown_map_mode_falls_back_to_station_map():
    assert usage_section_state("map", connected=False, map_view_mode="bad") == (
        "map.stations",
        True,
    )


def test_embedded_forecast_fallback_counts_as_streamlit_entry():
    state = {}
    sent = []

    assert _track(state, sent, "forecast") is True
    assert _track(state, sent, "forecast") is False
    assert sent == ["forecast.streamlit"]

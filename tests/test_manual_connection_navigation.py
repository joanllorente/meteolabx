from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_manual_connection_entry_points_request_observation_tab():
    sidebar = (ROOT / "components" / "sidebar.py").read_text(encoding="utf-8")
    selector = (ROOT / "components" / "station_selector.py").read_text(encoding="utf-8")
    favorites = (ROOT / "components" / "favorites.py").read_text(encoding="utf-8")
    map_source = (ROOT / "tabs" / "map.py").read_text(encoding="utf-8")

    assert "if manual_connection_succeeded:" in sidebar
    assert 'st.session_state[PENDING_ACTIVE_TAB] = "observation"' in sidebar
    assert 'pending_active_tab="observation"' in selector
    assert 'st.session_state[PENDING_ACTIVE_TAB] = "observation"' in favorites
    assert 'pending_active_tab="observation"' in map_source


def test_sidebar_only_navigates_after_a_successful_manual_connection():
    sidebar = (ROOT / "components" / "sidebar.py").read_text(encoding="utf-8")

    initialization = sidebar.index("manual_connection_succeeded = False")
    connect_block = sidebar.index("if connect_clicked:", initialization)
    navigation = sidebar.index("if manual_connection_succeeded:", connect_block)
    caption = sidebar.index("if connection_caption", navigation)

    assert initialization < connect_block < navigation < caption

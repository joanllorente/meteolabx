from datetime import datetime
from types import SimpleNamespace

from tabs import historical


def test_normalize_historical_summary_mode_maps_legacy_and_invalid():
    session_state = {"climo_summary_mode": "Mensual"}
    assert historical._normalize_historical_summary_mode(session_state) == "monthly"
    assert session_state["climo_summary_mode"] == "monthly"

    session_state["climo_summary_mode"] = "unsupported"
    assert historical._normalize_historical_summary_mode(session_state) == "monthly"
    assert session_state["climo_summary_mode"] == "monthly"


def test_historical_provider_support_uses_manual_notes_for_unavailable(note_recorder, translation_stub):
    supported = historical._historical_provider_is_supported("NWS", note_recorder, translation_stub)

    assert supported is False
    assert note_recorder.calls == ["historical.notes.nws_unavailable"]


def test_prepare_historical_selection_requires_month_and_year(
    patch_streamlit,
    streamlit_recorder,
    climograms_service_factory,
    note_recorder,
    translation_stub,
):
    patch_streamlit(historical)
    ok, periods, _service = historical._prepare_historical_selection(
        provider_id="AEMET",
        summary_mode="monthly",
        selected_months=[],
        selected_years=[],
        frost_selected_period="",
        frost_selected_periods=[],
        frost_period_options={"monthly": [], "annual": []},
        get_climograms_service=climograms_service_factory,
        render_neutral_info_note=note_recorder,
        t=translation_stub,
    )

    assert ok is False
    assert periods == []
    assert streamlit_recorder.info_messages == ["historical.info.select_month_and_year"]


def test_prepare_historical_selection_builds_periods_and_caption(
    patch_streamlit,
    streamlit_recorder,
    climograms_service_factory,
    note_recorder,
    translation_stub,
):
    patch_streamlit(historical)
    periods = [
        SimpleNamespace(start=datetime(2025, 1, 1), end=datetime(2025, 1, 31)),
        SimpleNamespace(start=datetime(2025, 2, 1), end=datetime(2025, 2, 28)),
    ]
    service = climograms_service_factory(periods=periods, description="2025-01..2025-02")
    ok, built_periods, returned_service = historical._prepare_historical_selection(
        provider_id="AEMET",
        summary_mode="monthly",
        selected_months=[1, 2],
        selected_years=[2025],
        frost_selected_period="",
        frost_selected_periods=[],
        frost_period_options={"monthly": [], "annual": []},
        get_climograms_service=lambda: service,
        render_neutral_info_note=note_recorder,
        t=translation_stub,
    )

    assert ok is True
    assert built_periods == periods
    assert returned_service is service
    assert streamlit_recorder.caption_messages
    assert streamlit_recorder.caption_messages[0].startswith("historical.caption.period_summary")


def test_historical_chart_scope_for_frost_yearly_uses_climate_period_labels(translation_stub):
    scope = historical._historical_chart_scope("FROST", "yearly", "annual", translation_stub)
    assert scope == (
        "historical.chart.x.climate_period",
        "historical.chart.scope.climate_periods",
        "historical.table.scope.climate_period",
        "historical.table.period_col.climate_period",
    )

from datetime import date, datetime
import inspect
from types import SimpleNamespace

import pandas as pd

from tabs import historical
from services import climograms


def test_normalize_historical_summary_mode_maps_legacy_and_invalid():
    session_state = {"climo_summary_mode": "Mensual"}
    assert historical._normalize_historical_summary_mode(session_state) == "monthly"
    assert session_state["climo_summary_mode"] == "monthly"

    session_state["climo_summary_mode"] = "unsupported"
    assert historical._normalize_historical_summary_mode(session_state) == "monthly"
    assert session_state["climo_summary_mode"] == "monthly"


def test_weatherlink_historical_summary_mode_is_monthly_only():
    session_state = {"climo_summary_mode": "annual"}

    assert historical._summary_mode_options("WEATHERLINK") == ["monthly"]
    assert historical._normalize_historical_summary_mode(session_state, "WEATHERLINK") == "monthly"
    assert session_state["climo_summary_mode"] == "monthly"


def test_year_options_default_keeps_recent_window():
    options = historical._year_options(datetime(2026, 7, 9))

    assert options[0] == 2026
    assert options[-1] == 1991


def test_provider_year_options_aemet_reaches_1950():
    options = historical._provider_year_options("AEMET", datetime(2026, 7, 9))

    assert options[0] == 2026
    assert options[-1] == 1950
    assert 1950 in options


def test_provider_year_options_non_aemet_keeps_recent_window():
    options = historical._provider_year_options("WU", datetime(2026, 7, 9))

    assert options[0] == 2026
    assert options[-1] == 1991


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


def test_prepare_historical_selection_clips_wu_periods_to_current_day(
    patch_streamlit,
    streamlit_recorder,
    translation_stub,
    note_recorder,
):
    patch_streamlit(historical)

    class _Service:
        def build_period_specs(self, summary_mode, selected_years, selected_months):
            return [
                climograms.ClimogramPeriod(
                    label="2026",
                    start=date(2026, 1, 1),
                    end=date(2026, 12, 31),
                )
            ]

        def clip_periods_to_today(self, periods):
            return climograms.clip_periods_to_today(periods, today_date=date(2026, 6, 6))

        def describe_period_range(self, periods):
            return climograms.describe_period_range(periods)

    ok, periods, _service = historical._prepare_historical_selection(
        provider_id="WU",
        summary_mode="annual",
        selected_months=[],
        selected_years=[2026],
        frost_selected_period="",
        frost_selected_periods=[],
        frost_period_options={},
        get_climograms_service=lambda: _Service(),
        render_neutral_info_note=note_recorder,
        t=translation_stub,
    )

    assert ok is True
    assert periods == [
        climograms.ClimogramPeriod(
            label="2026",
            start=date(2026, 1, 1),
            end=date(2026, 6, 6),
        )
    ]
    assert "01/01/2026 \u2192 06/06/2026" in streamlit_recorder.caption_messages[0]


def test_historical_chart_scope_for_frost_yearly_uses_climate_period_labels(translation_stub):
    scope = historical._historical_chart_scope("FROST", "yearly", "annual", translation_stub)
    assert scope == (
        "historical.chart.x.climate_period",
        "historical.chart.scope.climate_periods",
        "historical.table.scope.climate_period",
        "historical.table.period_col.climate_period",
    )


def test_historical_tab_does_not_render_wu_wind_rose_for_now():
    source = inspect.getsource(historical.render_historical_tab)

    assert "_render_historical_wu_wind_rose(" not in source


def test_historical_wu_wind_rose_stats_bins_daily_rows():
    daily_df = pd.DataFrame(
        {
            "wind_mean": [5.0, 4.0, 0.2, 6.0],
            "wind_dir_mean": [0.0, 44.0, 200.0, 270.0],
        }
    )

    stats = historical._wind_rose_stats_from_daily(daily_df)

    assert stats["total_samples"] == 4
    assert stats["valid_direction"] == 4
    assert stats["calm"] == 1
    assert stats["dir_total"] == 3
    assert stats["counts"]["N"] == 1
    assert stats["counts"]["NE"] == 1
    assert stats["counts"]["W"] == 1
    assert stats["counts"]["S"] == 0
    assert stats["dir_pcts"]["N"] == 100.0 / 3.0


def test_historical_wind_rose_treats_under_two_kmh_as_calm():
    daily_df = pd.DataFrame(
        {
            "wind_mean": [1.5, 2.1],
            "wind_dir_mean": [0.0, 90.0],
        }
    )

    stats = historical._wind_rose_stats_from_daily(daily_df)

    assert stats["calm"] == 1
    assert stats["dir_total"] == 1
    assert stats["counts"]["N"] == 0
    assert stats["counts"]["E"] == 1

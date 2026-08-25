from datetime import date, datetime
import ast
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


def test_historical_dataset_fetch_is_guarded_by_query_button():
    source = inspect.getsource(historical.render_historical_tab)
    tree = ast.parse(source)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    fetch_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_fetch_historical_dataset"
    ]
    assert len(fetch_calls) == 1

    node = fetch_calls[0]
    guarded_by_query = False
    while node in parents:
        node = parents[node]
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "query_requested"
        ):
            guarded_by_query = True
            break
    assert guarded_by_query is True


def test_historical_query_signature_changes_with_selection():
    base = historical._historical_query_signature(
        provider_id="WU",
        station_id="TEST",
        summary_mode="monthly",
        selected_months=[8],
        selected_years=[2026],
        frost_selected_period="",
        frost_selected_periods=[],
    )
    changed = historical._historical_query_signature(
        provider_id="WU",
        station_id="TEST",
        summary_mode="monthly",
        selected_months=[7],
        selected_years=[2026],
        frost_selected_period="",
        frost_selected_periods=[],
    )

    assert base != changed


def test_historical_result_stays_visible_while_selector_draft_changes():
    queried_signature = historical._historical_query_signature(
        provider_id="WU",
        station_id="TEST",
        summary_mode="monthly",
        selected_months=[7],
        selected_years=[2026],
        frost_selected_period="",
        frost_selected_periods=[],
    )
    draft_signature = historical._historical_query_signature(
        provider_id="WU",
        station_id="TEST",
        summary_mode="monthly",
        selected_months=[7, 8],
        selected_years=[2026],
        frost_selected_period="",
        frost_selected_periods=[],
    )
    result = {"signature": queried_signature, "summary_mode": "monthly"}

    assert queried_signature != draft_signature
    assert historical._historical_result_matches_connection(
        result,
        provider_id="WU",
        station_id="TEST",
    )
    assert historical._historical_result_summary_mode(result, "annual") == "monthly"


def test_historical_result_is_not_reused_for_another_connection():
    result = {
        "signature": historical._historical_query_signature(
            provider_id="WU",
            station_id="FIRST",
            summary_mode="monthly",
            selected_months=[8],
            selected_years=[2026],
            frost_selected_period="",
            frost_selected_periods=[],
        )
    }

    assert not historical._historical_result_matches_connection(
        result,
        provider_id="WU",
        station_id="SECOND",
    )


def test_historical_render_routes_raw_overrides_only_to_card_builder():
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(historical.render_historical_tab)))
    build_table_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_extremes_table"
    )
    build_cards_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_historical_extreme_cards"
    )

    assert "extremes_overrides" not in {keyword.arg for keyword in build_table_call.keywords}
    assert "extremes_overrides" in {keyword.arg for keyword in build_cards_call.keywords}


def test_historical_extreme_cards_combine_absolute_values(monkeypatch):
    calls = []

    def fake_card(title, value, unit="", **kwargs):
        payload = {"title": title, "value": value, "unit": unit, **kwargs}
        calls.append(payload)
        return payload

    def fake_dual_value_card(title, **kwargs):
        payload = {"title": title, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "card", fake_card)
    monkeypatch.setattr(historical, "dual_value_card", fake_dual_value_card)
    rows = pd.DataFrame(
        [
            ["historical.metrics.absolute_max", "36.6 °C", "14/08/2026"],
            ["historical.metrics.absolute_min", "23.3 °C", "05/08/2026"],
            ["historical.metrics.lowest_maximum", "25.4 °C", "05/08/2026"],
            ["historical.metrics.highest_minimum", "27.7 °C", "10/08/2026"],
            ["historical.metrics.windiest_day", "12.8 km/h", "14/08/2026"],
            ["historical.metrics.max_gust", "54.2 km/h", "14/08/2026"],
            ["historical.metrics.rainiest_day", "18.4 mm", "01/08/2026"],
            ["historical.metrics.tropical_nights", "19 noches", "—"],
            ["historical.metrics.torrid_nights", "11 noches", "—"],
        ],
        columns=["Metric", "Value", "Date"],
    )
    daily = pd.DataFrame(
        {
            "wind_mean": [5.0, 12.8, 4.0],
            "wind_dir_mean": [270.0, 280.0, 90.0],
            "gust_max": [20.0, 54.2, 30.0],
            "gust_dir_max": [0.0, 225.0, 90.0],
        }
    )

    cards = historical._historical_extreme_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        daily_df=daily,
    )

    assert len(cards) == 6
    assert calls[0]["title"] == "historical.cards.thermal_extremes"
    assert calls[0]["primary_value"] == "36.6"
    assert calls[0]["secondary_value"] == "23.3"
    assert calls[0]["footer_value"] == "13.3 °C"
    assert calls[1]["icon_kind"] == "temp_cold"
    assert calls[2]["icon_kind"] == "temp_night"
    assert [call["title"] for call in calls[3:]] == [
        "historical.metrics.max_gust",
        "historical.metrics.windiest_day",
        "historical.metrics.rainiest_day",
    ]
    assert calls[3]["primary_unit"] == "km/h"
    assert calls[3]["secondary_value"] == "SW"
    assert calls[3]["secondary_date"] == "225°"
    assert calls[4]["primary_unit"] == "km/h"
    assert calls[4]["secondary_value"] == "W"
    assert calls[4]["secondary_date"] == "280°"
    assert all("nights" not in call["title"] for call in calls)


def test_historical_wind_direction_suffix_distinguishes_missing_direction_and_sensor():
    assert historical._historical_unit_with_direction("54.2", "km/h", "-") == "km/h · -"
    assert historical._historical_unit_with_direction("-", "", "-") == ""
    assert historical._historical_direction_parts(247.5) == ("WSW", "247.5°")


def test_historical_multi_month_combines_wind_day_and_month_in_matrix(monkeypatch):
    calls = []

    def fake_wind_extremes_card(title, **kwargs):
        payload = {"title": title, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "wind_extremes_card", fake_wind_extremes_card)
    rows = pd.DataFrame(
        [
            ["historical.metrics.windiest_day", "12.0 km/h", "15/09/2026"],
            ["historical.metrics.windiest_month", "10.0 km/h", "09/2026"],
        ],
        columns=["Metric", "Value", "Date"],
    )
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-10", "2026-08-11", "2026-09-14", "2026-09-15"]),
            "wind_mean": [4.0, 5.0, 8.0, 12.0],
            "wind_dir_mean": [90.0, 100.0, 260.0, 270.0],
        }
    )

    cards = historical._historical_extreme_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        daily_df=daily,
        summary_mode="monthly",
        period_count=2,
        extremes_overrides={
            "Día más ventoso (viento medio)": {"Dirección": "135.0"},
        },
    )

    assert cards == calls
    assert calls[0]["title"] == "historical.cards.wind_extremes"
    assert calls[0]["day_label"] == "historical.cards.summary_labels.windiest_day"
    assert calls[0]["month_label"] == "historical.cards.summary_labels.windiest_month"
    assert calls[0]["day_value"] == "12.0"
    assert calls[0]["day_direction"] == "SE"
    assert calls[0]["day_degrees"] == "135°"
    assert calls[0]["month_value"] == "10.0"
    assert calls[0]["month_direction"] == "W"
    assert calls[0]["month_degrees"] == "265°"
    assert calls[0]["month_date"] == "Septiembre 2026"
    assert calls[0]["direction_label"] == "historical.cards.summary_labels.predominant_direction"


def test_historical_rain_card_adds_max_intensity_and_distinct_date(monkeypatch):
    calls = []

    def fake_card(title, value, unit="", **kwargs):
        payload = {"title": title, "value": value, "unit": unit, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "card", fake_card)
    rows = pd.DataFrame(
        [["historical.metrics.rainiest_day", "18.4 mm", "01/08/2026"]],
        columns=["Metric", "Value", "Date"],
    )
    daily = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-08-01")],
            "precip_rate_max": [24.6],
            "precip_rate_max_date": ["2026-08-03"],
        }
    )

    historical._historical_extreme_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        daily_df=daily,
        unit_preferences={"precip": "mm"},
    )

    assert "historical.cards.max_intensity" in calls[0]["subtitle_html"]
    assert "24.6 mm/h" in calls[0]["subtitle_html"]
    assert "03/08/2026" in calls[0]["subtitle_html"]


def test_annual_cards_pair_temperature_precipitation_and_sunshine(monkeypatch):
    calls = []

    def fake_card(title, value, unit="", **kwargs):
        payload = {"kind": "single", "title": title, "value": value, "unit": unit, **kwargs}
        calls.append(payload)
        return payload

    def fake_dual_value_card(title, **kwargs):
        payload = {"kind": "dual", "title": title, **kwargs}
        calls.append(payload)
        return payload

    def fake_wind_extremes_card(title, **kwargs):
        payload = {"kind": "wind-matrix", "title": title, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "card", fake_card)
    monkeypatch.setattr(historical, "dual_value_card", fake_dual_value_card)
    monkeypatch.setattr(historical, "wind_extremes_card", fake_wind_extremes_card)
    rows = pd.DataFrame(
        [
            ["historical.metrics.absolute_max", "39.0 °C", "12/07/2025"],
            ["historical.metrics.absolute_min", "-4.0 °C", "08/01/2024"],
            ["historical.metrics.warmest_year", "18.2 °C", "2025"],
            ["historical.metrics.coldest_year", "15.1 °C", "2024"],
            ["historical.metrics.max_gust", "92.0 km/h", "03/11/2024"],
            ["historical.metrics.windiest_year", "13.0 km/h", "2024"],
            ["historical.metrics.wettest_year", "810.0 mm", "2024"],
            ["historical.metrics.driest_year", "420.0 mm", "2025"],
            ["historical.metrics.sunniest_year", "2650.0 h", "2025"],
            ["historical.metrics.least_sunny_year", "2310.0 h", "2024"],
            ["historical.metrics.lowest_maximum", "3.0 °C", "08/01/2024"],
            ["historical.metrics.highest_minimum", "27.0 °C", "10/08/2025"],
        ],
        columns=["Metric", "Value", "Date"],
    )
    yearly = pd.DataFrame(
        {
            "wind_mean": [13.0, 8.0],
            "wind_dir_mean": [270.0, 270.0],
            "gust_max": [92.0, 65.0],
            "gust_dir_max": [247.5, 180.0],
        }
    )

    cards = historical._historical_extreme_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        summary_mode="annual",
        period_count=2,
        daily_df=yearly,
    )

    assert len(cards) == 6
    assert [item["title"] for item in cards] == [
        "historical.cards.thermal_extremes",
        "historical.cards.mean_temp_extremes",
        "historical.cards.daily_temperature_extremes",
        "historical.cards.wind_extremes",
        "historical.cards.precip_extremes",
        "historical.cards.solar_extremes",
    ]
    assert cards[1]["primary_date"] == "2025"
    assert cards[1]["secondary_date"] == "2024"
    assert cards[2]["primary_value"] == "27.0"
    assert cards[2]["secondary_value"] == "3.0"
    assert cards[2]["primary_label"] == "historical.metrics.highest_minimum"
    assert cards[2]["secondary_label"] == "historical.metrics.lowest_maximum"
    assert cards[3]["day_value"] == "92.0"
    assert cards[3]["month_value"] == "13.0"
    assert cards[3]["day_unit"] == "km/h"
    assert cards[3]["day_direction"] == "WSW"
    assert cards[3]["day_degrees"] == "247.5°"
    assert cards[3]["month_unit"] == "km/h"
    assert cards[3]["month_direction"] == "W"
    assert cards[3]["month_degrees"] == "270°"
    assert cards[3]["direction_label"] == "historical.cards.summary_labels.predominant_direction"
    assert cards[4]["primary_value"] == "810.0"
    assert cards[4]["secondary_value"] == "420.0"


def test_annual_cards_keep_missing_temperature_wind_and_solar_slots(monkeypatch):
    calls = []

    def fake_dual_value_card(title, **kwargs):
        payload = {"title": title, **kwargs}
        calls.append(payload)
        return payload

    def fake_wind_extremes_card(title, **kwargs):
        payload = {"title": title, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "dual_value_card", fake_dual_value_card)
    monkeypatch.setattr(historical, "wind_extremes_card", fake_wind_extremes_card)
    rows = pd.DataFrame(
        [
            ["historical.metrics.absolute_max", "31.0 °C", "2025"],
            ["historical.metrics.absolute_min", "4.0 °C", "2024"],
            ["historical.metrics.warmest_year", "18.0 °C", "2025"],
            ["historical.metrics.coldest_year", "16.0 °C", "2024"],
            ["historical.metrics.max_gust", "— km/h", "—"],
            ["historical.metrics.windiest_year", "— km/h", "—"],
            ["historical.metrics.sunniest_year", "— h", "—"],
            ["historical.metrics.least_sunny_year", "— h", "—"],
        ],
        columns=["Metric", "Value", "Date"],
    )

    cards = historical._historical_extreme_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        summary_mode="annual",
        period_count=2,
    )

    assert len(cards) == 6
    assert cards[2]["primary_value"] == cards[2]["secondary_value"] == "-"
    assert cards[3]["day_value"] == cards[3]["month_value"] == "-"
    assert cards[3]["day_unit"] == cards[3]["month_unit"] == ""
    assert cards[5]["primary_value"] == cards[5]["secondary_value"] == "-"


def test_historical_summary_metrics_are_grouped_into_five_cards(monkeypatch):
    calls = []

    def fake_metric_group_card(title, metrics, **kwargs):
        payload = {"title": title, "metrics": metrics, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "metric_group_card", fake_metric_group_card)
    rows = pd.DataFrame(
        [
            ["historical.metrics.mean_temperature", "18.4 °C"],
            ["historical.metrics.mean_maximums", "24.1 °C"],
            ["historical.metrics.mean_minimums", "12.7 °C"],
            ["historical.metrics.temperature_stddev", "3.2 °C"],
            ["historical.metrics.mean_wind", "7.9 km/h"],
            ["historical.metrics.accumulated_precipitation", "94.5 mm"],
            ["historical.metrics.mean_sunshine_hours", "8.1 h"],
            ["historical.metrics.tropical_nights", "4 noches"],
            ["historical.metrics.torrid_nights", "1 noches"],
        ],
        columns=["Metric", "Value"],
    )

    cards = historical._historical_summary_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        daily_df=pd.DataFrame(
            {
                "wind_mean": [6.0, 8.0, 7.0],
                "wind_dir_mean": [180.0, 190.0, 20.0],
            }
        ),
    )

    assert len(cards) == 5
    assert [card["title"] for card in cards] == [
        "historical.cards.average_temperatures",
        "historical.cards.wind_summary",
        "historical.cards.rain_summary",
        "historical.cards.solar_summary",
        "historical.cards.characteristic_days",
    ]
    assert cards[0]["metrics"] == [
        ("historical.cards.summary_labels.mean", "18.4", "°C"),
        ("historical.cards.summary_labels.maximums", "24.1", "°C"),
        ("historical.cards.summary_labels.minimums", "12.7", "°C"),
        ("historical.cards.summary_labels.stddev", "3.2", "°C"),
    ]
    assert cards[1]["metrics"][0][1:] == ("7.9", "km/h")
    assert cards[1]["metrics"][1] == (
        "historical.cards.summary_labels.predominant_direction",
        "S",
        "185°",
    )
    assert cards[1]["equal_columns"] is True
    assert cards[1]["stack_last_unit"] is True
    assert cards[2]["metrics"][1][1:] == ("-", "")
    assert cards[3]["metrics"][0][1:] == ("8.1", "h")
    assert cards[4]["metrics"][2][1:] == ("-", "")


def test_historical_summary_keeps_solar_slot_without_sensor(monkeypatch):
    calls = []

    def fake_metric_group_card(title, metrics, **kwargs):
        payload = {"title": title, "metrics": metrics, **kwargs}
        calls.append(payload)
        return payload

    monkeypatch.setattr(historical, "metric_group_card", fake_metric_group_card)
    rows = pd.DataFrame(
        [["historical.metrics.mean_temperature", "17.0 °C"]],
        columns=["Metric", "Value"],
    )

    cards = historical._historical_summary_cards(
        rows,
        t=lambda key, **_kwargs: key,
        dark=False,
        solar_metric_kind="irradiation",
    )

    assert len(cards) == 5
    assert cards[1]["metrics"][0][1:] == ("-", "")
    assert cards[1]["metrics"][1][1:] == ("-", "")
    assert cards[3]["metrics"] == [
        ("historical.cards.summary_labels.irradiation_mean", "-", "")
    ]


def test_wu_daily_rows_are_aggregated_for_multi_year_annual_extremes():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-10", "2024-07-10", "2025-01-10", "2025-07-10"]),
            "temp_mean": [10.0, 12.0, 20.0, 22.0],
            "temp_max": [14.0, 28.0, 24.0, 39.0],
            "temp_min": [2.0, 8.0, 5.0, 12.0],
            "wind_mean": [5.0, 7.0, 8.0, 10.0],
            "gust_max": [30.0, 40.0, 50.0, 60.0],
            "precip_total": [100.0, 200.0, 80.0, 120.0],
        }
    )

    table = climograms.build_extremes_table(
        daily,
        summary_mode="annual",
        period_count=2,
    )

    # Filas 2/3 del contrato anual: año más cálido y año más frío.
    assert table.iloc[2, 1] == "21.0 °C"
    assert table.iloc[2, 2] == "2025"
    assert table.iloc[3, 1] == "11.0 °C"
    assert table.iloc[3, 2] == "2024"
    assert table.iloc[6, 1] == "300.0 mm"
    assert table.iloc[6, 2] == "2024"


def test_annual_daily_rows_keep_lowest_maximum_and_highest_minimum_when_requested():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-10", "2024-07-10", "2025-02-10", "2025-08-10"]),
            "temp_mean": [5.0, 20.0, 7.0, 24.0],
            "temp_max": [4.0, 29.0, 2.0, 33.0],
            "temp_min": [-2.0, 22.0, -1.0, 25.0],
            "wind_mean": [5.0, 5.0, 5.0, 5.0],
            "precip_total": [0.0, 0.0, 0.0, 0.0],
        }
    )

    table = climograms.build_extremes_table(
        daily,
        summary_mode="annual",
        period_count=2,
        include_daily_temperature_extremes=True,
    )

    assert list(table.iloc[-2:, 0]) == [
        "Mínima de máximas",
        "Máxima de mínimas",
    ]
    assert table.iloc[-2, 1] == "2.0 °C"
    assert table.iloc[-2, 2] == "10/02/2025"
    assert table.iloc[-1, 1] == "25.0 °C"
    assert table.iloc[-1, 2] == "10/08/2025"


def test_meteocat_annual_solar_values_are_irradiation_not_sunshine_hours():
    yearly = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2025-01-01"]),
            "solar_mean": [15.2, 19.2],
            "temp_abs_max": [30.0, 31.0],
        }
    )

    extremes = climograms.build_extremes_table(
        yearly,
        summary_mode="annual",
        period_count=2,
        solar_metric_kind="irradiation",
    )
    metrics = dict(zip(extremes.iloc[:, 0], extremes.iloc[:, 1]))
    summary = climograms.build_general_metrics_table(
        yearly.assign(
            temp_mean=20.0,
            temp_max=25.0,
            temp_min=15.0,
            wind_mean=5.0,
            precip_total=100.0,
        ),
        solar_metric_kind="irradiation",
    )

    assert metrics["Año con mayor irradiación solar media"] == "19.2 MJ/m²"
    assert metrics["Año con menor irradiación solar media"] == "15.2 MJ/m²"
    assert summary.iloc[-1, 0] == "Irradiación solar global diaria media"
    assert summary.iloc[-1, 1] == "17.2 MJ/m²"


def test_general_metrics_calculates_same_year_monthly_rain_mean_and_rain_days():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-02-01", "2026-02-02"]),
            "temp_mean": [10.0, 11.0, 12.0, 13.0],
            "temp_max": [15.0, 16.0, 17.0, 18.0],
            "temp_min": [5.0, 6.0, 7.0, 8.0],
            "wind_mean": [3.0, 4.0, 5.0, 6.0],
            "precip_total": [0.0, 0.2, 1.0, 0.1],
        }
    )

    table = climograms.build_general_metrics_table(daily)
    metrics = dict(zip(table.iloc[:, 0], table.iloc[:, 1]))

    assert metrics["Media de precipitación"] == "0.7 mm"
    assert metrics["Días de lluvia"] == "2 días"


def test_multi_month_daily_history_also_derives_windiest_month_without_calls():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-10", "2026-08-11", "2026-09-10", "2026-09-11"]),
            "temp_mean": [20.0, 21.0, 22.0, 23.0],
            "temp_max": [25.0, 26.0, 27.0, 28.0],
            "temp_min": [15.0, 16.0, 17.0, 18.0],
            "wind_mean": [4.0, 6.0, 8.0, 12.0],
            "gust_max": [10.0, 12.0, 14.0, 16.0],
            "precip_total": [0.0, 0.0, 1.0, 0.0],
        }
    )

    table = climograms.build_extremes_table(
        daily,
        summary_mode="monthly",
        period_count=2,
    )
    rows = {row.iloc[0]: (row.iloc[1], row.iloc[2]) for _, row in table.iterrows()}

    assert rows["Mes más ventoso (viento medio)"] == ("10.0 km/h", "01/09/2026")


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

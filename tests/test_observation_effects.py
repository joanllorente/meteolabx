from pathlib import Path
from types import SimpleNamespace

from frontend.observation_effects import (
    ObservationEffectHandlers,
    apply_observation_effects,
)
from domain.observation_pipeline import ProcessingContext, prepare_observation_effects


def _handlers(events):
    def store_series(state, prefix, series):
        events.append(("store", prefix, series))
        state[f"{prefix}_stored"] = series

    return ObservationEffectHandlers(
        render_warning=lambda warning: warning.get("message", ""),
        emit_warning=lambda message: events.append(("emit", message)),
        log_warning=lambda message: events.append(("log", message)),
        store_series=store_series,
        set_chart_owner=lambda *owner: events.append(("chart_owner", *owner)),
        set_trend_hourly_owner=lambda *owner: events.append(("trend_owner", *owner)),
        clear_trend_hourly_owner=lambda: events.append(("trend_owner_clear",)),
    )


def test_applies_all_client_owned_effects_without_streamlit():
    state = {"existing": True}
    events = []
    result = SimpleNamespace(
        session_updates={"station_lat": 41.2, "station_lon": 2.1},
        warnings=[{"message": "old data"}, {"message": ""}],
        chart_series={"epochs": [1]},
        chart_series_owner=("METEOCAT", "X1"),
        trend_hourly_series={"epochs": [2]},
        trend_hourly_owner_action="set",
        trend_hourly_owner=("METEOCAT", "X1"),
    )

    apply_observation_effects(result, state, _handlers(events))

    assert state["existing"] is True
    assert state["station_lat"] == 41.2
    assert state["station_lon"] == 2.1
    assert state["chart_stored"] == {"epochs": [1]}
    assert state["trend_hourly_stored"] == {"epochs": [2]}
    assert events == [
        ("emit", "old data"),
        ("log", "old data"),
        ("store", "chart", {"epochs": [1]}),
        ("chart_owner", "METEOCAT", "X1"),
        ("store", "trend_hourly", {"epochs": [2]}),
        ("trend_owner", "METEOCAT", "X1"),
    ]


def test_clears_hourly_owner_without_storing_missing_hourly_series():
    state = {}
    events = []
    result = SimpleNamespace(
        session_updates={},
        warnings=[],
        chart_series={"epochs": []},
        chart_series_owner=None,
        trend_hourly_series=None,
        trend_hourly_owner_action="clear",
        trend_hourly_owner=None,
    )

    apply_observation_effects(result, state, _handlers(events))

    assert events == [
        ("store", "chart", {"epochs": []}),
        ("trend_owner_clear",),
    ]
    assert "trend_hourly_stored" not in state


def test_effect_layer_does_not_import_streamlit_or_calculation_modules():
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "observation_effects.py"
    ).read_text(encoding="utf-8")

    assert "import streamlit" not in source
    assert "domain.observation_pipeline" not in source
    assert "models." not in source


def test_effect_plan_does_not_calculate_local_derivatives():
    base = {
        "Tc": 22.0,
        "RH": 60.0,
        "epoch": 1_718_000_000,
        "elevation": 100.0,
    }

    plan = prepare_observation_effects(
        base,
        ProcessingContext(
            provider_name="METEOCAT",
            owner_station_id="X1",
            series_override={"epochs": [1_718_000_000], "has_data": True},
        ),
        now_epoch=1_718_000_060,
    )

    assert "Td" not in base
    assert "feels_like" not in base
    assert "heat_index" not in base
    assert not hasattr(plan, "processed")
    assert plan.chart_series_owner == ("METEOCAT", "X1")

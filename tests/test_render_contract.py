"""Contract tests for the authoritative backend payload used by the render."""

import ast
from pathlib import Path

from frontend.dashboard_payload import build_dashboard_payload


def test_render_keeps_observation_and_derivatives_separate():
    response = {
        "observation": {"Tc": 22.0, "Td": 14.0, "feels_like": 23.1},
        "derivatives": {"p_abs": 1002.3, "theta": 295.0},
        "daily_extremes": {"temp_max": 24.0},
    }

    dashboard = build_dashboard_payload(response)

    assert dashboard.observation == response["observation"]
    assert dashboard.derivatives == response["derivatives"]
    assert dashboard.daily_extremes == response["daily_extremes"]
    assert "p_abs_hpa" not in dashboard.observation
    assert "temp_max" not in dashboard.observation


def test_standard_provider_path_uses_canonical_dashboard_blocks():
    source = (Path(__file__).resolve().parents[1] / "meteolabx.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "process_standard_provider"
    )
    attributes = {
        node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
    }

    assert {"observation", "derivatives", "series", "recent_series"} <= attributes
    assert "processed_from_backend" not in source
    assert "ProcessedObservation" not in source


def test_frontend_has_no_local_meteorological_fallbacks():
    source = (Path(__file__).resolve().parents[1] / "meteolabx.py").read_text(
        encoding="utf-8"
    )
    for symbol in (
        "rain_rates_from_total",
        "pressure_trend_3h",
        "penman_monteith_et0",
        "apparent_temperature",
        "heat_index_rothfusz",
        "dewpoint_from_vapor_pressure",
        "_accumulate_et0_from_series",
        "chart_et0",
        "chart_balance",
    ):
        assert symbol not in source


def test_observation_tab_consumes_nested_blocks():
    source = (Path(__file__).resolve().parents[1] / "tabs" / "observation.py").read_text(
        encoding="utf-8"
    )
    assert "ctx.observation" in source
    assert "ctx.derivatives" in source
    assert "ctx.daily_extremes" in source
    assert "ctx.station" in source
    assert "ctx.base" not in source

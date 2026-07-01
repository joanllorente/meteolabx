import ast
from pathlib import Path

from frontend.dashboard_payload import build_dashboard_payload


def _processed_response():
    return {
        "observation": {
            "epoch": 1_718_000_000,
            "Tc": 21.5,
            "RH": 58.0,
            "p_hpa": 1013.2,
            "wind": 9.0,
            "gust": 18.0,
            "wind_dir_deg": 240.0,
            "elevation": 100.0,
        },
        "derivatives": {"p_abs": 1000.6, "Tw": 15.2},
        "daily_extremes": {"temp_max": 24.0, "temp_min": 12.0},
        "station": {
            "name": "Canonical station",
            "tz": "Europe/Madrid",
            "elevation": 100.0,
            "sensors": {"thermometer": True},
        },
        "series": {
            "epochs": [1_718_000_000],
            "temps": [21.5],
            "pressures_abs": [1000.6],
            "has_data": True,
        },
    }


def test_preserves_canonical_dashboard_blocks_without_flattening():
    dashboard = build_dashboard_payload(_processed_response())

    assert dashboard.observation["Tc"] == 21.5
    assert dashboard.derivatives["p_abs"] == 1000.6
    assert dashboard.daily_extremes["temp_max"] == 24.0
    assert dashboard.station["tz"] == "Europe/Madrid"
    assert dashboard.series["pressures_abs"] == [1000.6]
    for alias in ("station_code", "station_name", "station_tz", "p_abs_hpa", "_series"):
        assert alias not in dashboard.observation


def test_recent_canonical_pressure_is_preserved_for_render():
    dashboard = build_dashboard_payload(
        _processed_response(),
        recent_series={
            "epochs": [1],
            "temps": [20.0],
            "humidities": [60.0],
            "pressures": [1013.0],
            "pressures_abs": [1000.4],
            "has_data": True,
        },
    )

    assert dashboard.recent_series["pressures_abs"][0] == 1000.4


def test_standard_render_path_does_not_call_provider_legacy_adapters():
    source = (Path(__file__).resolve().parents[1] / "meteolabx.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_process_standard_provider_connection"
    )
    attributes = {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
    }
    called_names = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not any(name.startswith("get_") and name.endswith("_data") for name in attributes)
    assert "fetch_provider_current_processed_via_api" in called_names

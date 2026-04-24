import math

from services import meteocat


def test_select_metric_candidate_code_picks_first_with_data():
    selected = meteocat._select_metric_candidate_code(
        [100, 200, 300],
        lambda code: code == 200,
    )
    assert selected == 200


def test_apply_and_finalize_climo_rows_normalizes_values():
    rows = meteocat._build_climo_rows(["2025-01-01"])
    meteocat._apply_climo_metric_value(
        rows["2025-01-01"],
        "wind_mean",
        {"value": 5.0, "date": "2025-01-01"},
    )
    rows["2025-01-01"]["temp_max"] = 20.0
    rows["2025-01-01"]["temp_min"] = 10.0
    rows["2025-01-01"]["precip_total"] = -3.0

    frame = meteocat._finalize_climo_rows(rows)
    row = frame.iloc[0]

    assert math.isclose(row["wind_mean"], 18.0, rel_tol=1e-6)
    assert math.isclose(row["temp_mean"], 15.0, rel_tol=1e-6)
    assert row["precip_total"] == 0.0


def test_precip_window_prefers_interval_precipitation_over_accumulated_counter():
    var_map = {
        meteocat.V_PRECIP_ACC: [
            (1, 1308.8),
            (2, 1308.7),
            (3, 1308.7),
            (4, 1308.8),
            (5, 1308.9),
            (6, 1308.8),
            (7, 1308.3),
        ],
        meteocat.V_PRECIP: [
            (1, 0.0),
            (2, 0.0),
            (3, 0.0),
            (4, 0.0),
            (5, 0.0),
            (6, 0.0),
            (7, 0.0),
        ],
    }

    assert math.isclose(meteocat._precip_window_mm(var_map), 0.0, abs_tol=1e-6)


def test_precip_window_ignores_accumulated_counter_without_interval_precipitation():
    var_map = {
        meteocat.V_PRECIP_ACC: [
            (1, 1308.8),
            (2, 1309.0),
            (3, 0.4),
            (4, 0.9),
        ],
    }

    assert math.isnan(meteocat._precip_window_mm(var_map))

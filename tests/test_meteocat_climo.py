import math

from domain.parsing import meteocat_climo as climo_parsing


def test_select_metric_candidate_code_picks_first_with_data():
    selected = climo_parsing.select_metric_candidate_code(
        [100, 200, 300],
        lambda code: code == 200,
    )
    assert selected == 200


def test_apply_and_finalize_climo_rows_normalizes_values():
    rows = climo_parsing.build_climo_rows(["2025-01-01"])
    climo_parsing.apply_climo_metric_value(
        rows["2025-01-01"],
        "wind_mean",
        {"value": 5.0, "date": "2025-01-01"},
    )
    rows["2025-01-01"]["temp_max"] = 20.0
    rows["2025-01-01"]["temp_min"] = 10.0
    rows["2025-01-01"]["precip_total"] = -3.0

    frame = climo_parsing.finalize_climo_rows(rows)
    row = frame.iloc[0]

    assert math.isclose(row["wind_mean"], 18.0, rel_tol=1e-6)
    assert math.isclose(row["temp_mean"], 15.0, rel_tol=1e-6)
    assert row["precip_total"] == 0.0


import pytest

from utils.series_state import (
    clear_series_owner,
    normalize_chart_series,
    series_owner_matches,
    set_series_owner,
)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"precips": [0.0, 0.3]}, [0.0, 0.3]),
        ({"precip_accum_mm": [0.0, 0.3]}, [0.0, 0.3]),
        ({"precip_accum_mm": [0.0, 0.0], "precip_step_mm": [0.0, 0.3]}, [0.0, 0.3]),
    ],
)
def test_normalize_chart_series_precip_variants(payload, expected):
    normalized = normalize_chart_series({"epochs": [1, 2], **payload, "has_data": True})

    assert normalized["precips"] == expected


def test_series_owner_matches_provider_and_station_case_insensitive():
    state = {}

    set_series_owner(state, "trend_hourly", "poem", "2820")

    assert series_owner_matches(state, "trend_hourly", "POEM", "2820")
    assert not series_owner_matches(state, "trend_hourly", "WU", "ILHOSP26")


def test_clear_series_owner_removes_cached_owner_keys():
    state = {}
    set_series_owner(state, "trend_hourly", "POEM", "2820")

    clear_series_owner(state, "trend_hourly")

    assert not series_owner_matches(state, "trend_hourly", "POEM", "2820")
    assert "trend_hourly_series_provider_id" not in state
    assert "trend_hourly_series_station_id" not in state


def test_normalize_chart_series_keeps_positive_precip_variant():
    normalized = normalize_chart_series(
        {
            "epochs": [1, 2],
            "precips": [0.0, 0.0],
            "precip_accum_mm": [0.0, 0.0],
            "precip_step_mm": [0.0, 0.4],
            "has_data": True,
        }
    )

    assert normalized["precips"] == [0.0, 0.4]


def test_normalize_chart_series_accepts_precip_accum_variant():
    normalized = normalize_chart_series(
        {
            "epochs": [1, 2, 3],
            "precip_accum_mm": [0.0, 0.2, 0.5],
            "precip_step_mm": [0.1, 0.2, 0.3],
            "has_data": True,
        }
    )

    assert normalized["precips"] == [0.0, 0.2, 0.5]

import pytest

from utils.series_state import (
    chart_series_has_backend_derivatives,
    clear_series_owner,
    normalize_chart_series,
    series_owner_matches,
    set_series_owner,
    store_chart_series,
)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"precips": [0.0, 0.3]}, [0.0, 0.3]),
        ({"precip_accum_mm": [0.0, 0.3]}, []),
        ({"precip_step_mm": [0.0, 0.3]}, []),
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


def test_normalize_chart_series_prefers_canonical_precips():
    normalized = normalize_chart_series(
        {
            "epochs": [1, 2],
            "precips": [0.0, 0.4],
            "precip_accum_mm": [0.0, 0.0],
            "precip_step_mm": [0.0, 0.4],
            "has_data": True,
        }
    )

    assert normalized["precips"] == [0.0, 0.4]


def test_normalize_chart_series_rejects_provider_precip_aliases():
    normalized = normalize_chart_series(
        {
            "epochs": [1, 2, 3],
            "precip_accum_mm": [0.0, 0.2, 0.5],
            "precip_step_mm": [0.1, 0.2, 0.3],
            "has_data": True,
        }
    )

    assert normalized["precips"] == []


def test_store_chart_series_preserves_backend_derived_fields():
    state = {}
    payload = {
        "epochs": [1, 2],
        "temps": [20.0, 21.0],
        "humidities": [55.0, 57.0],
        "pressures_abs": [1000.0, 1001.0],
        "theta_e": [305.0, 306.0],
        "mixing_ratios": [8.1, 8.3],
        "theta_e_trends": [None, 1.0],
        "mixing_ratio_trends": [None, 0.2],
        "pressure_trends": [None, 0.5],
        "vapor_pressures": [12.8, 13.1],
        "saturation_pressures": [23.4, 24.1],
        "theoretical_solar_radiations": [0.0, 50.0],
        "wind_u": [1.0, 1.2],
        "wind_v": [0.5, 0.7],
        "has_data": True,
    }

    normalized = store_chart_series(state, payload)

    assert normalized["vapor_pressures"] == [12.8, 13.1]
    assert state["chart_vapor_pressures"] == [12.8, 13.1]
    assert state["chart_theta_e_trends"] == [None, 1.0]
    assert chart_series_has_backend_derivatives(normalized)


def test_chart_series_without_backend_derivatives_is_not_complete():
    assert not chart_series_has_backend_derivatives(
        {
            "epochs": [1, 2],
            "temps": [20.0, 21.0],
            "humidities": [55.0, 57.0],
            "pressures_abs": [1000.0, 1001.0],
            "has_data": True,
        }
    )

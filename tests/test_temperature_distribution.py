"""Distribución de máximas, mínimas y medias diarias del histórico."""

from __future__ import annotations

import pandas as pd

from domain.climograms import build_temperature_distribution


def test_distribution_uses_daily_values_and_percentages() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-06-01", periods=5),
            "temp_max": [19.9, 20.0, 22.0, 25.0, 29.9],
            "temp_min": [9.0, 10.0, 11.0, 14.0, 15.0],
            "temp_mean": [14.0, 15.0, 16.0, 19.0, 20.0],
        }
    )

    result = build_temperature_distribution(frame)

    maximums = result["temp_max"]
    assert maximums["bin_start"] == [18.0, 20.0, 22.0, 24.0, 26.0, 28.0]
    assert maximums["counts"] == [1, 1, 1, 1, 0, 1]
    assert maximums["percentages"] == [20.0, 20.0, 20.0, 20.0, 0.0, 20.0]
    assert maximums["sample_count"] == 5
    assert result["unit"] == "°C"
    assert result["bin_width"] == 2.0


def test_distribution_consolidates_duplicate_dates() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2025-06-01", "2025-06-01", "2025-06-02"],
            "temp_max": [20.0, 23.0, 26.0],
            "temp_min": [12.0, 10.0, 14.0],
            "temp_mean": [16.0, 18.0, 20.0],
        }
    )

    result = build_temperature_distribution(frame)

    assert result["temp_max"]["sample_count"] == 2
    assert result["temp_min"]["sample_count"] == 2
    assert result["temp_mean"]["sample_count"] == 2
    # La máxima del día 1 es 23, la mínima 10 y la media de sus muestras 17.
    assert result["temp_max"]["counts"] == [1, 0, 1]
    assert result["temp_min"]["counts"] == [1, 0, 1]
    assert result["temp_mean"]["counts"] == [1, 0, 1]


def test_distribution_respects_fahrenheit_units() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2025-01-01", "2025-01-02"],
            "temp_max": [0.0, 5.0],
            "temp_min": [-5.0, 0.0],
            "temp_mean": [-2.5, 2.5],
        }
    )

    result = build_temperature_distribution(frame, {"temperature": "f"})

    assert result["unit"] == "°F"
    assert result["bin_width"] == 4.0
    assert result["temp_max"]["sample_count"] == 2


def test_distribution_keeps_missing_series_empty() -> None:
    frame = pd.DataFrame({"date": ["2025-01-01"], "temp_max": [12.0]})

    result = build_temperature_distribution(frame)

    assert result["temp_max"]["sample_count"] == 1
    assert result["temp_min"]["sample_count"] == 0
    assert result["temp_mean"]["bin_start"] == []


def test_daily_distribution_can_share_minimum_and_maximum_scale() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2023-08-01", "2023-08-02", "2023-08-03"],
            "temp_mean": [24.0, 25.0, 26.0],
            "temp_max": [35.2, 38.8, 36.1],
            "temp_min": [18.2, 16.6, 17.5],
        }
    )

    result = build_temperature_distribution(frame, shared_bounds=True)

    # 16,6–38,8 °C producen doce categorías comunes de dos grados.
    expected_bins = [16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 36.0, 38.0]
    assert result["temp_max"]["bin_start"] == expected_bins
    assert result["temp_min"]["bin_start"] == expected_bins
    assert result["temp_mean"]["bin_start"] == expected_bins
    assert result["temp_max"]["counts"] == [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1]
    assert result["temp_min"]["counts"] == [2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert result["temp_mean"]["counts"] == [0, 0, 0, 0, 2, 1, 0, 0, 0, 0, 0, 0]

from __future__ import annotations

import math

import pytest

from domain.trend_series import derive_trend_series


def test_derive_trend_series_calculates_thermodynamics_and_hourly_rates() -> None:
    data = {
        "epochs": [1_700_000_000, 1_700_001_200, 1_700_002_400],
        "temps": [20.0, 21.0, 22.0],
        "humidities": [50.0, 52.0, 54.0],
        "pressures_abs": [1000.0, 1001.0, 1002.0],
        "winds": [10.0, 12.0, 14.0],
        "wind_dirs": [180.0, 270.0, 360.0],
    }

    result = derive_trend_series(data, period="today")

    assert len(result["theta_e"]) == 3
    assert len(result["mixing_ratios"]) == 3
    assert len(result["vapor_pressures"]) == 3
    assert len(result["saturation_pressures"]) == 3
    assert result["wind_u"][1] == pytest.approx(12.0)
    assert result["wind_v"][0] == pytest.approx(10.0)
    assert result["theta_e_interval_minutes"] == 20
    assert result["theta_e_trends"][1] == pytest.approx(
        (result["theta_e"][1] - result["theta_e"][0]) * 3.0
    )
    assert result["mixing_ratio_trends"][2] == pytest.approx(
        (result["mixing_ratios"][2] - result["mixing_ratios"][1]) * 3.0
    )


def test_derive_trend_series_recovers_humidity_and_absolute_pressure() -> None:
    data = {
        "epochs": [1_700_000_000, 1_700_010_800],
        "temps": [20.0, 21.0],
        "humidities": [float("nan"), float("nan")],
        "dewpts": [10.0, 11.0],
        "pressures": [1015.0, 1016.0],
    }

    result = derive_trend_series(data, period="synoptic", station_elevation=800.0)

    assert all(not math.isnan(value) for value in result["humidities"])
    assert result["pressures_abs"][0] == pytest.approx(1015.0 * math.exp(-0.1))
    assert result["theta_e_interval_minutes"] == 180
    assert not math.isnan(result["pressure_trends"][1])


def _hourly_series(*, base, n_before, n_today, with_abs):
    """Serie horaria today (desde base=00:00) + recent (horas previas a
    medianoche). Presión con pendiente de 1 hPa/h para tendencia no nula."""
    def _p(epoch):
        return 1000.0 + (epoch - base) / 3600.0  # hPa, 1/h

    today_epochs = [base + i * 3600 for i in range(n_today)]
    recent_epochs = [base - (n_before - k) * 3600 for k in range(n_before)]
    today = {
        "epochs": today_epochs,
        "temps": [20.0] * n_today,
        "humidities": [50.0] * n_today,
        "pressures": [_p(e) for e in today_epochs],  # MSL
    }
    recent = {
        "epochs": recent_epochs,
        "temps": [20.0] * n_before,
        "humidities": [50.0] * n_before,
        "pressures": [_p(e) for e in recent_epochs],  # MSL
        "has_data": True,
    }
    if with_abs:  # provider tipo Meteocat: el today ya trae presión absoluta
        today["pressures_abs"] = [_p(e) for e in today_epochs]
        recent["pressures_abs"] = [_p(e) for e in recent_epochs]
    return today, recent


@pytest.mark.parametrize("with_abs", [False, True])
def test_lookback_seeds_pressure_trend_at_start_of_day(with_abs) -> None:
    """El lookback fino (puntos previos a medianoche) permite que la
    tendencia de presión 3h arranque en el PRIMER punto del día (00:00) en
    vez de 3 h más tarde. Cubre ambos regímenes de ``derive_trend_series``:
    presión derivada de MSL (``with_abs=False``) y absoluta nativa del today
    (``with_abs=True``, caso Meteocat)."""
    from server.routers.observations import _prepend_lookback_points

    base = 1_700_000_000
    today, recent = _hourly_series(base=base, n_before=4, n_today=6, with_abs=with_abs)

    # Sin lookback: el primer punto no tiene dato 3 h antes → tendencia NaN.
    solo = derive_trend_series(dict(today), period="today")
    assert math.isnan(solo["pressure_trends"][0])

    # Con lookback: los puntos previos siembran la tendencia del 00:00.
    merged = _prepend_lookback_points(today, recent, lookback_hours=4)
    seeded = derive_trend_series(merged, period="today")
    # El índice de las 00:00 en la serie combinada = nº de puntos antepuestos.
    idx_midnight = len(merged["epochs"]) - len(today["epochs"])
    assert not math.isnan(seeded["pressure_trends"][idx_midnight])
    # Pendiente 1 hPa/h → tendencia ≈ 1.0.
    assert seeded["pressure_trends"][idx_midnight] == pytest.approx(1.0, abs=1e-6)


def test_derive_trend_series_preserves_parallel_missing_values() -> None:
    result = derive_trend_series(
        {"epochs": [1_700_000_000, 1_700_001_200], "temps": [20.0]},
        period="today",
    )

    assert len(result["theta_e"]) == 2
    assert all(math.isnan(value) for value in result["theta_e"])
    assert all(math.isnan(value) for value in result["theta_e_trends"])


def test_derive_trend_series_canonicalizes_incremental_precipitation() -> None:
    result = derive_trend_series(
        {
            "epochs": [100, 200, 300, 400],
            "temps": [20.0] * 4,
            "precip_step_mm": [0.2, 0.3, 0.0, 0.4],
        },
        period="today",
    )

    assert result["precips"] == pytest.approx([0.2, 0.5, 0.5, 0.9])
    assert "precip_step_mm" not in result
    assert "precip_accum_mm" not in result


def test_derive_trend_series_repairs_reset_in_accumulated_precipitation() -> None:
    result = derive_trend_series(
        {
            "epochs": [100, 200, 300, 400],
            "temps": [20.0] * 4,
            "precip_accum_mm": [2.5, 2.5, 0.5, 2.6],
        },
        period="today",
    )

    assert result["precips"] == pytest.approx([0.0, 0.0, 0.5, 2.6])


def test_derive_today_series_adds_solar_geometry_and_theoretical_curve() -> None:
    result = derive_trend_series(
        {
            "epochs": [1_718_016_000, 1_718_019_600],
            "temps": [20.0, 21.0],
            "humidities": [60.0, 58.0],
            "pressures_abs": [1000.0, 1000.5],
        },
        period="today",
        station_lat=41.387,
        station_lon=2.169,
        station_elevation=12.0,
        station_tz="Europe/Madrid",
    )

    assert len(result["theoretical_solar_radiations"]) == 2
    assert all(not math.isnan(value) for value in result["theoretical_solar_radiations"])
    assert result["sunrise_epoch"] is not None
    assert result["sunset_epoch"] is not None
    assert result["solar_altitude"] is not None
    assert result["solar_altitude_max"] is not None
    assert isinstance(result["is_nighttime"], bool)


def test_streamlit_tabs_do_not_import_meteorological_formula_modules() -> None:
    from pathlib import Path

    observation_source = Path("tabs/observation.py").read_text(encoding="utf-8")
    trends_source = Path("tabs/trends.py").read_text(encoding="utf-8")
    for source in (observation_source, trends_source):
        assert "models.thermodynamics" not in source
        assert "models.radiation" not in source
        assert "models.trends" not in source

"""Cota de nieve y diagnóstico de cruces múltiples."""

import numpy as np
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from rasterio.crs import CRS
from rasterio.transform import from_bounds

from server.services.thermal_levels import IsothermLevelAccumulator, wet_bulb_celsius
from server.services import arome_forecast as forecast


def test_wet_bulb_is_bounded_and_saturated_air_keeps_air_temperature():
    temperature = np.array([5.0, 5.0])
    dewpoint = np.array([5.0, 0.0])
    result = wet_bulb_celsius(temperature, dewpoint, 900.0)
    assert result[0] == pytest.approx(5.0)
    assert 0.0 < result[1] < 5.0


def test_highest_crossing_and_multiple_solution_mask():
    profile = IsothermLevelAccumulator(np.array([2.0, 2.0]), np.array([100.0, 100.0]), 0.5)
    profile.add(np.array([-1.0, -1.0]), np.array([1_000.0, 1_000.0]))
    profile.add(np.array([2.0, -2.0]), np.array([2_000.0, 2_000.0]))
    profile.add(np.array([-1.0, -3.0]), np.array([3_000.0, 3_000.0]))
    level, multiple = profile.result(np.array([1.0, 1.0]))
    assert level[0] == pytest.approx(2_500.0)
    assert level[1] == pytest.approx(550.0)
    assert multiple[0] == 1.0
    assert np.isnan(multiple[1])


def test_snow_to_ground_and_dry_cells():
    profile = IsothermLevelAccumulator(np.array([-2.0, 2.0]), np.array([800.0, 0.0]), 0.5)
    profile.add(np.array([-4.0, -2.0]), np.array([1_500.0, 1_000.0]))
    level, multiple = profile.result(np.array([0.2, 0.0]))
    assert level[0] == pytest.approx(800.0)
    assert np.isnan(level[1])
    assert np.isnan(multiple).all()


def test_missing_level_does_not_create_fictitious_crossing():
    profile = IsothermLevelAccumulator(np.array([3.0]), np.array([0.0]), 0.5)
    profile.add(np.array([np.nan]), np.array([500.0]))
    profile.add(np.array([-3.0]), np.array([1_000.0]))
    level, multiple = profile.result(np.array([1.0]))
    assert np.isnan(level[0])
    assert np.isnan(multiple[0])


def test_surface_exactly_at_threshold_counts_as_one_solution():
    profile = IsothermLevelAccumulator(np.array([0.5]), np.array([300.0]), 0.5)
    profile.add(np.array([2.0]), np.array([1_000.0]))
    profile.add(np.array([-2.0]), np.array([2_000.0]))
    level, multiple = profile.result(np.array([1.0]))
    assert level[0] == pytest.approx(1_375.0)
    assert multiple[0] == 1.0


def test_arome_snow_product_uses_highest_crossing_and_marks_multiple(monkeypatch):
    run = datetime(2026, 9, 25, tzinfo=timezone.utc)
    bounds = (0.0, 40.0, 1.0, 41.0)

    def field(value, unit):
        return forecast.RasterField(
            np.full((2, 2), value), from_bounds(*bounds, 2, 2),
            CRS.from_epsg(4326), bounds, unit
        )

    fields = {
        ("surface_t", 2.0): field(275.15, "K"),
        ("surface_td", 2.0): field(275.15, "K"),
        ("pressure", None): field(1000.0, "hPa"),
        ("terrain", None): field(100.0, "m"),
        ("precip", None): field(1.0, "mm"),
        **{("t", p): field(t + 273.15, "K") for p, t in [(900., -1.), (800., 2.), (700., -1.)]},
        **{("td", p): field(t + 273.15, "K") for p, t in [(900., -1.), (800., 2.), (700., -1.)]},
        **{("gp", p): field(h, "m") for p, h in [(900., 1000.), (800., 2000.), (700., 3000.)]},
    }
    client = SimpleNamespace(get_field=lambda _catalog, prefix, _run, _valid, level, _kind, **kwargs: fields[(prefix, level)])
    catalog = SimpleNamespace(by_prefix={"terrain": {run: None}})
    prefixes = {
        "height_temperature": "surface_t", "height_dewpoint": "surface_td",
        "surface_pressure": "pressure", "terrain": "terrain", "precipitation": "precip",
        "pressure_temperature": "t", "pressure_dewpoint": "td", "geopotential": "gp",
    }
    monkeypatch.setattr(forecast, "_surface_fields_from_package", lambda *_: None)
    monkeypatch.setattr(forecast, "_isobaric_levels_from_package", lambda *_: None)
    monkeypatch.setattr(forecast, "_pressure_levels", lambda *_: [900., 800., 700.])

    result = forecast._snow_level_field(client, catalog, prefixes, run, run)
    np.testing.assert_allclose(result.data, 2500.0)
    np.testing.assert_allclose(result.overlay, 1.0)


def test_arome_freezing_level_uses_temperature_without_humidity_or_rain(monkeypatch):
    run = datetime(2026, 9, 25, tzinfo=timezone.utc)
    bounds = (0.0, 40.0, 1.0, 41.0)

    def field(value, unit):
        return forecast.RasterField(
            np.full((2, 2), value), from_bounds(*bounds, 2, 2),
            CRS.from_epsg(4326), bounds, unit
        )

    fields = {
        ("surface_t", 2.0): field(275.15, "K"),
        ("terrain", None): field(100.0, "m"),
        ("t", 900.): field(271.15, "K"),
        ("t", 800.): field(274.15, "K"),
        ("t", 700.): field(271.15, "K"),
        ("gp", 900.): field(1000.0, "m"),
        ("gp", 800.): field(2000.0, "m"),
        ("gp", 700.): field(3000.0, "m"),
    }
    client = SimpleNamespace(
        get_field=lambda _catalog, prefix, _run, _valid, level, _kind: fields[(prefix, level)]
    )
    catalog = SimpleNamespace(by_prefix={"terrain": {run: None}})
    prefixes = {
        "height_temperature": "surface_t", "terrain": "terrain",
        "pressure_temperature": "t", "geopotential": "gp",
    }
    monkeypatch.setattr(forecast, "_isobaric_levels_from_package", lambda *_: None)
    monkeypatch.setattr(forecast, "_pressure_levels", lambda *_: [900., 800., 700.])

    result = forecast._freezing_level_field(client, catalog, prefixes, run, run)
    np.testing.assert_allclose(result.data, 2333.3333, atol=0.001)
    np.testing.assert_allclose(result.overlay, 1.0)

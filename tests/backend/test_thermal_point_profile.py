from datetime import datetime, timezone
import json

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from server.services.arome_forecast import _thermal_crossings
from server.services.arome_wcs import AromeWCS, RasterField
from server.services.thermal_levels import IsothermLevelAccumulator


def test_point_crossings_match_map_accumulator():
    levels = [
        {"pressure_hpa": 970.0, "height_m": 100.0, "temperature_c": 4.0},
        {"pressure_hpa": 850.0, "height_m": 1200.0, "temperature_c": -2.0},
        {"pressure_hpa": 700.0, "height_m": 3000.0, "temperature_c": 2.0},
        {"pressure_hpa": 500.0, "height_m": 5500.0, "temperature_c": -8.0},
    ]
    crossings = _thermal_crossings(levels, 0.0, "temperature_c")
    assert len(crossings) == 3
    accumulator = IsothermLevelAccumulator(np.array([4.0]), np.array([100.0]), 0.0)
    for level in levels[1:]:
        accumulator.add(np.array([level["temperature_c"]]), np.array([level["height_m"]]))
    heights, multiple = accumulator.result()
    assert np.isclose(crossings[-1]["height_m"], heights[0])
    assert multiple[0] == 1.0


def test_one_wcs_request_reads_all_point_pressure_levels(monkeypatch):
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff", width=3, height=3, count=2, dtype="float32",
            crs="EPSG:4326", transform=from_origin(1.0, 43.0, 0.025, 0.025),
        ) as dataset:
            dataset.write(np.full((3, 3), 273.15, dtype="float32"), 1)
            dataset.write(np.full((3, 3), 268.15, dtype="float32"), 2)
            dataset.update_tags(1, GRIB_SHORT_NAME="85000-ISBL", GRIB_UNIT="K")
            dataset.update_tags(2, GRIB_SHORT_NAME="70000-ISBL", GRIB_UNIT="K")
        body = memory.read()

    requests = []
    def fake_get(url, params, token):
        requests.append((url, params, token))
        return body, "application/wmo-grib"

    # test_sin_streamlit reimporta el módulo durante la suite. La clase se
    # importó al coleccionar este test y puede conservar otro diccionario
    # global; se parchea el que realmente consulta su método.
    monkeypatch.setitem(AromeWCS.get_point_isobaric.__globals__, "_api_get", fake_get)
    class Catalog:
        def coverage_id(self, prefix, run):
            return "TEST___2026-09-25T00.00.00Z"

    result = AromeWCS("token").get_point_isobaric(
        Catalog(), "TEST", datetime(2026, 9, 25, tzinfo=timezone.utc),
        datetime(2026, 9, 25, 1, tzinfo=timezone.utc), 42.96, 1.04,
    )
    assert len(requests) == 1
    assert result[850.0][1] == "K"
    assert np.isclose(result[700.0][0], 268.15, atol=0.001)


@pytest.mark.parametrize(
    ("product", "expected_highest"),
    [("freezing-level", 3500.0), ("snow-level", None)],
)
def test_thermal_point_profile_returns_all_crossings(monkeypatch, product, expected_highest):
    import server.services.arome_forecast as forecast

    run = datetime(2026, 9, 25, tzinfo=timezone.utc)
    valid = datetime(2026, 9, 25, 1, tzinfo=timezone.utc)
    geometry = (from_origin(1.0, 43.0, 0.025, 0.025), rasterio.crs.CRS.from_epsg(4326),
                (1.0, 42.975, 1.025, 43.0))
    def field(value, units):
        return RasterField(np.array([[value]], dtype=float), *geometry, units)

    class Client:
        def get_field(self, catalog, prefix, *args, **kwargs):
            return {
                "height_temperature": field(4.0, "C"),
                "height_dewpoint": field(3.0, "C"),
                "terrain": field(100.0, "m"),
                "surface_pressure": field(970.0, "hPa"),
            }[prefix]

        def get_point_isobaric(self, catalog, prefix, *args):
            return {
                "pressure_temperature": {850.0: (-2.0, "C"), 700.0: (2.0, "C"), 500.0: (-8.0, "C")},
                "pressure_dewpoint": {850.0: (-3.0, "C"), 700.0: (1.0, "C"), 500.0: (-9.0, "C")},
                "geopotential": {850.0: (1200.0 * 9.80665, "m^2/s^2"),
                                  700.0: (3000.0 * 9.80665, "m^2/s^2"),
                                  500.0: (5500.0 * 9.80665, "m^2/s^2")},
            }[prefix]

    class Catalog:
        by_prefix = {"terrain": {run: "test"}}
        def resolve(self, kind):
            assert kind == "surface_pressure"
            return kind

    prefixes = {name: name for name in (
        "height_temperature", "height_dewpoint", "terrain", "surface_pressure",
        "pressure_temperature", "pressure_dewpoint", "geopotential"
    )}
    if product == "freezing-level":
        prefixes.pop("surface_pressure")
    monkeypatch.setattr(forecast, "_product_context", lambda *args, **kwargs:
                        ({}, Client(), Catalog(), prefixes, run, [valid]))
    monkeypatch.setattr(forecast, "_pressure_levels", lambda *args: [850.0, 700.0, 500.0])
    monkeypatch.setattr(forecast, "_packages_available", lambda: False)
    monkeypatch.setattr(forecast, "_surface_fields_from_package", lambda *args: None)

    payload = forecast.thermal_point_profile(
        "token", product, valid.isoformat(), run.isoformat(), 42.99, 1.01
    )
    assert len(payload["crossings"]) == 3
    if product == "freezing-level":
        assert np.isclose(payload["crossings"][-1]["height_m"], expected_highest)
    else:
        # Al estar subsaturado, Tw cruza 0,5 °C antes que T: los marcadores
        # de nieve no son los de la isoterma seca del otro mapa.
        assert 3000.0 < payload["crossings"][-1]["height_m"] < 3375.0
    json.dumps(payload, allow_nan=False)

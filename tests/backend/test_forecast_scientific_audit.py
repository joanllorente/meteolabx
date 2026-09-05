"""Regresiones científicas de la auditoría de Forecast del 2026-09-05."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_bounds

from server.services import arome_forecast as af
from server.services import convective_diagnostics as cd
from server.services import arome_packages as ap


def column(values):
    return np.asarray(values, dtype=float)[:, None, None]


def controlled_parcel(buoyancy):
    """Aísla la cuadratura de las aproximaciones termodinámicas de parcela."""
    p = column([1000, 900, 800, 700, 500])
    z = column([0, 1000, 2000, 3000, 5000])
    t = np.full_like(p, 300.)
    with patch.object(cd, "parcel_temperature_profile_k", return_value=t), patch.object(
        cd, "virtual_temperature_k", side_effect=[t, t * (1 + column(buoyancy) / cd.GRAVITY)]
    ):
        return cd.parcel_diagnostics(p, t, t, z, p[0], t[0], t[0])


def test_cape_resolves_positive_triangles_between_zero_crossings():
    result = controlled_parcel([0, -.2, .2, -.2, -.2])
    assert result.lfc_height_m.item() == pytest.approx(1500)
    assert result.cape.item() == pytest.approx(100)


def test_cin_stops_at_the_lfc_even_when_layer_averages_cancel():
    result = controlled_parcel([0, -.2, .2, -.2, -.2])
    assert result.cin.item() == pytest.approx(-150)


def test_el_is_interpolated_at_the_last_positive_to_negative_crossing():
    result = controlled_parcel([0, .2, .2, -.1, -.2])
    assert result.equilibrium_height_m.item() == pytest.approx(2000 + 1000 * 2 / 3)


@pytest.mark.parametrize("missing", [False, True], ids=["truncated", "internal-gap"])
def test_pressure_mean_rejects_incomplete_layers(missing):
    p = column([1000, 900, 800, 700, 600] if missing else [1000, 900, 800])
    wind = np.full_like(p, 10.)
    if missing:
        wind[2] = np.nan
    result = cd.pressure_weighted_layer_mean(p, wind, np.array([[1000.]]), np.array([[600.]]))
    assert np.isnan(result.item())


@pytest.mark.parametrize("missing", [False, True], ids=["truncated", "internal-gap"])
def test_srh_requires_the_entire_requested_layer(missing):
    z = column([0, 500, 1000, 2000, 3000] if missing else [0, 500, 1000])
    u = column([0, 5, np.nan, 15, 20] if missing else [0, 5, 10])
    v = column([10, 5, np.nan, 0, -5] if missing else [10, 5, 0])
    result = cd.storm_relative_helicity(z, u, v, np.zeros((1, 1)), np.zeros((1, 1)), 3000)
    assert np.isnan(result.item())


RUN = datetime(2026, 9, 5, tzinfo=timezone.utc)


def field(value, unit):
    bounds = (0., 40., 1., 41.)
    return af.RasterField(np.full((2, 2), value), from_bounds(*bounds, 2, 2), CRS.from_epsg(4326), bounds, unit)


@pytest.mark.parametrize("product", ["relative-humidity-700", "cloud-cover"])
def test_small_explicit_percentages_are_not_fractions(monkeypatch, product):
    client = SimpleNamespace(get_field=lambda *a, **k: field(1., "%"))
    monkeypatch.setattr(af, "_product_context", lambda *a, **k: (
        af.PRODUCTS[product], client, None, {"field": "RH"}, RUN, [RUN]
    ))
    result, _, _ = af._computed_frame.__wrapped__("audit", product, RUN.isoformat())
    np.testing.assert_allclose(result.data, 1.)


def test_accumulation_does_not_publish_when_an_intermediate_hour_is_absent(monkeypatch):
    hours = [RUN + timedelta(hours=h) for h in (1, 3)]
    client = SimpleNamespace(get_field=lambda *a, **k: field(1., "mm"))
    monkeypatch.setattr(af, "_product_context", lambda *a, **k: (
        af.PRODUCTS["accumulated-precip"], client, None, {"field": "P"}, RUN, hours
    ))
    monkeypatch.setattr(af, "_serialize_grid", lambda _p, f, *a: f.data.copy())
    try:
        frames = list(af.accumulated_precip_series("audit", (hours[-1].isoformat(),)))
    except af.AromeError:
        return  # Rechazar explícitamente también evita publicar el falso total.
    assert not frames or np.isnan(frames[-1][1]).all()


def test_thermodynamic_heights_translate_with_terrain():
    p = column([1000, 900, 800, 700, 500])
    t = column([300, 292, 284, 275, 253])
    td = t - 5
    z0 = cd.hypsometric_height_profile_m(p, t, td, np.array([[0.]]))
    z1 = cd.hypsometric_height_profile_m(p, t, td, np.array([[1700.]]))
    np.testing.assert_allclose(z1 - z0, 1700.)
    a = cd.diagnose_convection(p, t, td, z0, include_dcape=False)
    b = cd.diagnose_convection(p, t, td, z1, include_dcape=False)
    for key in ("mucape", "mlcape", "sbcape", "ml_lfc_height_m"):
        np.testing.assert_allclose(getattr(a, key), getattr(b, key), equal_nan=True)


def test_vertical_totals_is_independent_of_input_temperature_units(monkeypatch):
    monkeypatch.setattr(af, "_isobaric_fields_from_package", lambda *a: None)
    client = SimpleNamespace(get_field=lambda _c, _p, _r, _t, level, _kind:
                             field(1000., "hPa") if _p == "PS" else (field(283.15, "K") if level == 850 else field(-20., "C")))
    result = af._level_difference_field(client, None, {"field": "T", "surface_pressure": "PS"}, af.PRODUCTS["vertical-totals"], RUN, RUN)
    np.testing.assert_allclose(result.data, 30.)


def test_dewpoint_depression_is_not_read_as_dewpoint(tmp_path):
    import rasterio

    path = tmp_path / "depression.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", transform=from_bounds(0, 40, 1, 41, 2, 2),
                       crs="EPSG:4326") as dataset:
        dataset.write(np.full((2, 2), 5., dtype="float32"), 1)
        dataset.update_tags(1, GRIB_ELEMENT="DEPR", GRIB_SHORT_NAME="85000-ISBL",
                            GRIB_VALID_TIME=str(int(RUN.timestamp())), GRIB_UNIT="K")
    result, _ = ap.read_isobaric_extras(path, RUN, [850.], ap.IP3_ELEMENTS)
    # Sin T no se puede convertir T-Td en Td: debe omitirlo o rechazarlo.
    assert not result.get("dewpoint")


def test_vertical_totals_masks_below_ground_850(monkeypatch):
    monkeypatch.setattr(af, "_isobaric_fields_from_package", lambda *a: None)

    def get_field(_catalog, prefix, _run, _time, level, _kind):
        if prefix == "PS":
            return field(800., "hPa")
        return field(10. if level == 850 else -20., "C")

    result = af._level_difference_field(
        SimpleNamespace(get_field=get_field), None, {"field": "T", "surface_pressure": "PS"},
        af.PRODUCTS["vertical-totals"], RUN, RUN,
    )
    assert np.isnan(result.data).all()


def test_energy_matches_independent_quadrature_with_multiple_buoyant_layers():
    from scipy.integrate import quad

    z = column([0, 1000, 2000, 3000, 4000, 5000, 6000])
    b = column([-.2, .2, -.1, .3, .1, -.2, -.3])
    p = 1000 * np.exp(-z / 8000)
    cape, cin, el, elp = cd._parcel_energy(p, z, b, p[0], np.array([[500.]]))
    expected_el = 4000 + 1000 / 3
    fn = lambda h: np.interp(h, z.ravel(), b.ravel())
    expected_cape = quad(fn, 500, expected_el, points=[1000, 2000, 3000, 4000])[0]
    expected_cin = quad(fn, 0, 500)[0]
    assert cape.item() == pytest.approx(expected_cape)
    assert cin.item() == pytest.approx(expected_cin)
    assert el.item() == pytest.approx(expected_el)
    assert elp.item() == pytest.approx(1000 * np.exp(-expected_el / 8000))


def test_open_buoyant_top_has_energy_but_no_equilibrium_level():
    result = controlled_parcel([0, -.2, .2, .2, .2])
    assert result.cape.item() == pytest.approx(650.)
    assert result.cin.item() == pytest.approx(-150.)
    assert np.isnan(result.equilibrium_height_m.item())
    assert np.isnan(result.equilibrium_pressure_hpa.item())


def test_energy_ignores_layers_below_elevated_parcel_origin():
    z = column([0, 1000, 2000, 3000, 4000])
    p = column([1000, 900, 800, 700, 600])
    b = column([np.nan, np.nan, -.2, .2, -.2])
    cape, cin, el, _ = cd._parcel_energy(p, z, b, p[2], np.array([[2500.]]))
    assert cape.item() == pytest.approx(100.)
    assert cin.item() == pytest.approx(-50.)
    assert el.item() == pytest.approx(3500.)


def test_energy_does_not_skip_a_missing_level_above_origin():
    result = controlled_parcel([0, -.2, np.nan, .2, -.2])
    assert np.isnan(result.cape.item())
    assert np.isnan(result.cin.item())
    assert np.isnan(result.equilibrium_height_m.item())


@pytest.mark.parametrize("unit", ["%", "[%]", "percent", ""])
def test_percent_convention_is_independent_of_field_extremes(unit):
    np.testing.assert_allclose(af._as_percent(np.array([0., .5, 1.]), unit), [0., .5, 1.])


@pytest.mark.parametrize("unit", ["1", "fraction", "dimensionless"])
def test_fraction_units_are_explicitly_converted(unit):
    np.testing.assert_allclose(af._as_percent(np.array([0., .5, 1.]), unit), [0., 50., 100.])


def test_single_frame_accumulation_also_requires_every_hour(monkeypatch):
    hours = [RUN + timedelta(hours=h) for h in (1, 3)]
    client = SimpleNamespace(get_field=lambda *a, **k: pytest.fail("No descargar una serie incompleta"))
    monkeypatch.setattr(af, "_product_context", lambda *a, **k: (
        af.PRODUCTS["accumulated-precip"], client, None, {"field": "P"}, RUN, hours
    ))
    with pytest.raises(af.AromeError, match="Faltan incrementos"):
        af._computed_frame.__wrapped__("audit", "accumulated-precip", hours[-1].isoformat())


def test_revision_hides_old_affected_frames_and_requeues_them(tmp_path):
    from server.services import forecast_store as fs

    store = fs.LocalObjectStore(tmp_path)
    time = RUN.isoformat()
    manifest = fs.new_manifest(time, [time])
    fs.mark_available(manifest, "ship", time)
    fs.mark_available(manifest, "temperature-2m", time)
    manifest.pop("calculation_revision")
    fs.write_json(store, fs.run_manifest_key(time), manifest)
    current = fs.read_json(store, fs.run_manifest_key(time))
    assert current["products"]["ship"]["available_times"] == []
    assert current["products"]["temperature-2m"]["available_times"] == [time]
    assert "--calc" in fs.frame_key(time, "ship", time)
    assert "--calc" not in fs.frame_key(time, "temperature-2m", time)
    fs.mark_available(current, "ship", time)
    fs.write_json(store, fs.run_manifest_key(time), current)
    assert fs.read_json(store, fs.run_manifest_key(time))["products"]["ship"]["available_times"] == [time]


def test_arome_revision_does_not_change_ecmwf_availability(tmp_path):
    from server.services import forecast_store as fs

    store = fs.LocalObjectStore(tmp_path)
    time = RUN.isoformat()
    manifest = fs.new_manifest(time, [time], model="ecmwf")
    fs.mark_available(manifest, "z500-mslp", time)
    key = fs.run_manifest_key(time, model="ecmwf")
    fs.write_json(store, key, manifest)
    assert fs.read_json(store, key) == manifest

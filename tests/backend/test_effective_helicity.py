"""Effective inflow bounds, numerical integration and shared SCP ingredients."""
from types import SimpleNamespace

import numpy as np
import pytest

from server.services import convective_diagnostics as cd


def test_elevated_helicity_matches_metpy_with_interpolated_limits():
    calc = pytest.importorskip('metpy.calc')
    from metpy.units import units
    z = np.array([0., 400., 1100., 2000., 3500., 6000.])
    u = np.array([3., 8., 10., 7., 20., 25.])
    v = np.array([-4., -2., 5., 15., 17., 22.])
    actual = cd.storm_relative_helicity(
        z[:, None, None], u[:, None, None], v[:, None, None],
        np.array([[8.]]), np.array([[3.]]), np.array([[1900.]]),
        bottom_m=np.array([[650.]]),
    )
    expected = calc.storm_relative_helicity(
        z * units.m, u * units('m/s'), v * units('m/s'),
        depth=1900 * units.m, bottom=650 * units.m,
        storm_u=8 * units('m/s'), storm_v=3 * units('m/s'),
    )[2].magnitude
    assert actual.item() == pytest.approx(expected)
    u[2] = np.nan
    assert np.isnan(cd.storm_relative_helicity(
        z[:, None, None], u[:, None, None], v[:, None, None],
        np.array([[8.]]), np.array([[3.]]), 1900., bottom_m=650.,
    )).all()


def test_scp_matches_metpy_at_shear_thresholds_and_preserves_missing():
    calc = pytest.importorskip('metpy.calc')
    from metpy.units import units
    shear = np.array([0., 9.999, 10., 15., 20., 40., np.nan])
    cape = np.full(7, 2000.)
    srh = np.full(7, 100.)
    expected = calc.supercell_composite(
        cape * units('J/kg'), srh * units('m^2/s^2'), shear * units('m/s')
    ).magnitude
    np.testing.assert_allclose(cd.supercell_composite_parameter(cape, srh, shear), expected)
    assert cd.supercell_composite_parameter(np.array([1000.]), np.array([-50.]), np.array([20.]))[0] == -1.
    assert np.isnan(cd.supercell_composite_parameter(np.array([np.nan]), np.array([50.]), np.array([0.]))[0])


def test_effective_layer_is_contiguous_and_reuses_cached_parcels(monkeypatch):
    # Columns: surface layer, elevated layer, stable, unobserved top.
    p = np.broadcast_to(np.array([1000., 900., 800., 700., 600., 500.])[:, None, None], (6, 4, 1)).copy()
    z = (1000. - p) * 10.
    t = np.full_like(p, 280.)
    capes = np.array([[100, 0, 0, 100], [200, 100, 0, 100],
                      [0, 200, 0, 100], [300, 0, 0, 100],
                      [0, 0, 0, 100], [0, 0, 0, 100.]])
    # Encode column identity in dewpoint for compacted calls.
    td = np.broadcast_to(np.arange(4)[None, :, None], p.shape).astype(float)
    calls = []
    def parcel(pressure, temperature, dewpoint, height, origin, *_):
        level = int((1000 - origin[0, 0]) / 100)
        columns = dewpoint[0, :, 0].astype(int)
        calls.append((level, columns.tolist()))
        return SimpleNamespace(cape=capes[level, columns, None], cin=np.full((len(columns), 1), -250.))
    monkeypatch.setattr(cd, 'parcel_diagnostics', parcel)
    surface = SimpleNamespace(cape=capes[0, :, None], cin=np.full((4, 1), -250.))
    mu = SimpleNamespace(cape=capes[1, :, None], cin=np.full((4, 1), -250.))
    base, top = cd.effective_inflow_layer(p, t, td, z, surface, mu, np.ones((4, 1), dtype=int))
    np.testing.assert_allclose(base[:, 0], [0, 1000, np.nan, 0], equal_nan=True)
    np.testing.assert_allclose(top[:, 0], [1000, 2000, np.nan, np.nan], equal_nan=True)
    assert all(level > 1 for level, _ in calls)  # No second SB or MU ascent.
    assert all(0 not in cols for level, cols in calls if level > 2)
    assert all(1 not in cols for level, cols in calls if level > 3)


def test_new_products_share_convective_scheduling():
    from server.services.arome_forecast import PRODUCTS
    from server.services.forecast_store import (
        CONVECTIVE_FORECAST_PRODUCTS, DERIVED_FORECAST_PRODUCTS,
        PERSISTED_FORECAST_PRODUCTS, CAPPED_FORECAST_PRODUCTS,
    )
    for product in ('esrh', 'scp', 'stp'):
        assert PRODUCTS[product]['kind'] == 'convective'
        for group in (CONVECTIVE_FORECAST_PRODUCTS, DERIVED_FORECAST_PRODUCTS,
                      PERSISTED_FORECAST_PRODUCTS, CAPPED_FORECAST_PRODUCTS):
            assert group.count(product) == 1


@pytest.mark.parametrize('name,values,expected', [
    ('ebwd', [0., 12.49, 12.5, 20., 30., 40.], [0., 0., .625, 1., 1.5, 1.5]),
    ('ml_lcl_agl_m', [0., 999., 1000., 1500., 2000., 2500.], [1., 1., 1., .5, 0., 0.]),
    ('mlcin', [0., -49., -50., -125., -200., -250.], [1., 1., 1., .5, 0., 0.]),
    ('effective_base_agl_m', [0., 1., 1000., np.nan, -1., 0.], [1., 0., 0., np.nan, np.nan, 1.]),
    ('effective_srh', [150., 300., -150., 0., np.nan, 75.], [1., 2., 0., 0., np.nan, .5]),
])
def test_effective_stp_spc_thresholds(name, values, expected):
    ingredients = dict(mlcape=np.full(6, 1500.), mlcin=np.full(6, -50.),
                       ml_lcl_agl_m=np.full(6, 1000.), effective_srh=np.full(6, 150.),
                       ebwd=np.full(6, 20.), effective_base_agl_m=np.zeros(6))
    ingredients[name] = np.array(values)
    np.testing.assert_allclose(cd.significant_tornado_parameter(**ingredients), expected, equal_nan=True)


def test_effective_stp_missing_ingredient_is_not_filled_with_zero():
    for key in ('mlcape', 'mlcin', 'ml_lcl_agl_m', 'effective_srh', 'ebwd', 'effective_base_agl_m'):
        ingredients = dict(mlcape=np.array([1500.]), mlcin=np.array([-50.]),
                           ml_lcl_agl_m=np.array([1000.]), effective_srh=np.array([150.]),
                           ebwd=np.array([0.]), effective_base_agl_m=np.array([1000.]))
        ingredients[key][:] = np.nan
        assert np.isnan(cd.significant_tornado_parameter(**ingredients)).all()


def test_lcl_height_is_interpolated_in_log_pressure_without_extrapolation():
    p = np.array([1000., 900., 800.])[:, None, None]
    z = np.array([1200., 2100., 3200.])[:, None, None]
    target = np.array([[np.sqrt(900. * 800.)]])
    assert cd.height_at_pressure_m(p, z, target).item() == pytest.approx(2650.)
    assert np.isnan(cd.height_at_pressure_m(p, z, np.array([[700.]]))).all()


def test_mixed_layer_stp_ingredients_reuse_the_parcel_and_use_agl(monkeypatch):
    from tests.backend.test_convective_diagnostics import _synthetic_profile
    p, t, td, _, _, terrain, *_ = _synthetic_profile(3, 4)
    z = cd.hypsometric_height_profile_m(p, t, td, terrain)
    calls = []
    original = cd.parcel_diagnostics
    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(cd, 'parcel_diagnostics', record)
    result = cd.diagnose_convection(p, t, td, z, include_dcape=False)
    mixed = calls[2]  # MU, SB, then ML100; remaining calls find effective bounds.
    np.testing.assert_array_equal(result.mlcin, mixed.cin)
    np.testing.assert_allclose(result.ml_lcl_height_m, mixed.lcl_height_m - terrain, equal_nan=True)
    assert result.mlcin is mixed.cin

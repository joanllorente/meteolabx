import numpy as np
import pytest
from server.services import convective_diagnostics as diagnostics

native = pytest.importorskip('server.services._dcape_native')


def profiles(rows=6, cols=7):
    rng = np.random.default_rng(772)
    levels = np.linspace(1000, 300, 25)
    p = np.broadcast_to(levels[:, None, None], (25, rows, cols)).copy()
    p[0] -= rng.uniform(0, 70, (rows, cols))
    t = 303-(1000-p)*.065+rng.normal(0,.5,p.shape)
    d = t-rng.uniform(1,18,p.shape)
    h = (1000-p)*11
    return p,t,d,h


@pytest.mark.parametrize('case', ['normal', 'nan', 'strided', 'invalid', 'duplicate'])
def test_native_source_and_dcape_match_reference(monkeypatch, case):
    args = profiles()
    if case == 'nan':
        for a in args: a[:, 1, 2] = np.nan
        args[2][4, 2, 3] = np.nan
    if case == 'strided': args = tuple(a[:, ::2, ::-1] for a in args)
    if case == 'invalid': args[0][:] = np.nan
    if case == 'duplicate': args[0][1] = args[0][0]
    p,t,d,h = args
    d = np.minimum(d,t)
    reference = diagnostics._dcape_source_python(p,t,d,h)
    actual = native.source(p,t,d,h)
    np.testing.assert_array_equal(actual[0], reference[0])
    for a,b in zip(actual[1:], reference[1:]):
        np.testing.assert_allclose(a,b,rtol=1e-12,atol=1e-10,equal_nan=True)
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE','python')
    reference = diagnostics.downdraft_cape(*args)
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE','cpp')
    actual = diagnostics.downdraft_cape(*args)
    np.testing.assert_allclose(actual,reference,rtol=1e-10,atol=1e-8,equal_nan=True)


def test_native_rejects_shapes_and_conversion():
    args=profiles()
    with pytest.raises(ValueError): native.source(args[0],args[1][:,:2],args[2],args[3])
    with pytest.raises(TypeError): native.source(*(a.astype('int32') for a in args))


def test_scalar_thermodynamics_match_sharppy():
    thermo = pytest.importorskip('sharppy.sharptab.thermo')
    rng = np.random.default_rng(123)
    p = rng.uniform(450, 1050, 500)
    t = rng.uniform(-40, 35, 500)
    target = rng.uniform(450, 1050, 500)
    td = t-rng.uniform(0, 30, 500)
    for function, arguments in [('wetlift', (p,t,target)), ('wetbulb', (p,t,td)),
                                ('satlift', (target,t))]:
        expected = np.array([getattr(thermo, function)(*(float(a[i]) for a in arguments))
                             for i in range(len(p))])
        np.testing.assert_allclose(getattr(native, function)(*arguments), expected,
                                   rtol=1e-11, atol=1e-9)
    assert native.satlift(1000., 12.) == 12.
    assert np.isnan(native.satlift(-1., 12.))


def test_column_dcape_matches_scalar_sharppy(monkeypatch):
    thermo = pytest.importorskip('sharppy.sharptab.thermo')
    from types import SimpleNamespace
    args = profiles()
    # Keep the independent Python source/integral, evaluate thermo scalar by scalar.
    monkeypatch.setattr(diagnostics, 'sharppy_thermo', SimpleNamespace(
        wetbulb=np.vectorize(thermo.wetbulb, otypes=[float]),
        wetlift=np.vectorize(thermo.wetlift, otypes=[float])))
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE', 'python')
    expected = diagnostics.downdraft_cape(*args)
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE', 'cpp-column')
    np.testing.assert_allclose(diagnostics.downdraft_cape(*args), expected,
                               rtol=1e-10, atol=1e-8)


def test_column_dcape_is_partition_independent(monkeypatch):
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE', 'cpp-column')
    args = profiles(128, 384)
    # Include invalid columns and a missing level, with noncontiguous views.
    args[2][:, 5, 8] = np.nan
    args[1][4, 50, 3] = np.nan
    args = tuple(a[:, :, ::-1] for a in args)
    expected = diagnostics.downdraft_cape(*args)
    for rows in (16, 32, 64):
        actual = np.concatenate([diagnostics.downdraft_cape(
            *(a[:, start:start+rows] for a in args)) for start in range(0, 128, rows)])
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("case", ["normal", "mixed", "irregular", "supersaturated", "nan"])
def test_float32_views_match_promoted_reference(monkeypatch, case):
    args = tuple(a.astype('float32')[:, ::-1, ::2] for a in profiles())
    if case == 'mixed': args = (*args[:3], args[3].astype('float64'))
    if case == 'irregular': args[0][2] = args[0][0] + 10
    if case == 'supersaturated': args[2][3] = args[1][3] + 5
    if case == 'nan': args[2][3, 1, 1] = np.nan
    promoted = tuple(a.astype('float64') for a in args)
    expected = diagnostics._dcape_source_python(promoted[0], promoted[1],
                    np.minimum(promoted[2], promoted[1]), promoted[3])
    actual = native.source(*args)
    np.testing.assert_array_equal(actual[0], expected[0])
    for a, b in zip(actual[1:], expected[1:]):
        np.testing.assert_allclose(a,b,rtol=1e-12,atol=1e-10)
    for engine in ('cpp', 'cpp-column'):
        monkeypatch.setenv('METEOLABX_DCAPE_ENGINE', engine)
        np.testing.assert_array_equal(diagnostics.downdraft_cape(*args),
                                      diagnostics.downdraft_cape(*promoted))


def test_native_saturation_matches_active_python_solver(monkeypatch):
    rng = np.random.default_rng(44)
    p = rng.uniform(150, 1050, (25, 8, 9))
    target = rng.uniform(200, 450, (8, 9))
    initial = rng.uniform(150, 400, p.shape)
    p[0, 0, 0] = np.nan
    initial[2, 1, 1] = np.nan
    target[3, 3] = np.nan
    monkeypatch.setenv('METEOLABX_SATURATION_ENGINE', 'python')
    expected = diagnostics._saturated_temperature_from_theta_e(p,target,initial)
    monkeypatch.setenv('METEOLABX_SATURATION_ENGINE', 'cpp')
    actual = diagnostics._saturated_temperature_from_theta_e(p,target,initial)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-8)


def test_shared_targets_match_python():
    args = profiles()
    args[0][:] = np.linspace(1000,400,25)[:,None,None]
    args[2][3,1,1] = np.nan
    expected = diagnostics._dcape_source_python(*args)
    actual = native.source(*args)
    np.testing.assert_array_equal(actual[0], expected[0])
    for a,b in zip(actual[1:],expected[1:]):
        np.testing.assert_allclose(a,b,rtol=1e-12,atol=1e-10)


def test_native_dcape_only_keeps_stored_dtype(monkeypatch):
    from server.services import arome_forecast as forecast
    p,t,d,h = (a.astype('float32') for a in profiles())
    terrain = np.zeros(p.shape[1:])
    monkeypatch.setenv('METEOLABX_DCAPE_ENGINE', 'cpp')
    def reject_conversion(array):
        raise AssertionError('dedicated native pass must not promote full profiles')
    monkeypatch.setattr(forecast, '_as_float64', reject_conversion)
    result = forecast._convective_outputs(p,t,d,t,t,terrain,terrain,terrain,
                                         list(p[:,0,0]), only_dcape=True, vertical_velocity=t)
    height = diagnostics.hypsometric_height_profile_m(p,t,d,terrain)
    expected = diagnostics.downdraft_cape(p.astype(float),t.astype(float),d.astype(float),height)
    np.testing.assert_array_equal(result['dcape'], expected)

import numpy as np
import pytest
from server.services import convective_diagnostics as diagnostics

pytest.importorskip('server.services._dcape_native')
pytest.importorskip('sharppy.sharptab.params')


@pytest.mark.parametrize('dtype', ['float32', 'float64'])
def test_ship_native_preserves_sharppy_adapter(monkeypatch, dtype):
    dtype = np.dtype(dtype).type
    rng = np.random.default_rng(821)
    args = [rng.uniform(lo,hi,(12,25)).astype(dtype)[:,::-1] for lo,hi in
            [(-100,6000),(-1,25),(-1,10),(-35,5),(-1,50),(-1,6000)]]
    boundaries = [[0,1300], [0,11,13.6], [0,5.8], [0,-5.5], [0,7,27], [0,2400]]
    for a, thresholds in zip(args,boundaries):
        for j, value in enumerate(thresholds):
            a[0,j]=value
            a[1,j]=np.nextafter(dtype(value),dtype(np.inf))
            a[2,j]=np.nextafter(dtype(value),dtype(-np.inf))
        a[3,0]=np.nan
        a[4,1]=np.inf
    monkeypatch.setenv('METEOLABX_SHIP_ENGINE','python')
    with np.errstate(divide='ignore',invalid='ignore'):
        expected=diagnostics.significant_hail_parameter_sharppy(*args)
    monkeypatch.setenv('METEOLABX_SHIP_ENGINE','cpp')
    actual=diagnostics.significant_hail_parameter_sharppy(*args)
    np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-12)


def test_ship_native_broadcast(monkeypatch):
    args=(np.array([[0.],[2000.]]),12.,7.,-15.,np.array([7.,20.,27.]),3000.)
    monkeypatch.setenv('METEOLABX_SHIP_ENGINE','python')
    expected=diagnostics.significant_hail_parameter_sharppy(*args)
    monkeypatch.setenv('METEOLABX_SHIP_ENGINE','cpp')
    np.testing.assert_allclose(diagnostics.significant_hail_parameter_sharppy(*args),expected,
                               rtol=1e-12,atol=1e-12)

from datetime import datetime,timezone
import numpy as np
import pytest
from server.services import arome_forecast as f

RUN=datetime(2026,9,20,tzinfo=timezone.utc)


@pytest.mark.parametrize('product,element,level,units',[
    ('temperature-850','temperature',850.,'C'),
    ('temperature-500','temperature',500.,'C'),
    ('relative-humidity-700','relative_humidity',700.,'%')])
def test_reads_only_requested_cached_level(monkeypatch,product,element,level,units):
    monkeypatch.setattr(f,'_packages_available',lambda:True)
    monkeypatch.setattr(f,'package_ready',lambda *args:True)
    data=np.array([[1.,np.nan]])
    geometry=('transform','crs','bounds')
    def read(path,valid,levels,elements):
        assert valid==RUN and levels==[level] and elements==(element,)
        return {element:{level:data}},geometry
    monkeypatch.setattr(f,'read_isobaric_profile',read)
    monkeypatch.setattr(f,'ensure_package',lambda *args:pytest.fail('must not download'))
    result=f._native_field_from_cached_ip1(product,RUN,RUN)
    assert result.data is data and result.units==units
    assert result.transform=='transform'


@pytest.mark.parametrize('ready,enabled',[(False,True),(True,False)])
def test_missing_or_disabled_package_uses_wcs_path(monkeypatch,ready,enabled):
    monkeypatch.setattr(f,'_packages_available',lambda:enabled)
    monkeypatch.setattr(f,'package_ready',lambda *args:ready)
    monkeypatch.setattr(f,'read_isobaric_profile',lambda *args:pytest.fail('must not read'))
    assert f._native_field_from_cached_ip1('temperature-850',RUN,RUN) is None


def test_unreadable_cached_file_falls_back(monkeypatch):
    monkeypatch.setattr(f,'_packages_available',lambda:True)
    monkeypatch.setattr(f,'package_ready',lambda *args:True)
    def failed(*args):raise OSError('file removed')
    monkeypatch.setattr(f,'read_isobaric_profile',failed)
    assert f._native_field_from_cached_ip1('temperature-850',RUN,RUN) is None


@pytest.mark.parametrize('product,level',[('temperature-850',850.),('temperature-500',500.)])
@pytest.mark.parametrize('cached_overlay',[True,False])
def test_native_geopotential_overlay_cache_and_fallback(monkeypatch,product,level,cached_overlay):
    from types import SimpleNamespace
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    bounds=(0.,40.,1.,41.)
    geometry=(from_bounds(*bounds,2,2),CRS.from_epsg(4326),bounds)
    geopotential=np.array([[1500.*9.80665,np.nan],[5000.*9.80665,0.]])
    calls=[]
    monkeypatch.setattr(f,'_packages_available',lambda:True)
    monkeypatch.setattr(f,'package_ready',lambda *args:True)
    def read(path,valid,levels,elements):
        assert valid==RUN and levels==[level]
        element=elements[0]
        if element=='geopotential':
            if not cached_overlay:return {},geometry
            return {element:{level:geopotential.copy()}},geometry
        assert element=='temperature'
        return {element:{level:np.full((2,2),-5.)}},geometry
    def get(catalog,prefix,run,valid,requested_level,kind,**kwargs):
        calls.append(prefix)
        assert prefix=='GP' and requested_level==level and kind=='pressure'
        return f.RasterField(geopotential.copy(),*geometry,'m^2/s^2')
    monkeypatch.setattr(f,'read_isobaric_profile',read)
    monkeypatch.setattr(f,'ensure_package',lambda *args:pytest.fail('must not download'))
    monkeypatch.setattr(f,'_product_context',lambda *args:(
        f.PRODUCTS[product],SimpleNamespace(get_field=get),None,
        {'field':'TMP','overlay':'GP'},RUN,[RUN]))
    result,_,_=f._computed_frame.__wrapped__('test',product,RUN.isoformat())
    assert calls==([] if cached_overlay else ['GP'])
    np.testing.assert_allclose(result.data,-5.)
    np.testing.assert_allclose(result.overlay,[[150.,np.nan],[500.,0.]])
    assert result.overlay_units=='dam'

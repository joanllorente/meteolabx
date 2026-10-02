"""SP1: statistical intervals, block boundaries and map integration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_bounds

from server.services import arome_forecast as f, arome_packages as p
from server.services.arome_models import using_model

RUN = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
BOUNDS = (0., 40., 1., 41.)
GEOMETRY = (from_bounds(*BOUNDS, 2, 2), CRS.from_epsg(4326), BOUNDS)


def tags(name, hour):
    element, level, unit, mode = p.SP1_FIELDS[name]
    end = RUN + timedelta(hours=hour)
    result = {
        'GRIB_ELEMENT': element, 'GRIB_SHORT_NAME': level,
        'GRIB_UNIT': {'TPRATE': '[kg/(m^2*s)]', 'DSWRF': '[W/(m^2)]'}.get(element, f'[{unit}]'),
        'GRIB_REF_TIME': str(int(RUN.timestamp())),
        'GRIB_VALID_TIME': str(int(end.timestamp())),
        'GRIB_FORECAST_SECONDS': str(hour * 3600), 'GRIB_PDS_PDTN': '0',
    }
    if mode != 'instant':
        start = hour - 1 if mode == 'maximum' else 0
        # PDT 4.8 as inventoried: common fields, interval end, statistics.
        assembled = [0, 0, 2, 255, 204, 0, 0, 1, start, 1, 0, 0, 255, -127, -2147483647,
                     end.year, end.month, end.day, end.hour, 0, 0, 1, 0,
                     2 if mode == 'maximum' else 1, 2, 1, hour - start, 255, 0]
        result.update(GRIB_FORECAST_SECONDS=str(start * 3600), GRIB_PDS_PDTN='8',
                      GRIB_PDS_TEMPLATE_ASSEMBLED_VALUES=' '.join(map(str, assembled)))
    return result


class Dataset:
    transform, crs, bounds = GEOMETRY

    def __init__(self, bands):
        self.bands = bands
        self.count = len(bands)
        self.reads = []

    def tags(self, i): return self.bands[i - 1][0]
    def read(self, i, masked):
        self.reads.append(i)
        return np.ma.masked_invalid(self.bands[i - 1][1])
    def __enter__(self): return self
    def __exit__(self, *args): pass


@pytest.mark.parametrize('name', list(p.SP1_FIELDS))
def test_reads_only_matching_run_level_hour_and_statistic(monkeypatch, name):
    wrong = tags(name, 2)
    wrong['GRIB_SHORT_NAME'] = 'wrong-level'
    data = np.array([[12., np.nan], [0., 8.]])
    dataset = Dataset([(tags(name, 1), data), (wrong, data), (tags(name, 2), data)])
    monkeypatch.setattr(p.rasterio, 'open', lambda path: dataset)
    result = p.read_sp1_field(Path('sp1'), RUN, RUN + timedelta(hours=2), name)
    np.testing.assert_allclose(result[0], data)
    assert result[1] == GEOMETRY
    assert result[2] == p.SP1_FIELDS[name][2]
    assert dataset.reads == [3]


@pytest.mark.parametrize('change', ['run', 'units', 'instant_rate', 'average', 'start', 'length', 'missing', 'end', 'multi'])
def test_ambiguous_precipitation_is_not_used(monkeypatch, change):
    t = tags('precip-1h', 2)
    v = t['GRIB_PDS_TEMPLATE_ASSEMBLED_VALUES'].split()
    if change == 'run': t['GRIB_REF_TIME'] = '0'
    if change == 'units': t['GRIB_UNIT'] = '[m]'
    if change == 'instant_rate': t['GRIB_PDS_PDTN'] = '0'
    if change == 'average': v[23] = '0'
    if change == 'start': t['GRIB_FORECAST_SECONDS'] = '3600'
    if change == 'length': v[26] = '1'
    if change == 'missing': v[22] = '1'
    if change == 'end': v[18] = '13'
    if change == 'multi': v[21] = '2'
    t['GRIB_PDS_TEMPLATE_ASSEMBLED_VALUES'] = ' '.join(v)
    dataset = Dataset([(t, np.ones((2, 2)))])
    monkeypatch.setattr(p.rasterio, 'open', lambda path: dataset)
    assert p.read_sp1_field(Path('sp1'), RUN, RUN + timedelta(hours=2), 'precip-1h') is None
    assert dataset.reads == []


def cached(monkeypatch, values):
    monkeypatch.setattr(f, '_packages_available', lambda: True)
    monkeypatch.setattr(f, 'package_ready', lambda *a: True)
    calls = []
    def read(path, run, valid, name):
        assert run == RUN
        calls.append((path, valid, name))
        h = int((valid - run).total_seconds() / 3600)
        value = values.get((name, h))
        if value is None: return None
        return np.full((2, 2), value), GEOMETRY, p.SP1_FIELDS[name][2]
    monkeypatch.setattr(f, 'read_sp1_field', read)
    monkeypatch.setattr(f, 'ensure_package', lambda *a: pytest.fail('must not download'))
    return calls


def test_hourly_precipitation_crosses_blocks_and_models(monkeypatch):
    calls = cached(monkeypatch, {('precip-1h', 7): 12., ('precip-1h', 6): 8.})
    with using_model('arome-ifs'):
        field = f._field_from_cached_sp1('precip-1h', RUN, RUN + timedelta(hours=7))
    np.testing.assert_allclose(field.data, 4.)
    assert field.units == 'mm'
    assert calls[0][0].name.endswith('07H12H.grib2')
    assert calls[1][0].name.endswith('00H06H.grib2')
    assert all(path.parent.name == 'arome-ifs' for path, _, _ in calls)


def test_missing_previous_hour_falls_back_but_accumulation_does_not_need_it(monkeypatch):
    calls = cached(monkeypatch, {('precip-1h', 7): 12., ('accumulated-precip', 7): 12.})
    assert f._field_from_cached_sp1('precip-1h', RUN, RUN + timedelta(hours=7)) is None
    field = f._field_from_cached_sp1('accumulated-precip', RUN, RUN + timedelta(hours=7))
    np.testing.assert_allclose(field.data, 12.)
    assert len(calls) == 3


@pytest.mark.parametrize('name,value,expected', [
    ('temperature-2m', 18., 18.), ('cloud-cover', 60., 60.),
    ('wind-gust', 12., 12.), ('precip-1h', 9., 9.),
    ('accumulated-precip', 20., 20.),
])
def test_native_maps_use_sp1_without_wcs(monkeypatch, name, value, expected):
    valid = RUN + timedelta(hours=1)
    cached(monkeypatch, {(name, 1): value})
    def no_wcs(*args, **kwargs): pytest.fail('unexpected WCS call')
    monkeypatch.setattr(f, '_product_context', lambda *a: (
        f.PRODUCTS[name], SimpleNamespace(get_field=no_wcs), None, {'field': 'unused'}, RUN, [valid]))
    result, _, _ = f._computed_frame.__wrapped__('token', name, valid.isoformat())
    np.testing.assert_allclose(result.data, expected)
    assert result.units == f.PRODUCTS[name]['unit']


def test_unavailable_sp1_uses_wcs(monkeypatch):
    cached(monkeypatch, {})
    calls = []
    def get(*args, **kwargs):
        calls.append(args)
        return f.RasterField(np.full((2, 2), 280.), *GEOMETRY, 'K')
    valid = RUN + timedelta(hours=1)
    monkeypatch.setattr(f, '_product_context', lambda *a: (
        f.PRODUCTS['temperature-2m'], SimpleNamespace(get_field=get), None, {'field': 'TMP'}, RUN, [valid]))
    result, _, _ = f._computed_frame.__wrapped__('token', 'temperature-2m', valid.isoformat())
    np.testing.assert_allclose(result.data, 6.85)
    assert len(calls) == 1


def test_series_starts_from_requested_sp1_accumulation_without_earlier_downloads(monkeypatch):
    cached(monkeypatch, {('accumulated-precip', 7): 20., ('accumulated-precip', 8): 24.})
    hours = [RUN + timedelta(hours=h) for h in (7, 8)]
    def no_wcs(*a, **k): pytest.fail('unexpected WCS call')
    monkeypatch.setattr(f, '_product_context', lambda *a, **k: (
        f.PRODUCTS['accumulated-precip'], SimpleNamespace(get_field=no_wcs), None,
        {'field': 'unused'}, RUN, hours))
    monkeypatch.setattr(f, '_serialize_grid', lambda product, field, *a: field.data.copy())
    result = list(f.accumulated_precip_series('token', tuple(h.isoformat() for h in hours)))
    assert len(result) == 2
    np.testing.assert_allclose(result[0][1], 20.)
    np.testing.assert_allclose(result[1][1], 24.)


def test_surface_wind_and_temperature_reuse_sp1(monkeypatch):
    cached(monkeypatch, {('surface_u', 1): 3., ('surface_v', 1): 4., ('temperature-2m', 1): 20.})
    f._SURFACE_WIND_CACHE.clear()
    monkeypatch.setattr(f, '_get_uv_height', lambda *a: pytest.fail('unexpected WCS'))
    valid = RUN + timedelta(hours=1)
    u, v = f._surface_wind_10m(None, None, {}, RUN, valid)
    np.testing.assert_allclose(u.data, 3.)
    np.testing.assert_allclose(v.data, 4.)
    assert f._surface_temperature(None, None, {}, RUN, valid).units == 'C'
    f._SURFACE_WIND_CACHE.clear()


@pytest.mark.parametrize('present', [True, False])
def test_theta_e_mslp_uses_sp1_with_independent_fallback(monkeypatch, present):
    valid = RUN + timedelta(hours=1)
    cached(monkeypatch, {('mean_sea_level_pressure', 1): 101325.} if present else {})
    def field(value, unit): return f.RasterField(np.full((2, 2), value), *GEOMETRY, unit)
    monkeypatch.setattr(f, '_theta_e_inputs_from_cached_packages', lambda *a: {
        'temperature': field(280., 'K'), 'dewpoint': field(278., 'K'),
        'surface_pressure': field(95000., 'Pa')})
    calls = []
    def get(*args, **kwargs):
        calls.append(args[1])
        assert args[1] == 'PRMSL'
        return field(101325., 'Pa')
    monkeypatch.setattr(f, '_product_context', lambda *a: (
        f.PRODUCTS['mslp-theta-e-850'], SimpleNamespace(get_field=get), None,
        {'overlay': 'PRMSL'}, RUN, [valid]))
    result, _, _ = f._computed_frame.__wrapped__('token', 'mslp-theta-e-850', valid.isoformat())
    np.testing.assert_allclose(result.overlay, 1013.25)
    assert calls == ([] if present else ['PRMSL'])


def test_wind_map_10m_uses_sp1_vectors(monkeypatch):
    valid = RUN + timedelta(hours=1)
    cached(monkeypatch, {('surface_u', 1): 3., ('surface_v', 1): 4.})
    def fail(*a, **k): pytest.fail('unexpected WCS')
    monkeypatch.setattr(f, '_product_context', lambda *a: (
        f.PRODUCTS['wind-level'], SimpleNamespace(get_field=fail), None,
        {'u': 'U', 'v': 'V'}, RUN, [valid]))
    result, _, _ = f._computed_frame.__wrapped__('token', 'wind-level', valid.isoformat(), level=10.)
    np.testing.assert_allclose(result.data, 5.)
    np.testing.assert_allclose(result.vector_u, 3.)
    np.testing.assert_allclose(result.vector_v, 4.)


def test_series_can_continue_with_wcs_after_sp1_without_double_counting(monkeypatch):
    cached(monkeypatch, {('accumulated-precip', 7): 20., ('accumulated-precip', 9): 29.})
    hours = [RUN + timedelta(hours=h) for h in (7, 8, 9)]
    calls = []
    def get(catalog, prefix, run, valid, *a, **k):
        calls.append(valid)
        return f.RasterField(np.full((2, 2), 4.), *GEOMETRY, 'mm')
    monkeypatch.setattr(f, '_product_context', lambda *a, **k: (
        f.PRODUCTS['accumulated-precip'], SimpleNamespace(get_field=get), None,
        {'field': 'P'}, RUN, hours))
    monkeypatch.setattr(f, '_serialize_grid', lambda product, field, *a: field.data.copy())
    result = list(f.accumulated_precip_series('token', tuple(h.isoformat() for h in hours)))
    for (_, data, _), expected in zip(result, (20., 24., 29.)):
        np.testing.assert_allclose(data, expected)
    assert calls == [hours[1]]


def test_hourly_differences_preserve_masks_and_clip_rounding_noise(monkeypatch):
    cached(monkeypatch, {})
    arrays = {2: np.array([[4., np.nan], [6., 1.999]]),
              1: np.array([[1., 2.], [np.nan, 2.]])}
    monkeypatch.setattr(f, 'read_sp1_field', lambda path, run, valid, name:
                        (arrays[valid.hour - RUN.hour], GEOMETRY, 'mm'))
    result = f._field_from_cached_sp1('precip-1h', RUN, RUN + timedelta(hours=2))
    np.testing.assert_allclose(result.data, [[3., np.nan], [np.nan, 0.]])


def test_different_grids_cannot_be_differenced(monkeypatch):
    cached(monkeypatch, {})
    def read(path, run, valid, name):
        geometry = GEOMETRY if valid.hour == RUN.hour + 2 else (
            from_bounds(0., 40., 2., 41., 2, 2), GEOMETRY[1], GEOMETRY[2])
        return np.full((2, 2), 5.), geometry, 'mm'
    monkeypatch.setattr(f, 'read_sp1_field', read)
    assert f._field_from_cached_sp1('precip-1h', RUN, RUN + timedelta(hours=2)) is None


def test_sparse_accumulations_do_not_download_the_gap(monkeypatch):
    cached(monkeypatch, {('accumulated-precip', 7): 20., ('accumulated-precip', 40): 80.})
    hours = [RUN + timedelta(hours=h) for h in (7, 40)]
    def fail(*a, **k): pytest.fail('must not reconstruct intermediate hours')
    monkeypatch.setattr(f, '_product_context', lambda *a, **k: (
        f.PRODUCTS['accumulated-precip'], SimpleNamespace(get_field=fail), None,
        {'field': 'P'}, RUN, hours))
    monkeypatch.setattr(f, '_serialize_grid', lambda product, field, *a: field.data.copy())
    result = list(f.accumulated_precip_series('token', tuple(h.isoformat() for h in hours)))
    assert len(result) == 2
    np.testing.assert_allclose(result[-1][1], 80.)

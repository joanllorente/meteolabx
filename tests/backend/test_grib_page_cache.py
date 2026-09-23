from pathlib import Path

from server.services import grib_page_cache as cache
from server.services import forecast_store


def setup(monkeypatch, tmp_path, status='complete'):
    monkeypatch.setenv('METEOLABX_AROME_PACKAGE_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(forecast_store, 'get_forecast_store', lambda: None)
    monkeypatch.setattr(forecast_store, 'retained_manifests', lambda _: [
        {'run': '2026-09-12T12:00:00Z', 'status': status}])
    monkeypatch.setattr(cache.threading, 'enumerate', lambda: [])
    advised = []
    monkeypatch.setattr(cache.os, 'POSIX_FADV_DONTNEED', 4, raising=False)
    monkeypatch.setattr(cache.os, 'posix_fadvise', lambda fd, *args: advised.append(args), raising=False)
    return advised


def test_completed_run_preserves_files_and_ignores_other_runs(monkeypatch, tmp_path):
    advised = setup(monkeypatch, tmp_path)
    wanted = tmp_path / 'IP1-20260912T12-00H06H.grib2'
    other = tmp_path / 'IP1-20260912T18-00H06H.grib2'
    wanted.write_bytes(b'grib-input')
    other.write_bytes(b'next-run')
    result = cache.release_completed_grib_cache()
    assert result == {'files_advised': 1, 'file_bytes': 10}
    assert advised == [(0, 0, 4)]
    assert wanted.read_bytes() == b'grib-input'
    assert other.read_bytes() == b'next-run'


def test_unfinished_run_never_discards_pages(monkeypatch, tmp_path):
    advised = setup(monkeypatch, tmp_path, 'publishing')
    assert cache.release_completed_grib_cache() == {'skipped': 'unfinished_runs'}
    assert not advised


def test_prefetch_never_discards_pages(monkeypatch, tmp_path):
    advised = setup(monkeypatch, tmp_path)
    class Thread:
        name = 'arome-prefetch'
        def is_alive(self): return True
    monkeypatch.setattr(cache.threading, 'enumerate', lambda: [Thread()])
    assert cache.release_completed_grib_cache() == {'skipped': 'prefetch_active'}
    assert not advised


def test_a_stuck_old_run_does_not_block_the_completed_one(monkeypatch, tmp_path):
    """Basta con que la pasada del fichero esté completa.

    Exigirlo de todas las conservadas dejaba que una vieja atascada en
    «publishing» bloqueara la liberación para siempre: 7,2 GB de caché de
    páginas durante todo el día, que Railway factura.
    """
    advised = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(forecast_store, 'retained_manifests', lambda _: [
        {'run': '2026-09-12T12:00:00Z', 'status': 'complete'},
        {'run': '2026-09-12T06:00:00Z', 'status': 'publishing'},
    ])
    completa = tmp_path / 'IP1-20260912T12-00H06H.grib2'
    atascada = tmp_path / 'IP1-20260912T06-00H06H.grib2'
    completa.write_bytes(b'grib-input')
    atascada.write_bytes(b'en curso')

    assert cache.release_completed_grib_cache() == {'files_advised': 1, 'file_bytes': 10}
    # La que sigue publicando conserva sus páginas: alguien puede estar leyéndola.
    assert len(advised) == 1
    assert atascada.read_bytes() == b'en curso'


def test_map_pages_only_for_completed_inactive_runs_and_files_survive(monkeypatch, tmp_path):
    advised = setup(monkeypatch, tmp_path)
    store = forecast_store.LocalObjectStore(tmp_path / 'store')
    monkeypatch.setattr(forecast_store, 'get_forecast_store', lambda: store)
    manifests = [
        {'run': '2026-09-12T12:00:00Z', 'status': 'complete'},
        {'run': '2026-09-12T18:00:00Z', 'status': 'publishing'},
        {'run': '2026-09-12T06:00:00Z', 'status': 'complete',
         'progress': {'active_jobs': [{'id': 'running'}]}},
    ]
    monkeypatch.setattr(forecast_store, 'retained_manifests', lambda _: manifests)
    paths = []
    for m in manifests:
        key = forecast_store.frame_key(m['run'], 'temperature-2m', m['run'])
        store.put(key, b'compressed-map', 'application/gzip')
        paths.append(store._path(key))
    result = cache.release_completed_grib_cache()
    assert result['map_files_advised'] == result['files_advised'] == 1
    assert result['map_file_bytes'] == len(b'compressed-map')
    assert len(advised) == 1
    assert all(p.read_bytes() == b'compressed-map' for p in paths)


def test_map_cleanup_respects_scope_and_ignores_temporary_files(monkeypatch, tmp_path):
    advised = setup(monkeypatch, tmp_path)
    store = forecast_store.LocalObjectStore(tmp_path / 'store')
    monkeypatch.setattr(forecast_store, 'get_forecast_store', lambda: store)
    run = '2026-09-12T12:00:00Z'
    monkeypatch.setattr(forecast_store, 'retained_manifests', lambda _: [
        {'run': run, 'status': 'complete', 'calculation_scope': 'peninsula'}])
    for scope in ('model', 'peninsula'):
        key = forecast_store.frame_key(run, 'wind-level', run, scope=scope)
        store.put(key, b'map', 'application/gzip')
    temp = store._path(key).parent / 'unfinished.tmp'
    temp.write_bytes(b'partial')
    assert cache.release_completed_grib_cache()['map_files_advised'] == 1
    assert len(advised) == 1
    assert temp.read_bytes() == b'partial'

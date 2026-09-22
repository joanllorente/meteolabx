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

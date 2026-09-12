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

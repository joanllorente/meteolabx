from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import requests

from server.services import arome_wcs as w, arome_wcs_metrics as metrics
from server.services.arome_models import run_in_model_context, using_model

RUN = '2026-09-30T12:00:00Z'
DATE = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


@pytest.fixture
def measured(monkeypatch, tmp_path):
    monkeypatch.setenv('METEOLABX_AROME_PACKAGE_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(w, '_credential_headers', lambda token: {})
    monkeypatch.setattr(w, '_wait_for_api_request_slot', lambda: None)
    monkeypatch.setattr(w.time, 'sleep', lambda delay: None)
    w.cache_clear()
    yield tmp_path
    w.cache_clear()


def test_attempts_include_retries_and_connection_errors_but_not_cache(measured, monkeypatch):
    responses = iter([503, 429, requests.ConnectionError('offline'), 200])
    monkeypatch.setattr(w, 'API_MAX_ATTEMPTS', 4)
    def get(*a, **kw):
        response = next(responses)
        if isinstance(response, Exception): raise response
        return SimpleNamespace(status_code=response, headers={}, content=b'GRIB', text='')
    monkeypatch.setattr(w.requests, 'get', get)
    with metrics.track_job(RUN, ('temperature-2m',)):
        for _ in range(2):
            assert w._api_get('https://example.invalid/GetCoverage', (), 'secret-token') == (b'GRIB', '')
    stats = metrics.request_stats(DATE)
    assert stats['requests'] == stats['coverage_requests'] == 4
    assert stats['metadata_requests'] == 0
    assert stats['retries'] == 3
    assert stats['by_status'] == {'503': 1, '429': 1, 'connection_error': 1, '200': 1}
    assert stats['without_response'] == 0
    assert stats['by_product'] == {'temperature-2m': 4}
    assert 'secret-token' not in metrics._path(DATE).read_text()
    assert 'example.invalid' not in metrics._path(DATE).read_text()


def test_metadata_disk_cache_and_unscoped_polling_are_not_counted(measured, monkeypatch):
    monkeypatch.setattr(w, '_metadata_cache_path', lambda *a: measured / 'metadata.xml')
    monkeypatch.setattr(w.requests, 'get', lambda *a, **k: SimpleNamespace(
        status_code=200, headers={'Content-Type': 'application/xml'}, content=b'<catalog/>', text=''))
    with metrics.track_job(RUN, ('wind-gust',)):
        for _ in range(2):
            w._api_get_metadata('https://example.invalid/GetCapabilities', (), 'secret')
    w._api_get_sin_cache('https://example.invalid/DescribeCoverage', (), 'secret')
    stats = metrics.request_stats(DATE)
    assert stats['requests'] == stats['metadata_requests'] == 1
    assert stats['by_operation'] == {'GetCapabilities': 1}


def test_threads_inherit_run_and_model_and_restarts_do_not_reset(measured):
    def request(_):
        metrics.request_started('GetCoverage', 0)
        metrics.request_finished('GetCoverage', 200)
    with using_model('arome-ifs'), metrics.track_job(RUN, ('ship', 'stp')):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run_in_model_context(request), range(30)))
    with using_model('arome-ifs'), metrics.track_job(RUN, ('dcape',)):
        request(0)
    with using_model('arome-ifs'):
        stats = metrics.request_stats(DATE)
        assert stats['requests'] == 31
        assert stats['jobs_observed'] == 2
        assert stats['by_product'] == {'ship,stp': 30, 'dcape': 1}
    with using_model('arome'):
        assert metrics.request_stats(DATE) is None


def test_concurrent_processes_append_without_losing_attempts(measured):
    code = '''from server.services.arome_wcs_metrics import track_job, request_started, request_finished
with track_job("2026-09-30T12:00:00Z", ("cloud-cover",)):
    for i in range(12):
        request_started("GetCoverage", 0)
        request_finished("GetCoverage", 200)
'''
    env = {**os.environ, 'METEOLABX_AROME_MODEL': 'arome'}
    children = [subprocess.Popen([sys.executable, '-c', code], env=env) for _ in range(3)]
    for child in children: assert child.wait(timeout=30) == 0
    stats = metrics.request_stats(DATE)
    assert stats['requests'] == stats['by_status']['200'] == 36
    assert stats['jobs_observed'] == 3


def test_zeros_missing_measurements_and_incomplete_events_differ(measured):
    assert metrics.request_stats(DATE) is None
    with metrics.track_job(RUN, ('cloud-cover',)): pass
    assert metrics.request_stats(DATE)['requests'] == 0
    with metrics.track_job(RUN, ('wind-gust',)):
        metrics.request_started('GetCoverage', 0)  # Killed before response.
    with metrics._path(DATE).open('a') as log: log.write('{"partial":')
    stats = metrics.request_stats(DATE)
    assert stats['requests'] == stats['without_response'] == 1


def test_metrics_io_failure_does_not_break_the_request(measured, monkeypatch):
    monkeypatch.setattr(metrics.os, 'open', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    monkeypatch.setattr(w.requests, 'get', lambda *a, **k: SimpleNamespace(
        status_code=200, headers={}, content=b'GRIB', text=''))
    with metrics.track_job(RUN, ('cloud-cover',)):
        assert w._api_get_sin_cache('https://example.invalid/GetCoverage', (), 'secret') == (b'GRIB', '')


def test_worker_reports_routes_separately_from_http_and_preserves_counts(measured, monkeypatch):
    from scripts import forecast_worker as worker
    from server.services.forecast_store import LocalObjectStore
    from server.services import run_report
    monkeypatch.setattr(worker, '_sample_resources', lambda manifest: None)
    monkeypatch.setattr(w.requests, 'get', lambda *a, **k: SimpleNamespace(
        status_code=200, headers={}, content=b'GRIB', text=''))
    def frame(*a, **k):
        return w._api_get('https://example.invalid/GetCoverage', (), 'secret')[0], {}
    monkeypatch.setattr(worker, 'frame_grid', frame)
    manifest = {'run': RUN}
    work = worker.ForecastJob(RUN, '2026-09-30T13:00:00Z', ('cloud-cover',), 'model', 0)
    worker._mark_job_started(manifest, work, 90, mode='wcs_parallel')
    worker._calculate_and_store_job('secret', LocalObjectStore(measured / 'frames'), work)
    worker._mark_job_finished(manifest, work)
    worker._record_downloads(manifest)
    assert manifest['resource_usage']['wcs']['requests'] == 1
    assert manifest['job_routes']['by_mode']['wcs_parallel'] == {'started': 1, 'completed': 1, 'failed': 0}
    assert manifest['job_routes']['partial'] is False
    # Restart/manifest reload, then a failed serialized admission and retry.
    manifest = json.loads(json.dumps(manifest))
    worker._mark_job_started(manifest, work, 90, mode='wcs')
    worker._mark_job_failed(manifest, work, 'HTTP 503')
    worker._mark_job_started(manifest, work, 90, mode='wcs')
    worker._mark_job_finished(manifest, work)
    worker._mark_job_finished(manifest, work)  # Duplicate completion cannot double-count route.
    assert manifest['job_routes']['by_mode']['wcs'] == {'started': 2, 'completed': 1, 'failed': 1}
    report = run_report.build_report(manifest)
    text = run_report.render_text(report)
    assert '1 peticiones reales' in text
    assert 'cola WCS serializada: 2 trabajos admitidos, 1 completados, 1 fallidos' in text
    assert 'WCS paralelo SP1: 1 trabajos admitidos, 1 completados, 0 fallidos' in text


def test_enabling_metrics_mid_run_marks_partial_history(monkeypatch):
    from scripts import forecast_worker as worker
    manifest = {'run': RUN, 'tier_timing': {'0': {'jobs': 10}}}
    work = worker.ForecastJob(RUN, '2026-09-30T13:00:00Z', ('cloud-cover',), 'model', 0)
    worker._mark_job_started(manifest, work, 90, mode='wcs_parallel')
    assert manifest['job_routes']['partial'] is True
    assert manifest['job_routes']['by_mode']['wcs_parallel']['started'] == 1

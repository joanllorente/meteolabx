"""Actual worker HTTP attempts, persisted across threads/processes/restarts.

The context belongs to the calculating job, including metadata requests and
auxiliary coverages from older RUNs. Background catalogue polling and API
visitors are deliberately outside it. No tokens, URLs or query strings are
stored. One small O_APPEND write per event; I/O failures never abort calculation.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_context: ContextVar[tuple[Path, str] | None] = ContextVar('arome_wcs_metrics', default=None)


def _path(run: datetime) -> Path:
    from server.services.arome_packages import _cache_dir
    return _cache_dir() / f'wcs-{run.astimezone(timezone.utc):%Y%m%dT%H}.jsonl'


def _append(event: dict) -> None:
    context = _context.get()
    if context is None:
        return
    path, product = context
    record = {**event, 'product': product, 'at': datetime.now(timezone.utc).isoformat()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(record, separators=(',', ':')) + '\n').encode())
        finally:
            os.close(fd)
    except OSError:
        logger.debug('No se pudo registrar una petición WCS', exc_info=True)


@contextmanager
def track_job(run_iso: str, products: tuple[str, ...]):
    """run_in_model_context copies this context into the profile's threads."""
    run = datetime.fromisoformat(run_iso.replace('Z', '+00:00'))
    token = _context.set((_path(run), ','.join(products)))
    try:
        # A job served completely from packages still produces measured zeros.
        _append({'event': 'job'})
        yield
    finally:
        _context.reset(token)


def request_started(operation: str, attempt: int) -> None:
    # Before requests.get: even a child killed mid-request leaves an attempt.
    _append({'event': 'request', 'operation': operation, 'retry': attempt > 0})


def request_finished(operation: str, status: int | None) -> None:
    _append({'event': 'response', 'operation': operation, 'status': status})


def request_stats(run: datetime) -> dict | None:
    """None means unmeasured, not zero. Cached responses produce no attempts."""
    summary = {'requests': 0, 'retries': 0, 'jobs_observed': 0,
               'by_operation': {}, 'by_product': {}, 'by_status': {}}
    try:
        with _path(run).open(encoding='utf-8') as log:
            for line in log:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # A concurrent/incomplete final line is read next time.
                if not isinstance(event, dict):
                    continue
                kind = event.get('event')
                if kind == 'job':
                    summary['jobs_observed'] += 1
                elif kind == 'request':
                    summary['requests'] += 1
                    summary['retries'] += int(bool(event.get('retry')))
                    for group, key in (('by_operation', event.get('operation', 'unknown')),
                                       ('by_product', event.get('product', 'unknown'))):
                        counts = summary[group]
                        counts[str(key)] = counts.get(str(key), 0) + 1
                elif kind == 'response':
                    key = str(event.get('status') or 'connection_error')
                    counts = summary['by_status']
                    counts[key] = counts.get(key, 0) + 1
                if kind in {'job', 'request', 'response'}:
                    summary.setdefault('observed_since', event.get('at'))
    except OSError:
        return None
    if 'observed_since' not in summary:
        return None
    summary['without_response'] = max(0, summary['requests'] - sum(summary['by_status'].values()))
    summary['coverage_requests'] = summary['by_operation'].get('GetCoverage', 0)
    summary['metadata_requests'] = summary['requests'] - summary['coverage_requests']
    return summary

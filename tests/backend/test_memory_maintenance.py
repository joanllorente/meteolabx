import asyncio
import pytest
from server.services.cache import AsyncTTLCache
from server.services import memory_maintenance as maintenance


@pytest.mark.asyncio
async def test_purge_preserves_grace_and_inflight(monkeypatch):
    monkeypatch.setattr('server.services.cache.time.time', lambda: 100)
    cache = AsyncTTLCache(default_ttl_s=10)
    cache._store.update({'expired': (50, 99, 'old'), 'grace': (50, 110, 'backup'),
                         'fresh': (110, 120, 'new'), 'refresh': (50, 99, 'busy')})
    cache._in_flight['refresh'] = asyncio.get_running_loop().create_future()
    assert await cache.purge_expired() == 1
    assert set(cache._store) == {'grace', 'fresh', 'refresh'}


@pytest.mark.asyncio
async def test_maintenance_reports_before_after(monkeypatch):
    cache = AsyncTTLCache(default_ttl_s=10)
    cache._store['expired'] = (0, 0, 'old')
    monkeypatch.setattr(maintenance, 'LIVE_CACHES', [cache])
    readings = iter([1000, 500])
    monkeypatch.setattr(maintenance, 'anonymous_bytes', lambda: next(readings))
    monkeypatch.setattr(maintenance, 'collect_and_trim', lambda: (3, 1))
    result = await maintenance.maintain_once()
    assert result['purged'] == 1
    assert result['anon_before'] == 1000 and result['anon_after'] == 500


@pytest.mark.asyncio
async def test_loop_can_be_cancelled():
    task = asyncio.create_task(maintenance.maintenance_loop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _proceso(raiz, pid, cmdline, anon_kb):
    carpeta = raiz / str(pid)
    carpeta.mkdir()
    (carpeta / 'cmdline').write_bytes(cmdline.replace(' ', '\0').encode())
    (carpeta / 'status').write_text(f'Name:\tpython\nRssAnon:\t{anon_kb} kB\nRssFile:\t10 kB\n')


def test_container_memory_splits_processes_and_page_cache(tmp_path):
    """La meseta se reparte por proceso y separa la caché de ficheros."""
    proc, cgroup = tmp_path / 'proc', tmp_path / 'cgroup'
    proc.mkdir(); cgroup.mkdir()
    _proceso(proc, 10, 'python -m uvicorn server.main:app', 1024 * 1024)
    _proceso(proc, 11, 'python -m scripts.forecast_worker --watch', 512 * 1024)
    _proceso(proc, 12, 'python -c from multiprocessing.spawn import spawn_main', 256 * 1024)
    _proceso(proc, 13, 'python -c from multiprocessing.spawn import spawn_main', 256 * 1024)
    # Hilo del núcleo: sin línea de órdenes, no cuenta.
    (proc / '2').mkdir()
    (proc / '2' / 'cmdline').write_bytes(b'')
    (proc / '2' / 'status').write_text('Name:\tkthreadd\n')
    (proc / 'self').mkdir()
    gib = 1024 ** 3
    (cgroup / 'memory.current').write_text(str(3 * gib))
    (cgroup / 'memory.stat').write_text(
        f'anon {2 * gib}\nfile {gib}\nactive_file {gib // 4}\ninactive_file {3 * gib // 4}\nshmem 0\n')

    reparto = maintenance.container_memory(proc, cgroup)
    procesos = reparto['processes']
    assert procesos['api'] == {'count': 1, 'anon_mb': 1024.0}
    assert procesos['worker'] == {'count': 1, 'anon_mb': 512.0}
    assert procesos['trabajo'] == {'count': 2, 'anon_mb': 512.0}
    assert reparto['cgroup']['file_mb'] == 1024.0
    assert reparto['cgroup']['anon_mb'] == 2048.0

    linea = maintenance.describe_container_memory(reparto)
    assert 'total 3.00 GB' in linea
    assert 'caché de ficheros 1.00 GB (activa 0.25)' in linea
    assert 'api 1.00 GB' in linea and 'trabajo×2 0.50 GB' in linea
    assert 'shmem' not in linea


def test_container_memory_without_proc_is_none(tmp_path):
    assert maintenance.container_memory(tmp_path / 'no', tmp_path / 'hay') is None
    assert maintenance.describe_container_memory(None) == 'sin datos del contenedor'


def test_growth_by_source_is_opt_in_and_compares_snapshots(monkeypatch):
    """Sin la variable no traza nada; con ella, nombra quién pidió la memoria."""
    monkeypatch.delenv('METEOLABX_MEMORY_TRACE', raising=False)
    assert maintenance.growth_by_source() is None

    monkeypatch.setenv('METEOLABX_MEMORY_TRACE', '1')
    monkeypatch.setattr(maintenance, '_snapshot', None)
    import tracemalloc
    try:
        assert maintenance.growth_by_source() == 'trazando desde ahora'
        retenido = [bytearray(400_000) for _ in range(30)]
        linea = maintenance.growth_by_source()
        assert 'test_memory_maintenance.py:' in linea and 'MB' in linea
        assert len(retenido) == 30
    finally:
        tracemalloc.stop()
        maintenance._snapshot = None


def test_untracked_memory_contrasts_what_is_traced_with_the_rss(monkeypatch):
    """La API creció 1,4 GB y la lista de crecimiento solo sumaba 250 MB.

    Comparar dos instantáneas se pierde lo que sube a goteo: el total
    acumulado frente a la RSS anónima dice de una vez si lo que falta pasa
    por el asignador de Python o no.
    """
    monkeypatch.setenv('METEOLABX_MEMORY_TRACE', '1')
    monkeypatch.setattr(maintenance, 'anonymous_bytes', lambda: 400 * maintenance._MB)
    import tracemalloc
    tracemalloc.start(1)
    retenido = [bytearray(400_000) for _ in range(30)]
    try:
        texto = maintenance.untracked_memory()
    finally:
        tracemalloc.stop()
    assert len(retenido) == 30
    assert 'rastreada 1' in texto  # los 12 MB retenidos, más lo que traiga pytest
    assert 'diferencia RSS anónima−rastreada' in texto
    assert 'metadatos de trazado' in texto
    assert 'test_memory_maintenance.py:' in texto


def test_untracked_memory_is_opt_in(monkeypatch):
    monkeypatch.delenv('METEOLABX_MEMORY_TRACE', raising=False)
    assert maintenance.untracked_memory() is None


def test_untracked_memory_says_nothing_before_tracing_starts(monkeypatch):
    monkeypatch.setenv('METEOLABX_MEMORY_TRACE', '1')
    assert maintenance.untracked_memory() is None


def test_total_diagnostic_reuses_snapshot_and_reports_tracer_overhead(monkeypatch):
    import tracemalloc
    from types import SimpleNamespace

    monkeypatch.setenv('METEOLABX_MEMORY_TRACE', '1')
    monkeypatch.setattr(tracemalloc, 'is_tracing', lambda: True)
    monkeypatch.setattr(tracemalloc, 'get_traced_memory', lambda: (10 * maintenance._MB, 20 * maintenance._MB))
    monkeypatch.setattr(tracemalloc, 'get_tracemalloc_memory', lambda: 3 * maintenance._MB)
    monkeypatch.setattr(maintenance, 'anonymous_bytes', lambda: 40 * maintenance._MB)
    monkeypatch.setattr(maintenance, '_snapshot', SimpleNamespace(statistics=lambda key: []))
    def unexpected_snapshot():
        raise AssertionError('must reuse the existing snapshot')
    monkeypatch.setattr(tracemalloc, 'take_snapshot', unexpected_snapshot)
    text = maintenance.untracked_memory()
    assert 'metadatos de trazado 3 MB' in text
    assert 'diferencia RSS anónima−rastreada 30 MB' in text


@pytest.mark.asyncio
async def test_allocator_samples_bracket_cleanup_and_diagnostics(monkeypatch):
    events = []
    def sample():
        events.append('sample')
        return {'sample_number': events.count('sample')}
    monkeypatch.setattr(maintenance, 'memory_sample', sample)
    monkeypatch.setattr(maintenance, 'LIVE_CACHES', [])
    monkeypatch.setattr(maintenance, 'anonymous_bytes', lambda: 100)
    monkeypatch.setattr(maintenance, '_release_idle_frame_caches', lambda: {})
    monkeypatch.setattr(maintenance, 'collect_and_trim', lambda: (events.append('trim') or 0, 1))
    monkeypatch.setattr(maintenance, 'container_memory', lambda: None)
    monkeypatch.setattr(maintenance, 'growth_by_source', lambda: events.append('snapshot'))
    monkeypatch.setattr(maintenance, 'untracked_memory', lambda: None)
    result = await maintenance.maintain_once()
    assert events == ['sample', 'trim', 'sample', 'snapshot', 'sample']
    assert result['allocator_samples']['after_diagnostics'] == {'sample_number': 3}

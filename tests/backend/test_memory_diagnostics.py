from server.services import memory_diagnostics as d


def test_allocator_uses_aggregate_totals_without_double_counting():
    xml = b'''<malloc version="1"><heap nr="0"><total type="rest" size="1048576"/></heap>
    <heap nr="1"/><total type="fast" size="1048576"/><total type="rest" size="2097152"/>
    <total type="mmap" size="4194304"/><system type="current" size="10485760"/></malloc>'''
    result = d.parse_malloc_info(xml)
    assert result == {'arenas': 2, 'arena_reserved_mib': 10, 'arena_free_bins_mib': 3,
                      'arena_not_in_free_bins_mib': 7, 'malloc_mmap_mib': 4}


def test_process_residency_and_threads(tmp_path):
    (tmp_path / 'smaps_rollup').write_text('address permissions\nRss: 2048 kB\nAnonymous: 1024 kB\nAnonHugePages: 512 kB\nSwap: 0 kB\n')
    (tmp_path / 'status').write_text('Name: python\nThreads: 12\n')
    result = d.process_memory(tmp_path)
    assert result == {'Rss_mib': 2, 'Anonymous_mib': 1, 'AnonHugePages_mib': .5, 'Swap_mib': 0, 'threads': 12}


def test_unavailable_is_not_zero(tmp_path, monkeypatch):
    assert d.process_memory(tmp_path)['smaps_unavailable'] is True
    monkeypatch.setattr(d.sys, 'platform', 'darwin')
    assert d.allocator_memory() == {'unavailable': 'not_linux'}


def test_lightweight_sample_does_not_start_tracing_or_take_snapshots(monkeypatch):
    import tracemalloc
    monkeypatch.setattr(d, 'process_memory', lambda: {'Rss_mib': 42})
    monkeypatch.setattr(d, 'allocator_memory', lambda: {'arenas': 2})
    monkeypatch.setattr(tracemalloc, 'is_tracing', lambda: False)
    def forbidden(*args): raise AssertionError('expensive diagnostic')
    monkeypatch.setattr(tracemalloc, 'start', forbidden)
    monkeypatch.setattr(tracemalloc, 'take_snapshot', forbidden)
    assert d.memory_sample() == {'process': {'Rss_mib': 42}, 'glibc': {'arenas': 2}}

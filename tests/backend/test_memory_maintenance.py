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

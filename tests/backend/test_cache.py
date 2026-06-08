"""
Tests del ``AsyncTTLCache`` y de la integración con los endpoints.

Estructura:
- Unit tests del caché en sí (hit/miss, TTL, coalescing, eviction).
- Integration tests del comportamiento end-to-end (el endpoint solo
  llama UNA vez al proveedor aunque le lleguen N peticiones idénticas).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from server.services.cache import AsyncTTLCache, make_cache_key

from .conftest import WU_OK_OBSERVATION


# =====================================================================
# make_cache_key
# =====================================================================

def test_make_cache_key_includes_provider_kind_station_and_hashed_api_key() -> None:
    key = make_cache_key("WU", "current", "ITEST123", "SECRET_KEY")
    assert key.startswith("wu:current:ITEST123:")
    # Hash de 12 chars hex al final
    suffix = key.split(":")[-1]
    assert len(suffix) == 12
    assert all(c in "0123456789abcdef" for c in suffix)


def test_make_cache_key_does_not_expose_api_key() -> None:
    secret = "S3CR3T_DO_NOT_LEAK"
    key = make_cache_key("WU", "current", "X", secret)
    assert secret not in key


def test_make_cache_key_same_inputs_same_key() -> None:
    a = make_cache_key("WU", "current", "ITEST", "key1")
    b = make_cache_key("WU", "current", "ITEST", "key1")
    assert a == b


def test_make_cache_key_different_api_keys_isolated() -> None:
    a = make_cache_key("WU", "current", "ITEST", "key1")
    b = make_cache_key("WU", "current", "ITEST", "key2")
    assert a != b


def test_make_cache_key_normalizes_station_id_to_uppercase() -> None:
    a = make_cache_key("WU", "current", "itest", "k")
    b = make_cache_key("WU", "current", "ITEST", "k")
    assert a == b


# =====================================================================
# AsyncTTLCache unit tests
# =====================================================================

@pytest.mark.asyncio
async def test_cache_miss_calls_fetcher_and_caches_result() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)
    calls = 0

    async def fetcher() -> str:
        nonlocal calls
        calls += 1
        return "value"

    result = await cache.get_or_fetch("k", fetcher)
    assert result == "value"
    assert calls == 1

    # Segunda llamada: hit
    result2 = await cache.get_or_fetch("k", fetcher)
    assert result2 == "value"
    assert calls == 1  # fetcher NO se llamó otra vez


@pytest.mark.asyncio
async def test_cache_expired_entry_refetches() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=0.05)
    calls = 0

    async def fetcher() -> str:
        nonlocal calls
        calls += 1
        return f"value_{calls}"

    assert await cache.get_or_fetch("k", fetcher) == "value_1"
    await asyncio.sleep(0.1)  # supera el TTL
    assert await cache.get_or_fetch("k", fetcher) == "value_2"
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_ttl_override_per_call() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=100.0)
    calls = 0

    async def fetcher() -> str:
        nonlocal calls
        calls += 1
        return "v"

    # TTL=0.05 explícito (más corto que el default)
    await cache.get_or_fetch("k", fetcher, ttl_s=0.05)
    await asyncio.sleep(0.1)
    await cache.get_or_fetch("k", fetcher, ttl_s=0.05)
    assert calls == 2


@pytest.mark.asyncio
async def test_request_coalescing_n_concurrent_callers_one_fetch() -> None:
    """
    El test estrella: 10 corutinas piden la misma key a la vez con
    caché vacío → el fetcher se invoca **una sola vez**, las otras 9
    esperan el mismo resultado.
    """
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)
    calls = 0
    started = asyncio.Event()
    can_finish = asyncio.Event()

    async def slow_fetcher() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await can_finish.wait()
        return "shared_value"

    # Disparar 10 corutinas a la vez
    tasks = [
        asyncio.create_task(cache.get_or_fetch("k", slow_fetcher))
        for _ in range(10)
    ]

    # Esperar a que el leader haya entrado al fetcher; el resto deben estar
    # esperando su future.
    await started.wait()
    can_finish.set()

    results = await asyncio.gather(*tasks)

    assert all(r == "shared_value" for r in results)
    assert calls == 1, "fetcher debería haberse invocado UNA sola vez"

    stats = cache.stats()
    assert stats["coalesced"] == 9
    assert stats["misses"] == 1


@pytest.mark.asyncio
async def test_fetcher_exception_not_cached_and_propagates() -> None:
    """
    Si el fetcher lanza excepción, NO se cachea. El siguiente intento
    debe reintentar el fetch.
    """
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)
    calls = 0

    async def flaky_fetcher() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return "ok"

    with pytest.raises(RuntimeError, match="boom"):
        await cache.get_or_fetch("k", flaky_fetcher)

    # Reintentar: el fetcher debería ejecutarse OTRA vez (no cacheamos errores).
    result = await cache.get_or_fetch("k", flaky_fetcher)
    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_fetcher_exception_propagates_to_all_followers() -> None:
    """
    Si el fetcher del leader falla, los followers también ven la excepción
    (mejor que recibir un valor stale o quedar colgados para siempre).
    """
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)
    started = asyncio.Event()
    can_finish = asyncio.Event()

    async def failing_fetcher() -> str:
        started.set()
        await can_finish.wait()
        raise ValueError("network down")

    tasks = [
        asyncio.create_task(cache.get_or_fetch("k", failing_fetcher))
        for _ in range(5)
    ]
    await started.wait()
    can_finish.set()

    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, ValueError) for r in results)
    assert all(str(r) == "network down" for r in results)


@pytest.mark.asyncio
async def test_lru_eviction_when_max_entries_exceeded() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=100.0, max_entries=2)

    async def fetcher_for(value: str):
        async def _f() -> str:
            return value
        return _f

    await cache.get_or_fetch("a", await fetcher_for("A"))
    await cache.get_or_fetch("b", await fetcher_for("B"))
    # Tras el siguiente fetch, "a" (la más vieja) debería ser evicted
    await cache.get_or_fetch("c", await fetcher_for("C"))

    stats = cache.stats()
    assert stats["entries"] == 2

    # "a" ya no está en caché: re-fetch hace miss y llama de nuevo
    refetch_calls = 0

    async def f_a() -> str:
        nonlocal refetch_calls
        refetch_calls += 1
        return "A_refetched"

    assert await cache.get_or_fetch("a", f_a) == "A_refetched"
    assert refetch_calls == 1


@pytest.mark.asyncio
async def test_different_keys_are_isolated() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)

    async def f1() -> str:
        return "one"

    async def f2() -> str:
        return "two"

    assert await cache.get_or_fetch("k1", f1) == "one"
    assert await cache.get_or_fetch("k2", f2) == "two"
    # Cada uno mantiene su valor
    assert await cache.get_or_fetch("k1", f2) == "one"  # f2 no se invoca porque k1 hit


@pytest.mark.asyncio
async def test_invalidate_returns_true_for_existing_and_false_for_missing() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)

    async def fetcher() -> str:
        return "v"

    await cache.get_or_fetch("k", fetcher)
    assert cache.invalidate("k") is True
    assert cache.invalidate("k") is False
    # Tras invalidar, próxima petición es miss
    calls = 0

    async def counting_fetcher() -> str:
        nonlocal calls
        calls += 1
        return "v2"

    await cache.get_or_fetch("k", counting_fetcher)
    assert calls == 1


def test_clear_empties_cache() -> None:
    cache: AsyncTTLCache[str] = AsyncTTLCache(default_ttl_s=10.0)
    # Acceso directo a _store solo para test del clear
    cache._store["a"] = (time.time() + 100, "A")
    cache._store["b"] = (time.time() + 100, "B")
    cache.clear()
    assert cache.stats()["entries"] == 0


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        AsyncTTLCache(default_ttl_s=0)
    with pytest.raises(ValueError):
        AsyncTTLCache(default_ttl_s=-1)
    with pytest.raises(ValueError):
        AsyncTTLCache(default_ttl_s=1, max_entries=0)


# =====================================================================
# Integración: el endpoint usa el caché
# =====================================================================

def test_endpoint_current_caches_provider_call(app_factory) -> None:
    """
    Dos peticiones idénticas al endpoint dentro del TTL deben generar
    UNA SOLA llamada al transporte HTTP del proveedor (WU).
    """
    # Wrappeamos el handler del MockTransport con un contador.
    call_count = 0

    def counting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=WU_OK_OBSERVATION)

    import httpx as _httpx
    from server.dependencies.http import get_http_client
    from server.main import create_app
    from fastapi.testclient import TestClient

    counting_client = _httpx.AsyncClient(transport=_httpx.MockTransport(counting_handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: counting_client

    with TestClient(app) as client:
        r1 = client.post("/v1/observations/current",
                         json={"provider": "WU", "station_id": "X", "api_key": "Y"})
        r2 = client.post("/v1/observations/current",
                         json={"provider": "WU", "station_id": "X", "api_key": "Y"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert call_count == 1, f"Esperaba 1 llamada a WU, hubo {call_count}"


def test_endpoint_current_different_api_keys_dont_share_cache(app_factory) -> None:
    """
    Aislamiento por usuario: misma estación con DISTINTAS API keys
    generan llamadas independientes (no comparten caché).
    """
    call_count = 0

    def counting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=WU_OK_OBSERVATION)

    import httpx as _httpx
    from server.dependencies.http import get_http_client
    from server.main import create_app
    from fastapi.testclient import TestClient

    counting_client = _httpx.AsyncClient(transport=_httpx.MockTransport(counting_handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: counting_client

    with TestClient(app) as client:
        client.post("/v1/observations/current",
                    json={"provider": "WU", "station_id": "X", "api_key": "alice"})
        client.post("/v1/observations/current",
                    json={"provider": "WU", "station_id": "X", "api_key": "bob"})

    assert call_count == 2, f"Esperaba 2 llamadas, hubo {call_count}"


def test_endpoint_current_provider_error_not_cached(app_factory) -> None:
    """
    Si el primer intento falla con 401, el siguiente intento NO debe
    devolver el error cacheado: debe reintentar (errores no se cachean).
    """
    call_count = 0
    next_status = 401

    def variable_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json=WU_OK_OBSERVATION)

    import httpx as _httpx
    from server.dependencies.http import get_http_client
    from server.main import create_app
    from fastapi.testclient import TestClient

    variable_client = _httpx.AsyncClient(transport=_httpx.MockTransport(variable_handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: variable_client

    with TestClient(app) as client:
        r1 = client.post("/v1/observations/current",
                         json={"provider": "WU", "station_id": "X", "api_key": "Y"})
        r2 = client.post("/v1/observations/current",
                         json={"provider": "WU", "station_id": "X", "api_key": "Y"})

    assert r1.status_code == 401
    assert r2.status_code == 200  # NO cacheado el error
    assert call_count == 2

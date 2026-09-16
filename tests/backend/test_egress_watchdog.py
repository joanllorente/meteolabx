"""Vigilante de la salida a internet."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from server.config import Settings
from server.dependencies import http as http_dependencies
from server.services import egress_watchdog


def _watchdog(monkeypatch, *, shared: list[bool], independent: bool, restarts: list[int]):
    outcomes = iter(shared)

    async def fake_shared(client, url):
        return next(outcomes)

    monkeypatch.setattr(egress_watchdog, "probe_shared", fake_shared)
    monkeypatch.setattr(egress_watchdog, "probe_independent", lambda url: independent)
    return egress_watchdog.EgressWatchdog(
        httpx.AsyncClient(),
        url="https://probe.test",
        failures_before_check=3,
        restart=lambda: restarts.append(1),
    )


@pytest.mark.asyncio
async def test_restarts_when_only_the_shared_client_is_stuck(monkeypatch) -> None:
    restarts: list[int] = []
    watchdog = _watchdog(monkeypatch, shared=[False, False, False], independent=True, restarts=restarts)
    assert [await watchdog.tick() for _ in range(3)] == [False, False, True]
    assert restarts == [1]


@pytest.mark.asyncio
async def test_does_not_restart_during_a_network_outage(monkeypatch) -> None:
    restarts: list[int] = []
    watchdog = _watchdog(monkeypatch, shared=[False] * 4, independent=False, restarts=restarts)
    assert [await watchdog.tick() for _ in range(4)] == [False] * 4
    assert restarts == []


@pytest.mark.asyncio
async def test_a_successful_probe_resets_the_count(monkeypatch) -> None:
    restarts: list[int] = []
    watchdog = _watchdog(
        monkeypatch, shared=[False, False, True, False, False], independent=True, restarts=restarts,
    )
    for _ in range(5):
        await watchdog.tick()
    assert watchdog.failures == 2
    assert restarts == []


@pytest.mark.asyncio
async def test_shared_probe_accepts_any_answer_but_not_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down.test":
            raise httpx.ConnectTimeout("sin conexión", request=request)
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await egress_watchdog.probe_shared(client, "https://up.test") is True
        assert await egress_watchdog.probe_shared(client, "https://down.test") is False


@pytest.mark.asyncio
async def test_pool_state_reads_a_real_client() -> None:
    async with httpx.AsyncClient() as client:
        state = egress_watchdog.pool_state(client)
    assert state["connections"] == 0 and state["queued"] == 0


@pytest.mark.asyncio
async def test_lifespan_starts_watchdog_only_when_enabled(monkeypatch) -> None:
    started = asyncio.Event()

    async def fake_loop(watchdog, *, interval_s):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(egress_watchdog, "watchdog_loop", fake_loop)

    for enabled, expected in ((False, False), (True, True)):
        started.clear()
        settings = Settings(
            _env_file=None, ranking_refresh_enabled=False, egress_watchdog_enabled=enabled,
        )
        monkeypatch.setattr(http_dependencies, "get_settings", lambda: settings)
        from fastapi import FastAPI

        async with http_dependencies.http_client_lifespan(FastAPI()):
            await asyncio.sleep(0.01)
            assert started.is_set() is expected

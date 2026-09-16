"""Tope de consultas en vivo simultáneas."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from server.dependencies.live_limit import LiveRequestLimiter


def _app(release: asyncio.Event, entered: asyncio.Event) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/observations/current/processed")
    async def slow_observation() -> dict:
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.get("/v1/health")
    async def health() -> dict:
        return {"ok": True}

    app.add_middleware(LiveRequestLimiter, api_prefix="/v1", max_concurrent=1, queue_timeout_s=0.05)
    return app


@pytest.mark.asyncio
async def test_rejects_live_request_when_full_and_leaves_others_alone() -> None:
    release, entered = asyncio.Event(), asyncio.Event()
    transport = httpx.ASGITransport(app=_app(release, entered))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.post("/v1/observations/current/processed"))
        await asyncio.wait_for(entered.wait(), 1.0)

        busy = await client.post("/v1/observations/current/processed")
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "30"
        assert busy.json()["error_code"] == "backend_busy"

        # Lo que no es dato en vivo no pasa por el tope.
        assert (await client.get("/v1/health")).status_code == 200

        release.set()
        assert (await first).status_code == 200
        # El hueco se devuelve al terminar.
        assert (await client.post("/v1/observations/current/processed")).status_code == 200


@pytest.mark.asyncio
async def test_waits_for_a_slot_within_the_queue_timeout() -> None:
    release, entered = asyncio.Event(), asyncio.Event()
    app = FastAPI()

    @app.post("/v1/climo/summary")
    async def summary() -> dict:
        entered.set()
        await release.wait()
        return {"ok": True}

    app.add_middleware(LiveRequestLimiter, api_prefix="/v1", max_concurrent=1, queue_timeout_s=2.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.post("/v1/climo/summary"))
        await asyncio.wait_for(entered.wait(), 1.0)
        second = asyncio.create_task(client.post("/v1/climo/summary"))
        await asyncio.sleep(0.05)
        release.set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200


def test_backend_app_mounts_the_limiter() -> None:
    from server.main import create_app

    assert any(m.cls is LiveRequestLimiter for m in create_app().user_middleware)

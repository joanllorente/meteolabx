"""Arranque de recursos compartidos sin gastar cuotas del ranking."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from server.config import Settings
from server.dependencies import http as http_dependencies
from server.services import ranking


@pytest.mark.asyncio
async def test_lifespan_does_not_start_ranking_when_disabled(monkeypatch) -> None:
    settings = Settings(_env_file=None, ranking_refresh_enabled=False)
    monkeypatch.setattr(http_dependencies, "get_settings", lambda: settings)

    async def unexpected_refresh(*args, **kwargs) -> None:
        raise AssertionError("El ranking no debe arrancar en modo local/test")

    monkeypatch.setattr(ranking, "refresh_loop", unexpected_refresh)
    app = FastAPI()

    async with http_dependencies.http_client_lifespan(app):
        # El store sigue disponible para endpoints y snapshots, aunque el job
        # que llama a proveedores esté apagado.
        assert app.state.ranking_refresh_enabled is False
        assert isinstance(app.state.ranking_store, ranking.RankingStore)


@pytest.mark.asyncio
async def test_lifespan_can_enable_ranking_explicitly(monkeypatch) -> None:
    settings = Settings(_env_file=None, ranking_refresh_enabled=True)
    monkeypatch.setattr(http_dependencies, "get_settings", lambda: settings)
    started = asyncio.Event()

    async def fake_refresh(*args, **kwargs) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ranking, "refresh_loop", fake_refresh)
    app = FastAPI()

    async with http_dependencies.http_client_lifespan(app):
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert app.state.ranking_refresh_enabled is True

"""Smoke test del endpoint /v1/health."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import __version__
from server.main import create_app


def test_health_returns_ok() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "version": __version__,
        "api_version": "v1",
    }

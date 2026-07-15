"""
Tests de ``/v1/health/providers`` y ``/v1/diagnostics`` (métricas en
memoria por proveedor + stats de caché).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app
from server.services import metrics

from tests.backend.test_meteogalicia_service import HOURLY_PAYLOAD, TENMIN_PAYLOAD


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_health_providers_reports_credentials_status() -> None:
    app = create_app()
    def _settings() -> Settings:
        settings = Settings(
            aemet_api_key="X", frost_client_id="A", frost_client_secret="B",
        )
        # Bypass del fallback legacy para tener un proveedor "missing".
        settings.metoffice_api_key = ""
        settings.windy_api_key = "W"
        return settings

    app.dependency_overrides[get_settings] = _settings
    with TestClient(app) as client:
        response = client.get("/v1/health/providers")

    assert response.status_code == 200
    body = response.json()
    assert body["AEMET"]["credentials"] == "configured"
    assert body["METOFFICE"]["credentials"] == "missing"
    assert body["FROST"]["credentials"] == "configured"
    assert body["WU"]["credentials"] == "per_user"
    assert body["NWS"]["credentials"] == "public"
    assert body["WINDY"]["credentials"] == "configured"
    # Sin tráfico todavía
    assert body["AEMET"]["calls"] == 0
    assert body["AEMET"]["last_error"] is None


def test_diagnostics_tracks_calls_cache_and_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "ultimos10min" in path:
            return httpx.Response(200, json=TENMIN_PAYLOAD)
        if "ultimosHorarios" in path:
            return httpx.Response(200, json=HOURLY_PAYLOAD)
        return httpx.Response(404, json={})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock
    app.dependency_overrides[get_settings] = lambda: Settings(meteocat_api_key="test-key")

    with TestClient(app) as client:
        # 2 peticiones idénticas: la segunda debe ser hit de caché
        for _ in range(2):
            ok = client.post(
                "/v1/observations/current",
                json={"provider": "METEOGALICIA", "station_id": "10045", "api_key": ""},
            )
            assert ok.status_code == 200

        # Un error: el mock devuelve 404 para los endpoints Meteocat →
        # station_not_found
        err = client.post(
            "/v1/observations/current",
            json={"provider": "METEOCAT", "station_id": "C6", "api_key": ""},
        )
        assert err.status_code == 404

        diag = client.get("/v1/diagnostics").json()
        health = client.get("/v1/health/providers").json()

    # Caché: 2 misses (fetch MG OK + fetch Meteocat fallido, que no se
    # cachea) y 1 hit (la segunda petición MG)
    assert diag["caches"]["current"]["misses"] == 2
    assert diag["caches"]["current"]["hits"] == 1

    # Proveedores: 1 llamada real OK a METEOGALICIA
    mg = diag["providers"]["METEOGALICIA"]
    assert mg["calls"] == 1
    assert mg["errors"] == 0
    assert mg["last_ok_epoch"] is not None

    # METEOCAT: error registrado con código estable
    mc = health["METEOCAT"]
    assert mc["errors"] == 1
    assert mc["last_error"]["error_code"] == "station_not_found"

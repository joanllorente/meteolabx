"""
Tests de los endpoints de observación con ``provider="METEOCAT"``.

Cubren el wiring router → servicio (dispatch, settings, mapeo de
errores HTTP); la lógica de parsing/normalización vive en
``test_meteocat_service.py``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app

from tests.backend.test_meteocat_service import ELEVATION, STATION

CAT_TZ = ZoneInfo("Europe/Madrid")


def _today_payload() -> list:
    """
    Payload del endpoint de día con lecturas fechadas HOY (hora local
    catalana). Los endpoints reales usan el día local en curso, así que
    el payload debe ser relativo a la fecha de ejecución del test.
    """
    day = datetime.now(CAT_TZ).strftime("%Y-%m-%d")
    return [
        {
            "codi": STATION,
            "variables": [
                {"codi": 32, "lectures": [
                    {"data": f"{day}T00:00", "valor": 22.0},
                    {"data": f"{day}T00:30", "valor": 23.5},
                ]},
                {"codi": 33, "lectures": [{"data": f"{day}T00:30", "valor": 55.0}]},
                {"codi": 34, "lectures": [{"data": f"{day}T00:30", "valor": 985.0}]},
                {"codi": 30, "lectures": [{"data": f"{day}T00:30", "valor": 5.0}]},
                {"codi": 50, "lectures": [{"data": f"{day}T00:30", "valor": 10.0}]},
                {"codi": 31, "lectures": [{"data": f"{day}T00:30", "valor": 180.0}]},
                {"codi": 40, "lectures": [{"data": f"{day}T00:30", "valor": 25.1}]},
                {"codi": 42, "lectures": [{"data": f"{day}T00:00", "valor": 19.8}]},
                {"codi": 35, "lectures": [
                    {"data": f"{day}T00:00", "valor": 0.3},
                    {"data": f"{day}T00:30", "valor": 0.2},
                ]},
            ],
        }
    ]


def _make_app_client(
    *,
    meteocat_api_key: str = "SERVER_KEY",
    day_status: int = 200,
    day_payload: Optional[list] = None,
    request_paths: Optional[list[str]] = None,
) -> TestClient:
    """App con settings y transporte HTTP mockeados para METEOCAT."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/estacions/mesurades/" in request.url.path:
            if request_paths is not None:
                request_paths.append(request.url.path)
            return httpx.Response(day_status, json=day_payload or _today_payload())
        return httpx.Response(404, json={})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock_client
    def _settings() -> Settings:
        settings = Settings(meteocat_api_key=meteocat_api_key)
        # Bypass del fallback legacy para poder simular "sin key".
        settings.meteocat_api_key = meteocat_api_key
        return settings

    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


def test_meteocat_current_uses_server_key_and_returns_canonical() -> None:
    with _make_app_client() as client:
        response = client.post(
            "/v1/observations/current",
            # api_key del body se ignora (key de servidor, como AEMET)
            json={"provider": "METEOCAT", "station_id": "c6", "api_key": ""},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["Tc"] == pytest.approx(23.5)
    assert body["wind"] == pytest.approx(18.0)  # km/h
    assert body["p_hpa"] == pytest.approx(985.0 * math.exp(ELEVATION / 8000.0))
    # station_id normalizado a mayúsculas por el schema
    assert body["epoch"] > 0


def test_meteocat_current_without_server_key_is_401() -> None:
    with _make_app_client(meteocat_api_key="") as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "METEOCAT", "station_id": STATION, "api_key": ""},
        )
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "provider_unauthorized"
    assert body["provider"] == "METEOCAT"


def test_meteocat_series_today_normalized() -> None:
    with _make_app_client() as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "METEOCAT", "station_id": STATION, "api_key": ""},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert len(body["epochs"]) == 2
    assert body["winds"][-1] == pytest.approx(18.0)
    # Huecos como null (JSON), no NaN
    assert body["pressures"][0] is None


def test_meteocat_current_processed_runs_pipeline() -> None:
    request_paths: list[str] = []
    with _make_app_client(request_paths=request_paths) as client:
        payload = {
            "provider": "METEOCAT",
            "station_id": STATION,
            "api_key": "",
            "sun_tz_name": "Europe/Madrid",
        }
        response = client.post(
            "/v1/observations/current/processed",
            json=payload,
        )
        cached_response = client.post("/v1/observations/current/processed", json=payload)
    assert response.status_code == 200
    body = response.json()
    observation = body["observation"]
    derivatives = body["derivatives"]
    assert observation["Tc"] == pytest.approx(23.5)
    # El pipeline usa la absoluta nativa de Meteocat (sin glue MSL→abs)
    assert derivatives["p_abs"] == pytest.approx(985.0)
    assert derivatives["z"] == pytest.approx(ELEVATION)
    assert derivatives["Tw"] is not None  # termodinámica calculada
    assert body["daily_extremes"]["temp_max"] == pytest.approx(25.1)
    assert body["daily_extremes"]["temp_min"] == pytest.approx(19.8)
    assert body["daily_extremes"]["precip_total"] == pytest.approx(0.5)
    # Primera conexión: una petición por cada fecha UTC que cubre el día local.
    # No se repiten para construir current y serie por separado, y la siguiente
    # conexión a la misma estación entra íntegramente por caché.
    assert cached_response.status_code == 200
    assert len(request_paths) == 2


def test_meteocat_current_processed_prefers_current_day_ranking_extremes() -> None:
    from server.services.ranking import StationDaily

    request_paths: list[str] = []
    with _make_app_client(request_paths=request_paths) as client:
        client.app.state.ranking_store.replace_daily(
            "METEOCAT",
            [
                StationDaily(
                    provider="METEOCAT",
                    station_id=STATION,
                    name="Test Meteocat",
                    tmax=27.4,
                    tmin=17.2,
                    gust=48.6,
                    rain=6.7,
                )
            ],
        )
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "METEOCAT",
                "station_id": STATION,
                "api_key": "",
                "sun_tz_name": "Europe/Madrid",
            },
        )

    assert response.status_code == 200
    extremes = response.json()["daily_extremes"]
    assert extremes["temp_max"] == pytest.approx(27.4)
    assert extremes["temp_min"] == pytest.approx(17.2)
    assert extremes["gust_max"] == pytest.approx(48.6)
    assert extremes["precip_total"] == pytest.approx(6.7)
    assert len(request_paths) == 2


def test_meteocat_current_processed_ignores_stale_ranking_day() -> None:
    from server.services.ranking import StationDaily

    with _make_app_client() as client:
        client.app.state.ranking_store.replace_daily(
            "METEOCAT",
            [
                StationDaily(
                    provider="METEOCAT",
                    station_id=STATION,
                    name="Stale Meteocat",
                    tmax=99.0,
                    tmin=-99.0,
                    gust=199.0,
                    rain=999.0,
                    local_date="2000-01-01",
                )
            ],
        )
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "METEOCAT", "station_id": STATION, "api_key": ""},
        )

    assert response.status_code == 200
    extremes = response.json()["daily_extremes"]
    assert extremes["temp_max"] == pytest.approx(25.1)
    assert extremes["temp_min"] == pytest.approx(19.8)
    assert extremes["gust_max"] == pytest.approx(36.0)
    assert extremes["precip_total"] == pytest.approx(0.5)


def test_meteocat_provider_error_maps_to_http_status() -> None:
    with _make_app_client(day_status=429) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "METEOCAT", "station_id": STATION, "api_key": ""},
        )
    assert response.status_code == 429
    assert response.json()["error_code"] == "provider_ratelimit"

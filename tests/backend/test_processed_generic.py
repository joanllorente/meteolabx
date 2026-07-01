"""
Garantía de que ``/current/processed`` es genérico: el dispatch único
(`_resolve_provider_fetchers`) hace que cualquier proveedor con
current+series soporte el pipeline completo, no solo WU/AEMET.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app
from server.schemas.errors import ProviderError

from tests.backend.test_meteogalicia_service import DAILY_PAYLOAD, HOURLY_PAYLOAD, TENMIN_PAYLOAD


def _meteogalicia_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "ultimos10min" in path:
            return httpx.Response(200, json=TENMIN_PAYLOAD)
        if "ultimosHorarios" in path:
            return httpx.Response(200, json=HOURLY_PAYLOAD)
        if "datosDiarios" in path:
            return httpx.Response(200, json=DAILY_PAYLOAD)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def test_processed_works_for_non_wu_aemet_provider() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = _meteogalicia_client
    app.dependency_overrides[get_settings] = lambda: Settings()

    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "METEOGALICIA",
                "station_id": "10045",
                "api_key": "",
                "sun_tz_name": "Europe/Madrid",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["observation"]["Tc"] == pytest.approx(21.5)
    # Pipeline completo ejecutado: termodinámica + serie del día
    derivatives = body["derivatives"]
    assert derivatives["p_abs"] == pytest.approx(1002.0)
    assert derivatives["Tw"] is not None
    assert derivatives["theta"] is not None
    assert derivatives["has_chart_data"] is True


def test_aemet_processed_falls_back_to_ten_minute_series(monkeypatch) -> None:
    """Un fallo transitorio de current no inutiliza una serie válida."""
    from server.routers import observations

    async def fail_current(*args, **kwargs):
        raise ProviderError(
            "provider_network_error", provider="AEMET", detail="temporary",
        )

    async def valid_series(*args, **kwargs):
        return {
            "epochs": [1717254600, 1717255200],
            "temps": [21.5, 22.0],
            "humidities": [66.0, 65.0],
            "dewpts": [15.0, 15.1],
            "pressures": [1012.0, 1013.0],
            "winds": [10.0, 12.0],
            "gusts": [18.0, 20.0],
            "wind_dirs": [170.0, 180.0],
            "uv_indexes": [],
            "solar_radiations": [],
            "has_data": True,
        }

    monkeypatch.setattr(observations.aemet, "fetch_current", fail_current)
    monkeypatch.setattr(observations.aemet, "fetch_today_series", valid_series)

    app = create_app()
    app.dependency_overrides[get_http_client] = _meteogalicia_client
    app.dependency_overrides[get_settings] = lambda: Settings(aemet_api_key="K")

    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "AEMET", "station_id": "3130C", "api_key": ""},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["observation"]["epoch"] == 1717255200
    assert body["observation"]["Tc"] == pytest.approx(22.0)
    assert body["observation"]["p_hpa"] == pytest.approx(1013.0)
    assert body["series"]["pressures_abs"]


def test_processed_includes_station_extremes_and_series() -> None:
    """Contrato unificado: /processed = dashboard payload completo."""
    app = create_app()
    app.dependency_overrides[get_http_client] = _meteogalicia_client
    app.dependency_overrides[get_settings] = lambda: Settings()

    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "METEOGALICIA", "station_id": "10045", "api_key": ""},
        )

    assert response.status_code == 200
    body = response.json()

    # Bloque station: catálogo del backend con sensors
    station = body["station"]
    assert station["provider"] == "METEOGALICIA"
    assert station["name"] == "Mabegondo"
    assert station["elevation"] == pytest.approx(94.0)
    assert station["tz"] == "Europe/Madrid"
    assert isinstance(station["sensors"], dict)
    assert station["sensors"].get("thermometer") is True

    # Bloque daily_extremes: resumen diario oficial de MeteoGalicia.
    extremes = body["daily_extremes"]
    assert extremes["temp_max"] == pytest.approx(23.2)
    assert extremes["temp_min"] == pytest.approx(17.16)
    assert extremes["rh_max"] == pytest.approx(96.0)
    assert extremes["rh_min"] == pytest.approx(41.0)
    assert extremes["gust_max"] == pytest.approx(43.2)
    assert extremes["precip_total"] == pytest.approx(0.6)

    # Bloque series: la serie del día viaja en la misma respuesta
    series = body["series"]
    assert series["has_data"] is True
    assert len(series["epochs"]) == 2


def test_processed_pressure_falls_back_to_series(monkeypatch) -> None:
    """
    Si el current llega sin presión (pasa a ratos en WU) pero la serie
    del día la trae, /processed usa el último punto válido de la serie
    para que la card no muestre "—" mientras Tendencias sí la pinta.
    """
    import httpx

    current_no_pressure = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 65, "winddir": 180,
            "lat": 41.387, "lon": 2.169, "elev": 12.0,
            "metric": {"temp": 22.0, "windSpeed": 8.0},  # sin pressure
        }]
    }
    series_with_pressure = {
        "observations": [
            {
                "epoch": 1717252000, "humidityAvg": 64, "winddirAvg": 180,
                "lat": 41.387, "lon": 2.169,
                "metric": {"tempAvg": 21.0, "pressureMax": 1012.5},
            },
            {
                "epoch": 1717255200, "humidityAvg": 65, "winddirAvg": 180,
                "lat": 41.387, "lon": 2.169,
                "metric": {"tempAvg": 22.0, "pressureMax": 1013.0},
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "observations/current" in request.url.path:
            return httpx.Response(200, json=current_no_pressure)
        return httpx.Response(200, json=series_with_pressure)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K"},
        )

    assert response.status_code == 200
    derivatives = response.json()["derivatives"]
    assert derivatives["p_msl"] == pytest.approx(1013.0)
    assert derivatives["p_abs"] is not None
    assert derivatives["p_abs_disp"] != "—"


def test_processed_trend_computed_from_canonical_series() -> None:
    """
    La tendencia 3h debe calcularse en el backend desde la serie
    canónica (pressures MSL → pressures_abs reconstruidas) y la
    etiqueta/flecha deben ser coherentes con dp3.
    """
    import httpx

    current = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 65, "winddir": 180,
            "lat": 41.387, "lon": 2.169, "elev": 12.0,
            "metric": {"temp": 22.0, "pressure": 1013.0, "windSpeed": 8.0},
        }]
    }
    series = {
        "observations": [
            {
                "epoch": 1717244400,  # ~3h antes
                "humidityAvg": 64, "winddirAvg": 180,
                "lat": 41.387, "lon": 2.169,
                "metric": {"tempAvg": 21.0, "pressureMax": 1011.0},
            },
            {
                "epoch": 1717255200,
                "humidityAvg": 65, "winddirAvg": 180,
                "lat": 41.387, "lon": 2.169,
                "metric": {"tempAvg": 22.0, "pressureMax": 1013.0},
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "observations/current" in request.url.path:
            return httpx.Response(200, json=current)
        return httpx.Response(200, json=series)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K"},
        )

    derivatives = response.json()["derivatives"]
    assert derivatives["dp3"] == pytest.approx(2.0, abs=0.1)
    # Coherencia número↔etiqueta: 2 hPa en 3h = subida débil
    assert derivatives["p_label"] == "Subida débil"
    assert derivatives["p_arrow"] == "↗"


def test_wu_processed_applies_calibration_before_extremes_and_derivatives() -> None:
    """La calibración WU es autoritativa en todo el payload /processed."""
    current = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 50,
            "winddir": 180,
            "lat": 41.387,
            "lon": 2.169,
            "elev": 20.0,
            "solarRadiation": 400.0,
            "metric": {
                "temp": 20.0,
                "pressure": 1010.0,
                "windSpeed": 10.0,
                "windGust": 20.0,
                "precipTotal": 2.0,
            },
        }]
    }
    series = {
        "observations": [{
            "epoch": 1717251600,
            "humidityAvg": 48,
            "humidityHigh": 70,
            "humidityLow": 40,
            "winddirAvg": 170,
            "solarRadiation": 350.0,
            "metric": {
                "tempAvg": 18.0,
                "tempHigh": 24.0,
                "tempLow": 12.0,
                "pressureMax": 1009.0,
                "windSpeedAvg": 8.0,
                "windgustHigh": 25.0,
            },
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = current if "observations/current" in request.url.path else series
        return httpx.Response(200, json=payload)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "WU",
                "station_id": "ITEST",
                "api_key": "K",
                "calibration": {
                    "thermometer": 2.0,
                    "hygrometer": 5.0,
                    "barometer": 3.0,
                    "anemometer": 4.0,
                    "wind_vane": 10.0,
                    "pyranometer": 100.0,
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    observation = body["observation"]
    assert observation["Tc"] == pytest.approx(22.0)
    assert observation["RH"] == pytest.approx(55.0)
    assert observation["p_hpa"] == pytest.approx(1013.0)
    assert observation["wind"] == pytest.approx(14.0)
    assert observation["gust"] == pytest.approx(24.0)
    assert observation["wind_dir_deg"] == pytest.approx(190.0)
    assert observation["solar_radiation"] == pytest.approx(500.0)

    assert body["series"]["temps"] == [pytest.approx(20.0)]
    assert body["series"]["humidities"] == [pytest.approx(53.0)]
    assert body["series"]["pressures"] == [pytest.approx(1012.0)]
    assert body["series"]["winds"] == [pytest.approx(12.0)]
    assert body["series"]["gusts"] == [pytest.approx(29.0)]

    extremes = body["daily_extremes"]
    assert extremes["temp_max"] == pytest.approx(26.0)
    assert extremes["temp_min"] == pytest.approx(14.0)
    assert extremes["rh_max"] == pytest.approx(75.0)
    assert extremes["rh_min"] == pytest.approx(45.0)
    assert extremes["gust_max"] == pytest.approx(29.0)
    assert body["derivatives"]["p_msl"] == pytest.approx(1013.0)


def test_wu_processed_detects_sensors_from_series() -> None:
    """WU no tiene catálogo: el backend detecta los sensores (capacidades
    canónicas) a partir de la observación + serie del día."""
    current = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 50,
            "winddir": 180,
            "lat": 41.387,
            "lon": 2.169,
            "elev": 20.0,
            "solarRadiation": 400.0,
            "metric": {"temp": 20.0, "pressure": 1010.0, "windSpeed": 10.0, "windGust": 20.0},
        }]
    }
    series = {
        "observations": [{
            "epoch": 1717251600,
            "humidityAvg": 48,
            "winddirAvg": 170,
            "solarRadiation": 350.0,
            "metric": {
                "tempAvg": 18.0, "pressureMax": 1009.0,
                "windSpeedAvg": 8.0, "windgustHigh": 25.0,
            },
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = current if "observations/current" in request.url.path else series
        return httpx.Response(200, json=payload)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K"},
        )

    assert response.status_code == 200
    sensors = response.json()["station"]["sensors"]
    assert isinstance(sensors, dict)
    # Presentes en la serie/observación → True
    for capability in (
        "thermometer", "hygrometer", "barometer",
        "anemometer", "wind_vane", "pyranometer",
    ):
        assert sensors.get(capability) is True, capability


def test_processed_absolute_pressure_without_station_elevation() -> None:
    """
    Muchas PWS de WU no reportan ``elev``: la absoluta debe derivarse
    igualmente (z=0, mismo default que usa el pipeline) en vez de dejar
    la card en "—" con la MSL presente.
    """
    import httpx

    current_no_elev = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 65, "winddir": 180,
            "lat": 41.387, "lon": 2.169,  # sin elev
            "metric": {"temp": 22.0, "pressure": 1022.0},
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "observations/current" in request.url.path:
            return httpx.Response(200, json=current_no_elev)
        return httpx.Response(200, json={"observations": []})

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K"},
        )

    derivatives = response.json()["derivatives"]
    assert derivatives["p_msl"] == pytest.approx(1022.0)
    # z=0 → absoluta ≈ MSL, pero NUNCA "—"
    assert derivatives["p_abs"] == pytest.approx(1022.0, abs=0.5)
    assert derivatives["p_abs_disp"] != "—"


def test_processed_user_elevation_overrides_provider() -> None:
    """
    La altitud introducida por el usuario (station_elevation) prioriza
    sobre la del proveedor: gobierna z, la presión absoluta y la
    termodinámica (θ, ρ…), igual que en el flujo legacy de WU.
    """
    import httpx

    current = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 65, "winddir": 180,
            "lat": 41.387, "lon": 2.169, "elev": 12.0,  # API dice 12 m
            "metric": {"temp": 22.0, "pressure": 1013.0},
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "observations/current" in request.url.path:
            return httpx.Response(200, json=current)
        return httpx.Response(200, json={"observations": []})

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "WU", "station_id": "ITEST", "api_key": "K",
                "station_elevation": 850.0,  # el usuario sabe más que la API
            },
        )

    derivatives = response.json()["derivatives"]
    assert derivatives["z"] == pytest.approx(850.0)
    # Absoluta derivada con 850 m (fórmula hipsométrica con Tc), no con
    # los 12 m de la API
    from models.thermodynamics import msl_to_absolute

    assert derivatives["p_abs"] == pytest.approx(
        msl_to_absolute(1013.0, 850.0, 22.0), abs=0.01,
    )
    assert "missing_elevation" not in {w["code"] for w in response.json()["warnings"]}


def test_processed_missing_elevation_emits_warning() -> None:
    """Sin altitud de usuario NI del proveedor → z=0 con warning explícito."""
    import httpx

    current_no_elev = {
        "observations": [{
            "epoch": 1717255200,
            "humidity": 65, "winddir": 180,
            "lat": 41.387, "lon": 2.169,
            "metric": {"temp": 22.0, "pressure": 1022.0},
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "observations/current" in request.url.path:
            return httpx.Response(200, json=current_no_elev)
        return httpx.Response(200, json={"observations": []})

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K"},
        )

    body = response.json()
    assert body["derivatives"]["z"] == pytest.approx(0.0)
    assert any(w["code"] == "missing_elevation" for w in body["warnings"])

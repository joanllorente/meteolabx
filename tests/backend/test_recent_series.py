"""
Tests de ``POST /v1/observations/series/recent`` y de los
``fetch_recent_series`` de los proveedores que lo soportan.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app
from server.schemas.errors import ProviderError
from server.services import frost, meteohub, nws, poem, weatherlink


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# NWS — fan-out diario + bin horario
# =====================================================================

def test_nws_recent_series_bins_hourly() -> None:
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 10, 12, 0, tzinfo=tz)

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params.get("start", "")
        # Solo el día más reciente trae datos; el resto vacío.
        if start.startswith("2026-06-09") or start.startswith("2026-06-10"):
            base = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)
            features = [
                {
                    "properties": {
                        "timestamp": base.replace(minute=m).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                        "temperature": {"value": 20.0 + m / 60.0, "unitCode": "wmoUnit:degC"},
                        "relativeHumidity": {"value": 60.0, "unitCode": "wmoUnit:percent"},
                        "seaLevelPressure": {"value": 101500.0, "unitCode": "wmoUnit:Pa"},
                    },
                    "geometry": {"type": "Point", "coordinates": [-93.47913, 44.98992]},
                }
                for m in (0, 20, 40)
            ]
            return httpx.Response(200, json={"features": features})
        return httpx.Response(200, json={"features": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        nws.fetch_recent_series("HPN09", days_back=3, client=client, now=now)
    )
    assert result["has_data"] is True
    # 3 lecturas en la misma hora → 1 punto (la última, 14:40 → 20.67 °C)
    assert len(result["epochs"]) == 1
    assert result["temps"][0] == pytest.approx(20.0 + 40 / 60.0)
    assert result["pressures"][0] == pytest.approx(1015.0)


# =====================================================================
# Frost — elementos de tendencia + MSL derivada
# =====================================================================

def test_frost_recent_series_uses_trend_elements() -> None:
    tz = ZoneInfo("Europe/Oslo")
    now = datetime(2026, 6, 10, 12, 0, tzinfo=tz)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["elements"] = request.url.params.get("elements", "")
        return httpx.Response(200, json={
            "data": [
                {
                    "sourceId": "SN100:0",
                    "referenceTime": "2026-06-09T10:00:00.000Z",
                    "observations": [
                        {"elementId": "air_temperature", "value": 15.0,
                         "timeResolution": "PT1H", "qualityCode": 0},
                        {"elementId": "surface_air_pressure", "value": 975.0,
                         "timeResolution": "PT1H", "qualityCode": 0},
                    ],
                }
            ]
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        frost.fetch_recent_series("SN100", "ID", "SECRET", client=client, now=now)
    )
    assert "wind_speed" not in captured["elements"]  # solo T/HR/presión
    assert result["has_data"] is True
    assert result["temps"][0] == pytest.approx(15.0)
    assert result["pressures"][0] == pytest.approx(975.0 * math.exp(333.0 / 8000.0))


# =====================================================================
# POEM — recorte de ventana sobre la serie TR
# =====================================================================

def test_poem_recent_series_clips_window() -> None:
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    old_epoch = int(now.timestamp()) - 10 * 86400   # fuera de ventana
    recent_epoch = int(now.timestamp()) - 2 * 86400

    def _fecha(epoch: int) -> str:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    payload = {"datos": [
        {"codigo": 1103, "fecha": _fecha(old_epoch), "ts": 150},
        {"codigo": 1103, "fecha": _fecha(recent_epoch), "ts": 185},
    ]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        poem.fetch_recent_series("1103", days_back=7, client=client, now=now)
    )
    assert result["has_data"] is True
    assert result["epochs"] == [recent_epoch]
    assert result["temps"] == [pytest.approx(18.5)]


# =====================================================================
# Meteohub — ventana ampliada + bin horario
# =====================================================================

def test_meteohub_recent_series_requests_window_back() -> None:
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 10, 12, 0, tzinfo=tz)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params.get("q", "")
        ref = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return httpx.Response(200, json={"data": [{
            "stat": {"details": [{"var": "B07030", "val": 165.0}]},
            "prod": [
                {"var": "B12101", "lev": "103,2000,0,0",
                 "val": [{"ref": ref, "val": 293.15}]},
            ],
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        meteohub.fetch_recent_series(
            "agrmet|44.08903|12.27459|carpineta", days_back=7, client=client, now=now,
        )
    )
    # La query pide desde 7 días atrás (3 jun 00:00 Roma = 2 jun 22:00 UTC)
    assert ">=2026-06-02 22:00" in captured["q"]
    assert result["has_data"] is True
    assert result["temps"][0] == pytest.approx(20.0)


# =====================================================================
# WeatherLink — chunks diarios + bin horario
# =====================================================================

def test_weatherlink_recent_series_chunks_days() -> None:
    now_epoch = int(datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc).timestamp())
    chunk_starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/stations"):
            return httpx.Response(200, json={"stations": [{
                "station_id": 123456, "station_name": "Mi Davis",
                "latitude": 41.4, "longitude": 2.1, "elevation": 100.0,
                "time_zone": "Europe/Madrid",
            }]})
        start = int(request.url.params.get("start-timestamp", "0"))
        chunk_starts.append(start)
        return httpx.Response(200, json={
            "station_id": 123456,
            "sensors": [{
                "sensor_type": 43, "data_structure_type": 11,
                "data": [{
                    "ts": start + 3600,
                    "temp_last": 68.0,
                    "hum_last": 65.0,
                    "bar_sea_level": 29.92,
                }],
            }],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        weatherlink.fetch_recent_series(
            "123456", "KEY", "SECRET", days_back=3, client=client, now_epoch=now_epoch,
        )
    )
    assert len(chunk_starts) == 3            # un chunk de 24 h por día
    assert result["has_data"] is True
    assert len(result["epochs"]) == 3        # una lectura por chunk/hora
    assert result["temps"][0] == pytest.approx(20.0)
    assert result["pressures"][0] == pytest.approx(29.92 * 33.8638866667)


# =====================================================================
# Endpoint
# =====================================================================

def _make_client(handler) -> TestClient:
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock_client
    app.dependency_overrides[get_settings] = lambda: Settings()
    return TestClient(app)


def test_recent_series_endpoint_nws() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        ts = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
        return httpx.Response(200, json={"features": [{
            "properties": {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "temperature": {"value": 21.0, "unitCode": "wmoUnit:degC"},
            },
            "geometry": {"type": "Point", "coordinates": [-93.47913, 44.98992]},
        }]})

    with _make_client(handler) as client:
        response = client.post(
            "/v1/observations/series/recent",
            json={"provider": "NWS", "station_id": "HPN09", "days_back": 2},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["temps"][-1] == pytest.approx(21.0)
    # Huecos como null (presión no reportada)
    assert body["pressures"][-1] is None
    assert body["theta_e"][-1] is None
    assert body["theta_e_trends"][-1] is None
    assert body["pressure_interval_minutes"] == 180


def test_resolve_recent_fetcher_unsupported_provider() -> None:
    # Todos los proveedores del enum tienen ya recent, así que la rama
    # ``unsupported_provider`` es defensiva (no alcanzable por la API con un
    # provider válido). La ejercemos llamando al resolver con uno ficticio.
    from types import SimpleNamespace

    from server.routers.observations import _resolve_recent_fetcher

    body = SimpleNamespace(
        provider="FAKE", days_back=1, station_id="x", api_key="", api_secret="",
    )
    with pytest.raises(ProviderError) as exc:
        _resolve_recent_fetcher(body, None, Settings())
    assert exc.value.error_code == "unsupported_provider"


def test_recent_series_endpoint_validates_days_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with _make_client(handler) as client:
        response = client.post(
            "/v1/observations/series/recent",
            json={"provider": "NWS", "station_id": "HPN09", "days_back": 99},
        )
    assert response.status_code == 422


# =====================================================================
# AEMET / Meteocat / MeteoGalicia / Météo-France
# =====================================================================

def test_aemet_recent_series_two_step_and_buckets() -> None:
    from server.services import aemet

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/valores/climatologicos/horarios/" in path:
            return httpx.Response(200, json={
                "estado": 200, "datos": "https://opendata.aemet.es/datos/x",
            })
        # Paso 2: datos reales
        return httpx.Response(200, json=[
            {"fint": "2026-06-09T10:00:00", "ta": 20.0, "hr": 60.0,
             "pres_nmar": 1015.0, "lat": 41.3, "lon": 2.1},
            {"fint": "2026-06-09T11:00:00", "ta": 21.0, "hr": 58.0,
             "pres_nmar": 1015.5},
        ])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(aemet.fetch_recent_series("0201X", "K", client=client))
    assert result["has_data"] is True
    # 10:00 y 11:00 UTC caen en buckets de 3 h distintos? 10h//3=3, 11h//3=3
    # → mismo bucket, gana la última lectura (11:00)
    assert len(result["epochs"]) == 1
    assert result["temps"][0] == pytest.approx(21.0)
    assert result["pressures"][0] == pytest.approx(1015.5)
    assert result["lat"] == pytest.approx(41.3)


def test_meteocat_recent_series_converts_to_msl() -> None:
    from server.services import meteocat

    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    reading_dt = datetime(2026, 6, 9, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/2026/06/09"):
            return httpx.Response(200, json=[{
                "codi": "C6",
                "variables": [
                    {"codi": 32, "lectures": [
                        {"data": reading_dt.strftime("%Y-%m-%dT%H:%M"), "valor": 18.0},
                    ]},
                    {"codi": 34, "lectures": [
                        {"data": reading_dt.strftime("%Y-%m-%dT%H:%M"), "valor": 985.0},
                    ]},
                ],
            }])
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        meteocat.fetch_recent_series("C6", "K", client=client, now=now)
    )
    assert result["has_data"] is True
    assert result["temps"][0] == pytest.approx(18.0)
    # MSL canónica: abs 985 con altitud catálogo 264 m
    assert result["pressures"][0] == pytest.approx(985.0 * math.exp(264.0 / 8000.0))


def test_meteogalicia_recent_series_buckets() -> None:
    from server.services import meteogalicia
    from tests.backend.test_meteogalicia_service import HOURLY_PAYLOAD, NOW_LOCAL

    def handler(request: httpx.Request) -> httpx.Response:
        if "ultimosHorarios" in request.url.path:
            assert request.url.params.get("numHoras") == "72"
            return httpx.Response(200, json=HOURLY_PAYLOAD)
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        meteogalicia.fetch_recent_series("10045", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is True
    assert result["pressures"][-1] == pytest.approx(1001.5 * math.exp(94.0 / 8000.0))


def test_meteofrance_recent_series_steps() -> None:
    from server.services import meteofrance

    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        date_param = request.url.params.get("date", "")
        calls.append(date_param)
        if date_param == "2026-06-10T12:00:00Z":
            return httpx.Response(200, json=[{
                "validity_time": "2026-06-10T12:00:00+00:00",
                "t": 295.15, "u": 60.0, "pmer": 101500.0,
            }])
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        meteofrance.fetch_recent_series(
            "01014002", "K", days_back=1, step_hours=3, client=client, now=now,
        )
    )
    assert len(calls) == 9  # 24h/3h + 1
    assert result["has_data"] is True
    assert result["temps"][0] == pytest.approx(22.0)
    assert result["pressures"][0] == pytest.approx(1015.0)


def test_wu_recent_series_hourly_7day() -> None:
    from server.services import wu

    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        assert "hourly/7day" in request.url.path
        assert request.url.params.get("apiKey") == "K"
        return httpx.Response(200, json={"observations": [
            {
                "epoch": now_epoch - 7200,
                "lat": 41.387, "lon": 2.169,
                "humidityAvg": 60.0,
                "metric": {"tempAvg": 21.0, "dewptAvg": 13.0, "pressureMax": 1014.0},
            },
            {
                "epoch": now_epoch - 3600,
                "humidityAvg": 58.0,
                # Sin pressureMax → fallback a pressureMin
                "metric": {"tempAvg": 22.0, "dewptAvg": 13.5, "pressureMin": 1013.5},
            },
        ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(wu.fetch_recent_series("IBARCE12345", "K", client=client))

    assert result["has_data"] is True
    assert result["temps"] == [pytest.approx(21.0), pytest.approx(22.0)]
    assert result["dewpts"] == [pytest.approx(13.0), pytest.approx(13.5)]
    assert result["pressures"][1] == pytest.approx(1013.5)
    assert result["lat"] == pytest.approx(41.387)


def test_wu_recent_series_requires_api_key() -> None:
    from server.services import wu

    with pytest.raises(ProviderError) as excinfo:
        _run(wu.fetch_recent_series("IBARCE12345", ""))
    assert excinfo.value.error_code == "missing_api_key"


def test_wu_dashboard_session_cache_uses_backend_keyword_api_key() -> None:
    """El wrapper WU respeta el contrato keyword-only del cliente FastAPI."""
    from unittest.mock import patch

    import streamlit as st

    st.session_state.pop("wu_cache_dashboard", None)
    dashboard = {"current": {"temp": 21.0}, "series": {"epochs": []}}

    from api.weather_underground import fetch_wu_dashboard_session_cached

    with patch(
        "utils.api_client.fetch_provider_current_processed_via_api",
        return_value=dashboard,
    ) as mock_fetch:
        result = fetch_wu_dashboard_session_cached(
            "IBARCE12345",
            "K",
            60,
            calibration={"temp": {"offset": 0.5}},
            station_elevation=25.0,
            sun_tz_name="Europe/Madrid",
        )

        cached = fetch_wu_dashboard_session_cached(
            "IBARCE12345",
            "K",
            60,
            calibration={"temp": {"offset": 0.5}},
            station_elevation=25.0,
            sun_tz_name="Europe/Madrid",
        )

    mock_fetch.assert_called_once_with(
        "WU",
        "IBARCE12345",
        api_key="K",
        sun_tz_name="Europe/Madrid",
        station_elevation=25.0,
        calibration={"temp": {"offset": 0.5}},
    )
    assert result == dashboard
    assert cached == dashboard
    st.session_state.pop("wu_cache_dashboard", None)


def test_wu_hourly7d_session_cache_uses_backend(monkeypatch) -> None:
    """fetch_hourly_7day_session_cached enruta exclusivamente por FastAPI."""
    from unittest.mock import MagicMock, patch

    import requests as _requests
    import streamlit as st

    st.session_state.pop("wu_cache_hourly7d", None)

    response = MagicMock(spec=_requests.Response)
    response.status_code = 200
    response.json.return_value = {
        "epochs": [1781078400],
        "temps": [21.0],
        "humidities": [60.0],
        "dewpts": [13.0],
        "pressures": [1014.0],
        "lat": 41.387, "lon": 2.169, "has_data": True,
    }

    from api.weather_underground import fetch_hourly_7day_session_cached

    with patch("utils.api_client.requests.post", return_value=response) as mock_post:
        result = fetch_hourly_7day_session_cached("IBARCE12345", "K")

    assert mock_post.call_args.kwargs["json"]["provider"] == "WU"
    assert result["has_data"] is True
    assert result["dewpts"] == [pytest.approx(13.0)]

    # Segunda llamada: servida desde la caché de sesión (sin POST nuevo)
    with patch("utils.api_client.requests.post") as mock_post2:
        cached = fetch_hourly_7day_session_cached("IBARCE12345", "K")
    assert mock_post2.call_count == 0
    assert cached["temps"] == [pytest.approx(21.0)]
    st.session_state.pop("wu_cache_hourly7d", None)


# =====================================================================
# WU — la serie sinóptica se calibra en el backend (igual que /processed)
# =====================================================================

def test_recent_series_applies_wu_calibration(monkeypatch) -> None:
    """``/series/recent`` aplica los offsets WU sobre la serie sinóptica.

    La config de calibración viaja en el body (frontend/localStorage) y el
    backend la aplica tras el caché y antes de derivar.
    """
    from server.services import wu

    raw = {
        "epochs": [1717251600, 1717255200],
        "temps": [18.0, 20.0],
        "humidities": [50.0, 52.0],
        "pressures": [1010.0, 1011.0],
        "winds": [8.0, 9.0],
        "gusts": [20.0, 22.0],
        "wind_dirs": [170.0, 180.0],
        "solar_radiations": [300.0, 400.0],
        "has_data": True,
    }

    async def fake_recent(station_id, api_key, *, days_back, client):
        return {**raw, "temps": list(raw["temps"])}

    monkeypatch.setattr(wu, "fetch_recent_series", fake_recent)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        timeout=5.0,
    )
    base_body = {"provider": "WU", "station_id": "ITEST", "api_key": "K", "days_back": 7}
    with TestClient(app) as client:
        plain = client.post("/v1/observations/series/recent", json=base_body)
        calibrated = client.post(
            "/v1/observations/series/recent",
            json={**base_body, "calibration": {"thermometer": 2.0}},
        )

    assert plain.status_code == 200
    assert calibrated.status_code == 200
    plain_temps = plain.json()["temps"]
    calib_temps = calibrated.json()["temps"]
    assert any(t is not None for t in plain_temps)
    assert calib_temps == [
        (None if t is None else pytest.approx(t + 2.0)) for t in plain_temps
    ]


def test_today_series_lookback_enables_pressure_trend_at_midnight(monkeypatch) -> None:
    """``/series/today`` puede anteponer recent para calcular dp/dt desde medianoche."""
    from server.services import wu

    day_start = 1_800_000_000
    today = {
        "epochs": [day_start, day_start + 3600],
        "temps": [20.0, 21.0],
        "humidities": [50.0, 51.0],
        "pressures": [1010.0, 1011.0],
        "has_data": True,
    }
    recent = {
        "epochs": [day_start - 10_800, day_start - 7200, day_start - 3600],
        "temps": [17.0, 18.0, 19.0],
        "humidities": [47.0, 48.0, 49.0],
        "pressures": [1007.0, 1008.0, 1009.0],
        "has_data": True,
    }

    async def fake_today(station_id, api_key, *, client):
        return dict(today)

    async def fake_recent(station_id, api_key, *, days_back, client):
        return dict(recent)

    monkeypatch.setattr(wu, "fetch_today_series", fake_today)
    monkeypatch.setattr(wu, "fetch_recent_series", fake_recent)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={
                "provider": "WU",
                "station_id": "ITEST",
                "api_key": "K",
                "lookback_hours": 3,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["epochs"][:4] == [day_start - 10_800, day_start - 7200, day_start - 3600, day_start]
    midnight_index = body["epochs"].index(day_start)
    assert body["pressure_trends"][midnight_index] == pytest.approx(1.0)


def test_recent_series_derives_pressure_trends_without_catalog_elevation(monkeypatch) -> None:
    """WU/WeatherLink per-user no siempre tienen altitud en catálogo; eso no debe anular dp/dt."""
    from server.services import wu

    async def fake_recent(station_id, api_key, *, days_back, client):
        return {
            "epochs": [1_800_000_000, 1_800_010_800],
            "temps": [20.0, 21.0],
            "humidities": [50.0, 51.0],
            "pressures": [1010.0, 1013.0],
            "has_data": True,
        }

    monkeypatch.setattr(wu, "fetch_recent_series", fake_recent)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/series/recent",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "K", "days_back": 1},
        )

    assert response.status_code == 200
    assert response.json()["pressure_trends"][1] == pytest.approx(1.0)

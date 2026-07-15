from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app
from server.routers.observations import _build_daily_extremes, _windy_flatlined_fields
from server.services import windy
from fastapi.testclient import TestClient


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
PAYLOAD = {
    "header": {
        "id": "f012abcd",
        "name": "Garden PWS",
        "lat": 41.5,
        "lon": 2.1,
        "elev_m": 100,
    },
    "data": {
        "ts": [1783764000000, 1783767600000],
        "temp": [293.15, 295.15],
        "rh": [70, 60],
        "dew_point": [287.15, 287.15],
        "pressure": [100000, 100100],
        "wind": [2, 3],
        "wind_gust": [4, 5],
        "wind_dir": [180, 200],
        "uv": [1, 2],
        "precip_1h": [0.0, 1.2],
    },
}


def _run(coro):
    return asyncio.run(coro)


def _client(status: int = 200, payload=None):
    captured = {"requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["requests"] += 1
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("windy-api-key")
        return httpx.Response(status, json=PAYLOAD if payload is None else payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._captured = captured  # type: ignore[attr-defined]
    return client


def test_current_normalizes_windy_units():
    current = windy.current_from_payload(PAYLOAD, now=NOW)

    assert current["Tc"] == pytest.approx(22.0)
    assert current["Td"] != current["Td"]
    assert current["wind"] == pytest.approx(10.8)
    assert current["gust"] == pytest.approx(18.0)
    assert current["p_abs_hpa"] == pytest.approx(1001.0)
    assert current["p_hpa"] > current["p_abs_hpa"]
    assert current["precip_rate"] == pytest.approx(1.2)
    assert current["precip_total"] == pytest.approx(1.2)
    assert current["station_name"] == "Garden PWS"


def test_today_series_is_canonical_and_aligned():
    series = windy.today_series_from_payload(PAYLOAD, now=NOW)

    assert series["has_data"] is True
    assert len(series["epochs"]) == 2
    assert series["temps"] == [pytest.approx(20.0), pytest.approx(22.0)]
    assert all(value != value for value in series["dewpts"])
    assert series["winds"] == [pytest.approx(7.2), pytest.approx(10.8)]
    assert series["precips"] == [0.0, pytest.approx(1.2)]
    assert len(series["pressures_abs"]) == len(series["epochs"])


def test_today_series_starts_at_station_local_midnight():
    before_midnight = int(datetime(2026, 7, 10, 21, 45, tzinfo=timezone.utc).timestamp())
    local_midnight = int(datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc).timestamp())
    payload = {
        "header": {"id": "f0", "lat": 41.0, "lon": 2.0},
        "data": {
            # 23:45 and 00:00 Europe/Madrid during summer time.
            "ts": [before_midnight * 1000, local_midnight * 1000],
            "temp": [293.15, 294.15],
            "rh": [60, 61],
        },
    }

    series = windy.today_series_from_payload(
        payload,
        now=NOW,
        tz_name="Europe/Madrid",
    )

    assert series["epochs"] == [local_midnight]
    assert series["temps"] == [pytest.approx(21.0)]


def test_flatlined_windy_fields_do_not_publish_daily_extremes():
    epochs = [1_700_000_000 + index * 1800 for index in range(14)]
    series = {
        "epochs": epochs,
        "temps": [29.2] * len(epochs),
        "humidities": [61.0] * len(epochs),
        "gusts": [10.0, 20.0] * 7,
    }

    assert _windy_flatlined_fields(series) == {"temps", "humidities"}
    extremes = _build_daily_extremes(
        {"Tc": 29.2, "RH": 61.0, "gust": 15.0, "precip_total": 0.0},
        series,
        provider="WINDY",
    )
    assert extremes.temp_max is None
    assert extremes.temp_min is None
    assert extremes.rh_max is None
    assert extremes.rh_min is None
    assert extremes.gust_max == 20.0


def test_fetch_observations_preserves_case_sensitive_station_id():
    client = _client()
    result = _run(windy.fetch_observations("nMcOlGzd", "secret", client=client))

    assert result == PAYLOAD
    assert client._captured["key"] == "secret"
    assert "/nMcOlGzd/observation" in client._captured["url"]


def test_missing_key_is_rejected_without_request():
    with pytest.raises(ProviderError) as excinfo:
        _run(windy.fetch_observations("f012abcd", ""))
    assert excinfo.value.error_code == "missing_api_key"


@pytest.mark.parametrize(
    "status,error_code",
    [(401, "provider_unauthorized"), (404, "station_not_found"), (429, "provider_ratelimit")],
)
def test_http_errors_are_normalized(status, error_code):
    with pytest.raises(ProviderError) as excinfo:
        _run(windy.fetch_observations("f012abcd", "secret", client=_client(status)))
    assert excinfo.value.error_code == error_code


def test_empty_station_has_no_data_error():
    with pytest.raises(ProviderError) as excinfo:
        windy.current_from_payload({"header": {"id": "f0"}})
    assert excinfo.value.error_code == "provider_no_data"


def test_processed_endpoint_supports_windy():
    # El endpoint usa la hora real: los timestamps deben caer en el "hoy"
    # real (el último bucket de 30 min nunca es anterior a la medianoche
    # UTC en curso) o la serie del día sale vacía.
    epoch_2 = int(datetime.now(timezone.utc).timestamp()) // 1800 * 1800
    payload = {
        "header": dict(PAYLOAD["header"]),
        "data": {**PAYLOAD["data"], "ts": [(epoch_2 - 1800) * 1000, epoch_2 * 1000]},
    }
    mock = _client(payload=payload)
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock
    app.dependency_overrides[get_settings] = lambda: Settings(windy_api_key="secret")

    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "WINDY",
                "station_id": "nMcOlGzd",
                "sun_tz_name": "UTC",
                "max_data_age_minutes": 999999,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["observation"]["Tc"] == pytest.approx(22.0)
    assert body["station"]["provider"] == "WINDY"
    assert body["series"]["has_data"] is True
    assert mock._captured["requests"] == 1
    assert "/nMcOlGzd/observation" in mock._captured["url"]

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app
from server.schemas.errors import ProviderError
from server.services import netatmo
from fastapi.testclient import TestClient


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
EPOCH_1 = 1783764000  # 2026-07-11 10:00 UTC
EPOCH_2 = EPOCH_1 + 1800

STATION_ID = "70:ee:50:aa:bb:cc"
STATION = {
    "provider": "NETATMO",
    "station_id": STATION_ID,
    "name": "Terrassa",
    "lat": 41.56,
    "lon": 2.01,
    "elevation": 300.0,
    "tz": "Europe/Madrid",
}

def _build_public_row(epoch_2: int) -> dict:
    return {
        "_id": STATION_ID,
        "place": {"location": [2.01, 41.56], "altitude": 300, "country": "ES"},
        "measures": {
            "02:00:00:aa:bb:cc": {
                "res": {str(epoch_2): [22.0, 60.0]},
                "type": ["temperature", "humidity"],
            },
            STATION_ID: {
                "res": {str(epoch_2): [1013.0]},
                "type": ["pressure"],
            },
            "06:00:00:aa:bb:cc": {
                "wind_strength": 10.8,
                "gust_strength": 18.0,
                "wind_angle": 200,
                "wind_timeutc": epoch_2,
            },
            "05:00:00:aa:bb:cc": {
                "rain_live": 1.2,
                "rain_60min": 0.5,
                "rain_24h": 4.0,
                "rain_timeutc": epoch_2,
            },
        },
    }


def _build_measures(epoch_1: int) -> dict:
    return {
        "02:00:00:aa:bb:cc": [
            {"beg_time": epoch_1, "step_time": 1800, "value": [[20.0, 70.0], [22.0, 60.0]]}
        ],
        STATION_ID: [
            {"beg_time": epoch_1, "step_time": 1800, "value": [[1012.0], [1013.0]]}
        ],
        "06:00:00:aa:bb:cc": [
            {"beg_time": epoch_1, "step_time": 1800, "value": [[7.2, 14.4, 180], [10.8, 18.0, 200]]}
        ],
        "05:00:00:aa:bb:cc": [
            {"beg_time": epoch_1, "step_time": 1800, "value": [[0.0], [1.2]]}
        ],
    }


PUBLIC_ROW = _build_public_row(EPOCH_2)
MEASURES = _build_measures(EPOCH_1)


def _run(coro):
    return asyncio.run(coro)


def _reset_token_cache():
    netatmo._TOKEN_STATE.update(
        {"key": None, "access_token": "", "expires_at": 0.0, "refresh_token": ""}
    )


def _client(public_status: int = 200):
    captured = {"requests": [], "tokens": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        captured["requests"].append(url)
        if "/oauth2/token" in url:
            captured["tokens"] += 1
            return httpx.Response(200, json={
                "access_token": "at-123", "refresh_token": "rt-456", "expires_in": 10800,
            })
        if "/api/getpublicdata" in url:
            if public_status != 200:
                return httpx.Response(public_status, json={"error": {"code": 1}})
            return httpx.Response(200, json={"status": "ok", "body": [PUBLIC_ROW]})
        if "/api/getmeasure" in url:
            module_id = request.url.params.get("module_id")
            captured.setdefault("measure_modules", []).append(module_id)
            return httpx.Response(200, json={
                "status": "ok", "body": MEASURES.get(module_id, []),
            })
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._captured = captured  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    _reset_token_cache()
    yield
    _reset_token_cache()


@pytest.fixture
def _catalog_station(monkeypatch):
    from server.services import stations

    monkeypatch.setattr(
        stations, "get_station",
        lambda provider, station_id: dict(STATION)
        if provider == "NETATMO" and station_id == STATION_ID else None,
    )


def _payload():
    rows_raw = {}
    for module_id, chunks in MEASURES.items():
        fields = {
            "02:00:00:aa:bb:cc": "temp_rh",
            STATION_ID: "pressure",
            "06:00:00:aa:bb:cc": "wind",
            "05:00:00:aa:bb:cc": "rain",
        }[module_id]
        netatmo._merge_measure(rows_raw, fields, netatmo._measure_points(chunks))
    return {
        "station": dict(STATION),
        "public": PUBLIC_ROW,
        "rows": netatmo._finalize_rows(rows_raw, 300.0),
    }


def test_current_uses_public_row_and_today_precip():
    current = netatmo.current_from_payload(_payload(), now=NOW, tz_name="UTC")

    assert current["Tc"] == pytest.approx(22.0)
    assert current["RH"] == pytest.approx(60.0)
    assert current["Td"] != current["Td"]
    assert current["wind"] == pytest.approx(10.8)
    assert current["gust"] == pytest.approx(18.0)
    assert current["p_hpa"] == pytest.approx(1013.0)
    assert current["p_abs_hpa"] < current["p_hpa"]
    assert current["precip_rate"] == pytest.approx(1.2)
    assert current["precip_total"] == pytest.approx(1.2)
    assert current["epoch"] == EPOCH_2
    assert current["station_name"] == "Terrassa"
    assert current["elevation"] == pytest.approx(300.0)


def test_today_series_is_canonical_and_cumulative():
    series = netatmo.today_series_from_payload(_payload(), now=NOW, tz_name="UTC")

    assert series["has_data"] is True
    assert series["epochs"] == [EPOCH_1, EPOCH_2]
    assert series["temps"] == [pytest.approx(20.0), pytest.approx(22.0)]
    assert all(value != value for value in series["dewpts"])
    assert series["pressures"] == [pytest.approx(1012.0), pytest.approx(1013.0)]
    assert series["pressures_abs"][0] < series["pressures"][0]
    assert series["winds"] == [pytest.approx(7.2), pytest.approx(10.8)]
    assert series["precips"] == [pytest.approx(0.0), pytest.approx(1.2)]
    assert len(series["solar_radiations"]) == 2


def test_today_series_respects_local_midnight():
    series = netatmo.today_series_from_payload(
        _payload(),
        now=datetime(2026, 7, 11, 23, 30, tzinfo=timezone.utc),
        # En Tongatapu (UTC+13) la medianoche local cae a las 11:00 UTC,
        # después de ambos epochs: la serie de hoy queda vacía.
        tz_name="Pacific/Tongatapu",
    )
    assert series["has_data"] is False


def test_fetch_observations_merges_modules(_catalog_station):
    client = _client()
    payload = _run(netatmo.fetch_observations(
        STATION_ID, "cid", "csecret", "rtoken", client=client,
    ))

    assert payload["station"]["name"] == "Terrassa"
    assert payload["public"]["_id"] == STATION_ID
    assert [row["epoch"] for row in payload["rows"]] == [EPOCH_1, EPOCH_2]
    assert payload["rows"][1]["Tc"] == pytest.approx(22.0)
    assert payload["rows"][1]["p_hpa"] == pytest.approx(1013.0)
    assert client._captured["tokens"] == 1
    assert sorted(client._captured["measure_modules"]) == sorted(MEASURES.keys())


def test_access_token_is_cached_between_calls(_catalog_station):
    client = _client()
    _run(netatmo.fetch_observations(STATION_ID, "cid", "csecret", "rtoken", client=client))
    _run(netatmo.fetch_observations(STATION_ID, "cid", "csecret", "rtoken", client=client))

    assert client._captured["tokens"] == 1


def test_missing_credentials_are_rejected_without_request():
    with pytest.raises(ProviderError) as excinfo:
        _run(netatmo.fetch_observations(STATION_ID, "", "", "", client=_client()))
    assert excinfo.value.error_code == "missing_api_key"


def test_unknown_station_is_not_found(monkeypatch):
    from server.services import stations

    monkeypatch.setattr(stations, "get_station", lambda provider, station_id: None)
    with pytest.raises(ProviderError) as excinfo:
        _run(netatmo.fetch_observations("ff:ff", "cid", "csecret", "rtoken", client=_client()))
    assert excinfo.value.error_code == "station_not_found"


def test_ratelimit_code_26_is_normalized(_catalog_station):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "at", "expires_in": 10800})
        return httpx.Response(403, json={"error": {"code": 26, "message": "usage"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as excinfo:
        _run(netatmo.fetch_observations(STATION_ID, "cid", "csecret", "rtoken", client=client))
    assert excinfo.value.error_code == "provider_ratelimit"


def test_processed_endpoint_supports_netatmo(monkeypatch):
    import sys

    this_module = sys.modules[__name__]
    from server.services import stations

    monkeypatch.setattr(
        stations, "get_station",
        lambda provider, station_id: dict(STATION)
        if provider == "NETATMO" and station_id == STATION_ID else None,
    )
    # El endpoint usa la hora real: los epochs deben caer en el "hoy" real
    # (el último bucket de 30 min ya cerrado, que nunca es anterior a la
    # medianoche UTC en curso).
    epoch_2 = int(datetime.now(timezone.utc).timestamp()) // 1800 * 1800
    monkeypatch.setattr(this_module, "PUBLIC_ROW", _build_public_row(epoch_2))
    monkeypatch.setattr(this_module, "MEASURES", _build_measures(epoch_2 - 1800))
    mock = _client()
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock
    app.dependency_overrides[get_settings] = lambda: Settings(
        netatmo_client_id="cid",
        netatmo_client_secret="csecret",
        netatmo_refresh_token="rtoken",
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={
                "provider": "NETATMO",
                "station_id": STATION_ID,
                "sun_tz_name": "UTC",
                "max_data_age_minutes": 999999,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["observation"]["Tc"] == pytest.approx(22.0)
    assert body["station"]["provider"] == "NETATMO"
    assert body["series"]["has_data"] is True
    assert "NaN" not in json.dumps(body)

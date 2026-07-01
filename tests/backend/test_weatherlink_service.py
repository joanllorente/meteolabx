"""
Tests del servicio puro ``server.services.weatherlink``.

WeatherLink usa doble credencial per-user (api-key en query +
X-Api-Secret en header) y unidades imperiales; la normalización vive
en ``domain/parsing/weatherlink.py``.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

from pathlib import Path

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import weatherlink, weatherlink_climo


STATION = "123456"
NOW_EPOCH = int(datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc).timestamp())

STATIONS_PAYLOAD = {
    "stations": [
        {
            "station_id": 123456,
            "station_name": "Mi Davis",
            "latitude": 41.4,
            "longitude": 2.1,
            "elevation": 100.0,
            "time_zone": "Europe/Madrid",
        }
    ]
}

CURRENT_PAYLOAD = {
    "station_id": 123456,
    "sensors": [
        {
            "sensor_type": 43,
            "data_structure_type": 10,
            "data": [
                {
                    "ts": NOW_EPOCH,
                    "temp": 71.6,             # °F → 22 °C
                    "hum": 60.0,
                    "dew_point": 57.2,        # °F → 14 °C
                    "wind_speed_last": 10.0,  # mph → 16.09 km/h
                    "wind_speed_hi_last_10_min": 20.0,
                    "wind_dir_last": 180.0,
                    "bar_sea_level": 29.92,   # inHg → 1013.2 hPa
                    "rainfall_daily_mm": 1.2,
                    "solar_rad": 800.0,
                    "uv_index": 6.0,
                }
            ],
        }
    ],
}

HISTORIC_PAYLOAD = {
    "station_id": 123456,
    "sensors": [
        {
            "sensor_type": 43,
            "data_structure_type": 11,
            "data": [
                {
                    "ts": NOW_EPOCH - 3600,
                    "temp_last": 68.0,   # 20 °C
                    "hum_last": 65.0,
                    "bar_sea_level": 29.90,
                },
                {
                    "ts": NOW_EPOCH,
                    "temp_last": 71.6,   # 22 °C
                    "hum_last": 60.0,
                    "bar_sea_level": 29.92,
                },
            ],
        }
    ],
}

CLIMO_HISTORIC_PAYLOAD = {
    "station_id": 123456,
    "sensors": [
        {
            "sensor_type": 43,
            "data_structure_type": 11,
            "data": [
                {
                    "ts": int(datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc).timestamp()),
                    "temp_last": 68.0,        # 20 °C
                    "wind_speed_avg": 5.0,    # mph → km/h
                    "wind_speed_hi": 12.0,
                    "wind_dir_of_prevail": 90.0,
                    "rainfall_daily_mm": 0.4,
                },
                {
                    "ts": int(datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc).timestamp()),
                    "temp_last": 77.0,        # 25 °C
                    "wind_speed_avg": 10.0,
                    "wind_speed_hi": 20.0,
                    "wind_dir_of_prevail": 180.0,
                    "rainfall_daily_mm": 1.8,
                },
            ],
        }
    ],
}


def _client(
    *,
    stations=None,
    current=None,
    historic=None,
    status: int = 200,
) -> httpx.AsyncClient:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("paths", []).append(request.url.path)
        captured["api_key"] = request.url.params.get("api-key", "")
        captured["secret"] = request.headers.get("X-Api-Secret", "")
        if status != 200:
            return httpx.Response(status, json={})
        path = request.url.path
        if path.endswith("/stations"):
            return httpx.Response(200, json=stations or STATIONS_PAYLOAD)
        if "/current/" in path:
            return httpx.Response(200, json=current or CURRENT_PAYLOAD)
        if "/historic/" in path:
            return httpx.Response(200, json=historic or HISTORIC_PAYLOAD)
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_weatherlink_service_does_not_import_streamlit() -> None:
    for path in ("server/services/weatherlink.py", "domain/parsing/weatherlink.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "import streamlit" not in source, path
        assert "from streamlit" not in source, path


def test_fetch_current_requires_both_credentials() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(weatherlink.fetch_current(STATION, "KEY", ""))
    assert excinfo.value.error_code == "missing_api_key"
    assert excinfo.value.status_code == 400


def test_fetch_current_converts_imperial_units() -> None:
    client = _client()
    result = _run(
        weatherlink.fetch_current(STATION, "KEY", "SECRET", client=client)
    )

    # Credenciales en query/header, nunca en el path
    assert client._captured["api_key"] == "KEY"
    assert client._captured["secret"] == "SECRET"

    assert result["Tc"] == pytest.approx(22.0)
    assert result["Td"] == pytest.approx(14.0)  # nativo preservado
    assert result["RH"] == pytest.approx(60.0)
    assert result["wind"] == pytest.approx(16.09344)
    assert result["gust"] == pytest.approx(32.18688)
    assert result["p_hpa"] == pytest.approx(29.92 * 33.8638866667)
    # Absoluta derivada de MSL con la elevación de /stations (100 m)
    assert result["p_abs_hpa"] == pytest.approx(
        (29.92 * 33.8638866667) / math.exp(100.0 / 8000.0)
    )
    assert result["precip_total"] == pytest.approx(1.2)
    assert result["solar_radiation"] == pytest.approx(800.0)
    assert result["uv"] == pytest.approx(6.0)
    assert result["station_name"] == "Mi Davis"
    assert result["elevation"] == pytest.approx(100.0)


def test_fetch_current_unauthorized() -> None:
    client = _client(status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(weatherlink.fetch_current(STATION, "KEY", "SECRET", client=client))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_fetch_current_empty_payload_is_bad_response() -> None:
    client = _client(current={"station_id": 123456, "sensors": []})
    with pytest.raises(ProviderError) as excinfo:
        _run(weatherlink.fetch_current(STATION, "KEY", "SECRET", client=client))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_today_series_uses_station_window() -> None:
    client = _client()
    result = _run(
        weatherlink.fetch_today_series(
            STATION, "KEY", "SECRET", client=client, now_epoch=NOW_EPOCH,
        )
    )

    # /stations primero (timezone/altitud) y luego /historic
    paths = client._captured["paths"]
    assert any(p.endswith("/stations") for p in paths)
    assert any("/historic/" in p for p in paths)

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(20.0), pytest.approx(22.0)]
    assert result["pressures"][1] == pytest.approx(29.92 * 33.8638866667)
    assert result["lat"] == pytest.approx(41.4)


def test_fetch_today_series_empty() -> None:
    client = _client(historic={"station_id": 123456, "sensors": []})
    result = _run(
        weatherlink.fetch_today_series(
            STATION, "KEY", "SECRET", client=client, now_epoch=NOW_EPOCH,
        )
    )
    assert result["has_data"] is False


def test_weatherlink_climo_daily_aggregates_historic_records() -> None:
    client = _client(historic=CLIMO_HISTORIC_PAYLOAD)
    df = _run(
        weatherlink_climo.fetch_climo_daily_for_periods(
            client,
            STATION,
            "KEY",
            "SECRET",
            [(datetime(2026, 6, 10).date(), datetime(2026, 6, 10).date())],
        )
    )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["date"].strftime("%Y-%m-%d") == "2026-06-10"
    assert row["temp_mean"] == pytest.approx(22.5)
    assert row["temp_max"] == pytest.approx(25.0)
    assert row["temp_min"] == pytest.approx(20.0)
    assert row["wind_mean"] == pytest.approx((5.0 + 10.0) * 1.609344 / 2.0)
    assert row["gust_max"] == pytest.approx(20.0 * 1.609344)
    assert row["precip_total"] == pytest.approx(1.8)


def test_weatherlink_climo_forbidden_maps_to_unauthorized() -> None:
    client = _client(status=403)
    with pytest.raises(ProviderError) as excinfo:
        _run(
            weatherlink_climo.fetch_climo_daily_for_periods(
                client,
                STATION,
                "KEY",
                "SECRET",
                [(datetime(2026, 6, 10).date(), datetime(2026, 6, 10).date())],
            )
        )
    assert excinfo.value.error_code == "provider_unauthorized"

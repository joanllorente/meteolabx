"""
Tests del port async puro de la climatología de AEMET
(``server/services/aemet_climo.py``) y de su rama en
``POST /v1/climo/dataset``.
"""

from __future__ import annotations

import io
import math
from datetime import date
from typing import Optional
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app

DAILY_RECORDS = [
    {
        "fecha": "2025-06-01",
        "indicativo": "3195",
        "tmed": "21,4",
        "tmax": "27,0",
        "tmin": "15,8",
        "velmedia": "2,5",     # m/s → 9 km/h
        "dir": "SW",
        "racha": "10,0",       # m/s → 36 km/h
        "prec": "Ip",          # inapreciable → 0.0
        "sol": "11,2",
        "stdvv": "999",        # nunca debe colarse como dirección
    },
    {
        "fecha": "2025-06-02",
        "indicativo": "3195",
        "tmax": "26,0",
        "tmin": "14,0",        # sin tmed → media de máx/mín
        "prec": "4,2",
    },
]

MONTHLY_RECORDS = [
    {
        "fecha": "2024-1",
        "tm_mes": "9,5",
        "tm_max": "13,0",
        "tm_min": "6,0",
        "ta_max": "18.2(27)",       # absoluta + día de ocurrencia
        "ta_min": "-1.5(03)",
        "p_mes": "120,0",
        "n_llu": "14",
        "w_racha": "99/21.1(07)",   # dir/velocidad m/s → 75.96 km/h
        "nt_00": "3",
    },
    {
        "fecha": "2024-2",
        "tm_mes": "10,5",
        "ta_max": "21.0(11)",
        "ta_min": "0.5(20)",
        "p_mes": "80,0",
        "n_llu": "10",
        "nt_00": "1",
    },
    {
        "fecha": "2024-13",          # resumen anual: absoluta más extrema
        "tm_mes": "14,8",
        "ta_max": "37.4(27/ago)",
        "ta_min": "-2.0(03/ene)",
        "p_mes": "410,0",
    },
]


def _two_step_client(records, record: Optional[dict] = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        if "/valores/climatologicos/" in path:
            return httpx.Response(
                200, json={"estado": 200, "datos": "https://opendata.aemet.es/datos/x"},
            )
        if record is not None:
            record.setdefault("paths", []).append(path)
        return httpx.Response(200, json=records)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.mark.asyncio
async def test_daily_periods_parses_aemet_quirks() -> None:
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    async with _two_step_client(DAILY_RECORDS) as client:
        df = await fetch_climo_daily_for_periods(
            client, "3195", "K", [(date(2025, 6, 1), date(2025, 6, 30))],
        )

    assert len(df) == 2
    day1 = df.iloc[0]
    assert day1["temp_mean"] == pytest.approx(21.4)     # coma decimal
    assert day1["wind_mean"] == pytest.approx(9.0)      # m/s → km/h
    assert day1["gust_max"] == pytest.approx(36.0)
    assert day1["wind_dir_mean"] == pytest.approx(225.0)  # "SW" cardinal, no stdvv
    assert day1["precip_total"] == pytest.approx(0.0)   # 'Ip' = inapreciable
    assert day1["solar_hours"] == pytest.approx(11.2)

    day2 = df.iloc[1]
    assert day2["temp_mean"] == pytest.approx(20.0)     # (26 + 14) / 2
    assert day2["precip_total"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_daily_long_range_splits_in_150_day_chunks() -> None:
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    record: dict = {}
    async with _two_step_client(DAILY_RECORDS, record) as client:
        await fetch_climo_daily_for_periods(
            client, "3195", "K", [(date(2024, 1, 1), date(2024, 12, 31))],
        )
    # 366 días / 150 → 3 ventanas (la URL del paso 2 se pide 3 veces)
    assert len(record["paths"]) == 3


@pytest.mark.asyncio
async def test_legacy_txt_station_served_without_aemet_api_calls() -> None:
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_daily_for_periods(
            client, "9771C", "K", [(date(1950, 1, 1), date(1950, 1, 31))],
        )

    assert calls == []
    assert len(df) == 31
    first = df.iloc[0]
    assert first["date"].strftime("%Y-%m-%d") == "1950-01-01"
    assert first["temp_max"] == pytest.approx(9.1)
    assert first["temp_min"] == pytest.approx(3.3)
    assert first["precip_total"] == pytest.approx(0.0)
    assert first["solar_hours"] == pytest.approx(6.3)


@pytest.mark.asyncio
async def test_legacy_txt_station_uses_aemet_api_only_outside_local_coverage() -> None:
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    records = [
        {
            "fecha": "2026-01-01",
            "indicativo": "9771C",
            "tmax": "10,0",
            "tmin": "2,0",
            "prec": "1,0",
        }
    ]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        calls.append(path)
        if "/valores/climatologicos/" in path:
            return httpx.Response(
                200, json={"estado": 200, "datos": "https://opendata.aemet.es/datos/x"},
            )
        return httpx.Response(200, json=records)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_daily_for_periods(
            client, "9771C", "K", [(date(2025, 12, 31), date(2026, 1, 1))],
        )

    assert len(calls) == 2
    assert "2026-01-01T00%3A00%3A00UTC" in calls[0]
    assert len(df) == 2
    assert df.iloc[0]["date"].strftime("%Y-%m-%d") == "2025-12-31"
    assert df.iloc[1]["date"].strftime("%Y-%m-%d") == "2026-01-01"


@pytest.mark.asyncio
async def test_legacy_txt_station_monthly_summary_uses_local_daily_file() -> None:
    from server.services.aemet_climo import fetch_climo_monthly_for_year

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_monthly_for_year(client, "9771C", "K", 1950)

    assert calls == []
    assert len(df) == 12
    assert df.iloc[0]["date"].strftime("%Y-%m-%d") == "1950-01-01"
    assert df.iloc[0]["precip_total"] == pytest.approx(4.8)
    assert df.iloc[0]["temp_abs_min"] == pytest.approx(-4.4)


@pytest.mark.asyncio
async def test_legacy_txt_station_yearly_summary_mixes_local_and_api_years() -> None:
    from server.services.aemet_climo import fetch_climo_yearly_for_years

    records = [
        {"fecha": "2026-1", "tm_mes": "12,0", "p_mes": "10,0"},
        {"fecha": "2026-13", "tm_mes": "13,0", "p_mes": "100,0"},
    ]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        calls.append(path)
        if "/valores/climatologicos/" in path:
            return httpx.Response(
                200, json={"estado": 200, "datos": "https://opendata.aemet.es/datos/x"},
            )
        return httpx.Response(200, json=records)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_yearly_for_years(client, "9771C", "K", [1950, 2026])

    assert len(calls) == 2
    assert "anioini/2026/aniofin/2026" in calls[0]
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["1950-01-01", "2026-01-01"]
    assert df.iloc[0]["precip_total"] == pytest.approx(240.2)
    assert df.iloc[1]["precip_total"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_monthly_year_uses_annual_record_for_absolute_extremes() -> None:
    from server.services.aemet_climo import fetch_climo_monthly_for_year

    async with _two_step_client(MONTHLY_RECORDS) as client:
        df = await fetch_climo_monthly_for_year(client, "3195", "K", 2024)

    assert len(df) == 2  # el registro YYYY-13 no es una fila más
    jan = df.iloc[0]
    assert jan["temp_mean"] == pytest.approx(9.5)
    assert jan["rain_days"] == pytest.approx(14.0)
    assert jan["frost_nights"] == pytest.approx(3.0)
    assert jan["gust_max"] == pytest.approx(21.1 * 3.6)  # "99/21.1" → 21.1 m/s

    # El anual (37.4 ago / -2.0 ene) corrige los extremos absolutos
    assert pd.to_numeric(df["temp_abs_max"]).max() == pytest.approx(37.4)
    assert pd.to_numeric(df["temp_abs_min"]).min() == pytest.approx(-2.0)
    # Y arrastra la fecha del paréntesis
    assert "2024-08-27" in df["temp_abs_max_date"].astype(str).tolist()


@pytest.mark.asyncio
async def test_yearly_for_years_prefers_annual_summary() -> None:
    from server.services.aemet_climo import fetch_climo_yearly_for_years

    async with _two_step_client(MONTHLY_RECORDS) as client:
        df = await fetch_climo_yearly_for_years(client, "3195", "K", [2024])

    assert len(df) == 1
    row = df.iloc[0]
    assert row["temp_mean"] == pytest.approx(14.8)      # del resumen anual
    assert row["precip_total"] == pytest.approx(410.0)
    assert row["temp_abs_max"] == pytest.approx(37.4)
    # Noches de helada: el anual no las trae → suma de mensuales (3+1)
    assert row["frost_nights"] == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_unauthorized_cuts_immediately() -> None:
    from server.schemas.errors import ProviderError
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"estado": 401, "descripcion": "API key invalido"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        with pytest.raises(ProviderError) as excinfo:
            await fetch_climo_daily_for_periods(
                client, "3195", "BAD", [(date(2025, 6, 1), date(2025, 6, 5))],
            )
    assert excinfo.value.error_code == "provider_unauthorized"


@pytest.mark.asyncio
async def test_failed_chunk_degrades_to_empty() -> None:
    from server.services.aemet_climo import fetch_climo_daily_for_periods

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_daily_for_periods(
            client, "3195", "K", [(date(2025, 6, 1), date(2025, 6, 5))],
        )
    assert df.empty


def test_endpoint_uses_async_service_not_frontend_dispatch() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: _two_step_client(DAILY_RECORDS)

    with patch(
        "utils.historical_dispatch.fetch_historical_dataset",
        side_effect=AssertionError("la rama AEMET no debe pasar por el dispatcher frontend"),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/v1/climo/dataset",
                json={
                    "provider": "AEMET",
                    "station_id": "3195",
                    "api_key": "K",
                    "summary_mode": "monthly",
                    "periods": [
                        {"label": "jun 2025", "start": "2025-06-01", "end": "2025-06-30"},
                    ],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    df = pd.read_json(io.StringIO(body["dataset"]), orient="table")
    assert df["temp_mean"].iloc[0] == pytest.approx(21.4)
    assert df["wind_dir_mean"].iloc[0] == pytest.approx(225.0)

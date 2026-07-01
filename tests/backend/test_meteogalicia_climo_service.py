"""
Tests del port async puro del climo de MeteoGalicia
(``server/services/meteogalicia_climo.py``) y de su rama en
``POST /v1/climo/dataset`` (sirve el contrato canónico en
threadpool para este proveedor).
"""

from __future__ import annotations

import io
import math
from datetime import date
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app


def _measure(code: str, value: float, validation: int = 1) -> dict:
    return {"codigoParametro": code, "valor": value, "lnCodigoValidacion": validation}


DAILY_PAYLOAD = {
    "listDatosDiarios": [
        {
            "data": "2025-06-01T00:00:00.000+02:00",
            "listaEstacions": [{
                "idEstacion": "10045",
                "listaMedidas": [
                    _measure("TA_AVG_1.5m", 18.5),
                    _measure("TA_MAX_1.5m", 24.0),
                    _measure("TA_MIN_1.5m", 12.0),
                    _measure("PP_SUM_1.5m", 3.4),
                    _measure("VV_AVG_10m", 2.0),     # m/s → 7.2 km/h
                    _measure("VV_MAX_2m", 5.0),      # m/s → 18 km/h
                    _measure("HSOL_SUM_1.5m", 9.1),
                ],
            }],
        },
        {
            "data": "2025-06-02T00:00:00.000+02:00",
            "listaEstacions": [{
                "idEstacion": "10045",
                "listaMedidas": [
                    _measure("TA_AVG_1.5m", 19.0),
                    _measure("TA_MAX_1.5m", -9999.0),     # centinela → NaN
                    _measure("PP_SUM_1.5m", 1.0, validation=3),  # erróneo → fuera
                ],
            }],
        },
    ]
}

MONTHLY_PAYLOAD = {
    "listDatosMensuais": [
        {
            "data": "2024-01-15T00:00:00.000+01:00",  # se canonicaliza al día 1
            "listaEstacions": [{
                "idEstacion": "10045",
                "listaMedidas": [
                    _measure("TA_AVG_1.5m", 9.5),
                    _measure("TA_AVGMAX_1.5m", 13.0),
                    _measure("TA_AVGMIN_1.5m", 6.0),
                    _measure("TA_MAX_1.5m", 18.2),
                    _measure("TA_MIN_1.5m", -1.5),
                    _measure("PP_SUM_1.5m", 120.0),
                    _measure("NDPP_RECUENTO_1.5m", 14.0),
                    _measure("NDX_RECUENTO_1.5m", 3.0),
                ],
            }],
        },
        {
            "data": "2024-02-15T00:00:00.000+01:00",
            "listaEstacions": [{
                "idEstacion": "10045",
                "listaMedidas": [
                    _measure("TA_AVG_1.5m", 10.5),
                    _measure("TA_MAX_1.5m", 21.0),
                    _measure("TA_MIN_1.5m", 0.5),
                    _measure("PP_SUM_1.5m", 80.0),
                    _measure("NDPP_RECUENTO_1.5m", 10.0),
                    _measure("NDX_RECUENTO_1.5m", 1.0),
                ],
            }],
        },
    ]
}


def _mock_client(daily=DAILY_PAYLOAD, monthly=MONTHLY_PAYLOAD, status=200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "datosDiarios" in request.url.path:
            return httpx.Response(status, json=daily)
        if "datosMensuais" in request.url.path:
            return httpx.Response(status, json=monthly)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# =====================================================================
# Servicio async puro
# =====================================================================

@pytest.mark.asyncio
async def test_daily_for_periods_parses_units_and_sentinels() -> None:
    from server.services.meteogalicia_climo import fetch_climo_daily_for_periods

    async with _mock_client() as client:
        df = await fetch_climo_daily_for_periods(
            client, "10045", [(date(2025, 6, 1), date(2025, 6, 30))],
        )

    assert len(df) == 2
    row = df.iloc[0]
    assert row["date"] == pd.Timestamp("2025-06-01")
    assert row["temp_mean"] == pytest.approx(18.5)
    # m/s → km/h, y 10m prioriza sobre 2m para el mismo campo
    assert row["wind_mean"] == pytest.approx(7.2)
    assert row["gust_max"] == pytest.approx(18.0)
    assert row["solar_hours"] == pytest.approx(9.1)

    row2 = df.iloc[1]
    assert math.isnan(row2["temp_max"])        # centinela -9999 descartado
    assert math.isnan(row2["precip_total"])    # validación 3 descartada


@pytest.mark.asyncio
async def test_monthly_for_year_canonicalizes_month_start() -> None:
    from server.services.meteogalicia_climo import fetch_climo_monthly_for_year

    async with _mock_client() as client:
        df = await fetch_climo_monthly_for_year(client, "10045", 2024)

    assert df["date"].tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")]
    jan = df.iloc[0]
    assert jan["temp_mean"] == pytest.approx(9.5)
    assert jan["temp_max"] == pytest.approx(13.0)      # media de máximas
    assert jan["temp_abs_max"] == pytest.approx(18.2)  # absoluta del mes
    assert jan["rain_days"] == pytest.approx(14.0)
    assert jan["frost_nights"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_yearly_aggregates_monthly_rows() -> None:
    from server.services.meteogalicia_climo import fetch_climo_yearly_for_years

    async with _mock_client() as client:
        df = await fetch_climo_yearly_for_years(client, "10045", [2024])

    assert len(df) == 1
    year_row = df.iloc[0]
    assert year_row["date"] == pd.Timestamp("2024-01-01")
    assert year_row["temp_mean"] == pytest.approx(10.0)        # media (9.5, 10.5)
    assert year_row["precip_total"] == pytest.approx(200.0)    # suma
    assert year_row["rain_days"] == pytest.approx(24.0)        # suma
    assert year_row["temp_abs_max"] == pytest.approx(21.0)     # máximo
    assert year_row["temp_abs_min"] == pytest.approx(-1.5)     # mínimo


@pytest.mark.asyncio
async def test_upstream_error_degrades_to_empty_df() -> None:
    """Una petición caída → sin filas, nunca excepción."""
    from server.services.meteogalicia_climo import fetch_climo_daily_for_periods

    async with _mock_client(status=502) as client:
        df = await fetch_climo_daily_for_periods(
            client, "10045", [(date(2025, 6, 1), date(2025, 6, 30))],
        )
    assert df.empty


# =====================================================================
# Rama async en POST /v1/climo/dataset
# =====================================================================

def _endpoint_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_http_client] = _mock_client
    return TestClient(app)


def test_endpoint_uses_async_service_not_frontend_dispatch() -> None:
    with patch(
        "utils.historical_dispatch.fetch_historical_dataset",
        side_effect=AssertionError("la rama METEOGALICIA no debe pasar por el dispatcher frontend"),
    ):
        with _endpoint_client() as client:
            response = client.post(
                "/v1/climo/dataset",
                json={
                    "provider": "METEOGALICIA",
                    "station_id": "10045",
                    "api_key": "",
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
    assert df["temp_mean"].tolist()[:1] == [18.5]
    assert df["wind_mean"].iloc[0] == pytest.approx(7.2)


def test_endpoint_annual_single_year_hits_monthly() -> None:
    with _endpoint_client() as client:
        response = client.post(
            "/v1/climo/dataset",
            json={
                "provider": "METEOGALICIA",
                "station_id": "10045",
                "api_key": "",
                "summary_mode": "annual",
                "selected_years": [2024],
            },
        )

    assert response.status_code == 200
    df = pd.read_json(io.StringIO(response.json()["dataset"]), orient="table")
    assert len(df) == 2  # dos meses del payload mensual
    assert df["temp_abs_max"].iloc[0] == pytest.approx(18.2)


def test_endpoint_annual_multi_year_aggregates() -> None:
    with _endpoint_client() as client:
        response = client.post(
            "/v1/climo/dataset",
            json={
                "provider": "METEOGALICIA",
                "station_id": "10045",
                "api_key": "",
                "summary_mode": "annual",
                "selected_years": [2023, 2024],
            },
        )

    assert response.status_code == 200
    df = pd.read_json(io.StringIO(response.json()["dataset"]), orient="table")
    assert len(df) == 2  # una fila agregada por año
    assert df["precip_total"].tolist() == [200.0, 200.0]


# =====================================================================
# El servicio async deriva del parsing compartido
# =====================================================================

def test_async_matches_shared_parsing() -> None:
    """
    El port async no añade transformaciones sobre
    ``domain/parsing/meteogalicia_climo``: para el mismo payload, el
    DataFrame del servicio async coincide con el del parsing compartido
    aplicado directamente (única fuente de verdad del ensamblado).
    """
    import asyncio

    from domain.parsing.meteogalicia_climo import (
        DAILY_PARAM_MAP,
        extract_climo_rows,
        rows_to_climo_df,
    )
    from server.services.meteogalicia_climo import fetch_climo_daily_for_periods

    expected_rows = extract_climo_rows(
        DAILY_PAYLOAD, "10045",
        list_key="listDatosDiarios", param_map=DAILY_PARAM_MAP,
    )
    expected_df = rows_to_climo_df(expected_rows)

    async def _run():
        async with _mock_client() as client:
            return await fetch_climo_daily_for_periods(
                client, "10045", [(date(2025, 6, 1), date(2025, 6, 30))],
            )

    async_df = asyncio.run(_run())
    pd.testing.assert_frame_equal(expected_df, async_df)

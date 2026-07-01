"""
Tests del port async puro de las normales climáticas de Frost
(``server/services/frost_climo.py``) y de su rama en
``POST /v1/climo/dataset``.
"""

from __future__ import annotations

import base64
import io
import math
from typing import Optional
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app

PERIOD = "1991/2020"

AVAILABLE_PAYLOAD = {
    "data": [
        {"period": PERIOD, "elementId": "mean(air_temperature P1M)"},
        {"period": PERIOD, "elementId": "sum(precipitation_amount P1M)"},
        {"period": PERIOD, "elementId": "mean(air_temperature P1Y)"},
        {"period": PERIOD, "elementId": "sum(precipitation_amount P1Y)"},
        # mensual de máximas NO disponible → no debe pedirse ni rellenarse
    ]
}

NORMALS_MONTHLY_PAYLOAD = {
    "data": [
        {"elementId": "mean(air_temperature P1M)", "month": 1, "normal": -4.2},
        {"elementId": "mean(air_temperature P1M)", "month": 7, "normal": 14.8},
        {"elementId": "sum(precipitation_amount P1M)", "month": 1, "normal": 55.0},
        {"elementId": "sum(precipitation_amount P1M)", "month": 7, "normal": 90.0},
    ]
}

NORMALS_YEARLY_PAYLOAD = {
    "data": [
        {"elementId": "mean(air_temperature P1Y)", "normal": 4.9},
        {"elementId": "sum(precipitation_amount P1Y)", "normal": 780.0},
    ]
}


def _mock_client(record: Optional[dict] = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.setdefault("requests", []).append(request)
        if "climatenormals/available" in request.url.path:
            return httpx.Response(200, json=AVAILABLE_PAYLOAD)
        if "climatenormals" in request.url.path:
            elements = request.url.params.get("elements", "")
            if "P1Y" in elements:
                return httpx.Response(200, json=NORMALS_YEARLY_PAYLOAD)
            return httpx.Response(200, json=NORMALS_MONTHLY_PAYLOAD)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.mark.asyncio
async def test_monthly_normals_only_requests_available_elements() -> None:
    from server.services.frost_climo import fetch_climo_monthly_for_period

    record: dict = {}
    async with _mock_client(record) as client:
        df = await fetch_climo_monthly_for_period(
            client, "SN100", PERIOD, [1, 7],
            client_id="ID", client_secret="SECRET",
        )

    assert len(df) == 2
    jan = df.iloc[0]
    assert jan["date"] == pd.Timestamp(2020, 1, 1)  # año de anclaje del periodo
    assert jan["temp_mean"] == pytest.approx(-4.2)
    assert jan["precip_total"] == pytest.approx(55.0)
    assert math.isnan(jan["temp_max"])  # elemento no publicado
    assert jan["period_label"] == PERIOD

    # La petición de normales solo pide los elementos disponibles
    normals_req = [
        r for r in record["requests"] if "available" not in r.url.path
    ][0]
    elements = normals_req.url.params.get("elements", "")
    assert "mean(air_temperature P1M)" in elements
    assert "max(air_temperature" not in elements

    # Credenciales por HTTP Basic
    auth_header = normals_req.headers.get("Authorization", "")
    assert auth_header == "Basic " + base64.b64encode(b"ID:SECRET").decode()


@pytest.mark.asyncio
async def test_yearly_normals_one_row_per_period() -> None:
    from server.services.frost_climo import fetch_climo_yearly_for_periods

    async with _mock_client() as client:
        df = await fetch_climo_yearly_for_periods(
            client, "SN100", [PERIOD],
            client_id="ID", client_secret="SECRET",
        )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["date"] == pd.Timestamp(2020, 1, 1)
    assert row["temp_mean"] == pytest.approx(4.9)
    assert row["precip_total"] == pytest.approx(780.0)


@pytest.mark.asyncio
async def test_empty_inputs_return_empty_df() -> None:
    from server.services.frost_climo import (
        fetch_climo_monthly_for_period,
        fetch_climo_yearly_for_periods,
    )

    async with _mock_client() as client:
        df_m = await fetch_climo_monthly_for_period(
            client, "SN100", "", [1], client_id="I", client_secret="S",
        )
        df_y = await fetch_climo_yearly_for_periods(
            client, "SN100", [], client_id="I", client_secret="S",
        )
    assert df_m.empty and df_y.empty


def test_endpoint_uses_async_service_not_frontend_dispatch() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = _mock_client

    with patch(
        "utils.historical_dispatch.fetch_historical_dataset",
        side_effect=AssertionError("la rama FROST no debe pasar por el dispatcher frontend"),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/v1/climo/dataset",
                json={
                    "provider": "FROST",
                    "station_id": "SN100",
                    "api_key": "",
                    "summary_mode": "monthly",
                    "selected_months": [1, 7],
                    "frost_period": PERIOD,
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    df = pd.read_json(io.StringIO(body["dataset"]), orient="table")
    assert df["temp_mean"].tolist() == [-4.2, 14.8]


def test_endpoint_annual_mode_uses_periods() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = _mock_client

    with TestClient(app) as client:
        response = client.post(
            "/v1/climo/dataset",
            json={
                "provider": "FROST",
                "station_id": "SN100",
                "api_key": "",
                "summary_mode": "annual",
                "frost_periods": [PERIOD],
            },
        )

    assert response.status_code == 200
    df = pd.read_json(io.StringIO(response.json()["dataset"]), orient="table")
    assert len(df) == 1
    assert df["precip_total"].iloc[0] == pytest.approx(780.0)


# =====================================================================
# Periodos disponibles (selector de climogramas) — endpoint + parsing
# =====================================================================

def test_build_period_options_separates_monthly_and_annual_and_sorts():
    from domain.parsing.frost_climo import build_period_options

    available = {
        "1991/2020": ["mean(air_temperature P1M)", "mean(air_temperature P1Y)"],
        "1961/1990": ["sum(precipitation_amount P1M)"],  # solo mensual
        "sin_datos": ["elemento_irrelevante"],
    }
    opts = build_period_options(available)
    assert opts["monthly"] == ["1961/1990", "1991/2020"]   # ordenados por año
    assert opts["annual"] == ["1991/2020"]                  # solo el que tiene P1Y


def test_endpoint_frost_period_options() -> None:
    app = create_app()

    def _client_with_two_periods() -> httpx.AsyncClient:
        payload = {
            "data": [
                {"period": "1991/2020", "elementId": "mean(air_temperature P1M)"},
                {"period": "1991/2020", "elementId": "mean(air_temperature P1Y)"},
                {"period": "1961/1990", "elementId": "sum(precipitation_amount P1M)"},
            ]
        }
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)

    app.dependency_overrides[get_http_client] = _client_with_two_periods
    with TestClient(app) as client:
        response = client.post(
            "/v1/climo/frost/period-options", json={"station_id": "SN100"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["monthly"] == ["1961/1990", "1991/2020"]
    assert body["annual"] == ["1991/2020"]


def test_frontend_client_frost_period_options_non_fatal(monkeypatch) -> None:
    from utils.api_client import fetch_frost_period_options_via_api
    from utils.api_errors import BackendApiError

    # Backend caído → el selector debe quedar vacío, sin propagar el error.
    def _boom(*args, **kwargs):
        raise BackendApiError("network")

    monkeypatch.setattr("utils.api_client._post_observation_request", _boom)
    assert fetch_frost_period_options_via_api("SN100") == {"monthly": [], "annual": []}

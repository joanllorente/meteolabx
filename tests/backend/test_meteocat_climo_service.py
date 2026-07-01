"""
Tests del port async puro de la climatología de Meteocat
(``server/services/meteocat_climo.py``) y de su rama en
``POST /v1/climo/dataset`` (último proveedor que salía del dispatcher
legacy; además devuelve ``extremes``).
"""

from __future__ import annotations

import io
from datetime import date
from typing import Optional
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app
from domain.parsing import meteocat_climo as P

STATION = "C6"


def _daily_payload(*pairs):
    return {"valors": [{"data": f"{d}T00:00Z", "valor": v} for d, v in pairs]}


def _monthly_payload(*pairs):
    return {"valors": [{"data": f"{m}T00:00Z", "valor": v} for m, v in pairs]}


def _annual_payload(*pairs):
    return {"valors": [{"any": str(y), "valor": v, "data": f"{y}-07-15T00:00Z"} for y, v in pairs]}


def _var_from_path(path: str) -> int:
    return int(path.rstrip("/").rsplit("/", 1)[-1])


def _mock_client(routes, status: int = 200) -> httpx.AsyncClient:
    """``routes``: callable(kind, var, params) -> json|None (None = sin datos)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={})
        path = request.url.path
        params = dict(request.url.params)
        if "/estadistics/diaris/" in path:
            kind = "diaris"
        elif "/estadistics/mensuals/" in path:
            kind = "mensuals"
        elif "/estadistics/anuals/" in path:
            kind = "anuals"
        else:
            return httpx.Response(404, json={})
        body = routes(kind, _var_from_path(path), params)
        return httpx.Response(200, json=body if body is not None else {"valors": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# =====================================================================
# Histórico diario
# =====================================================================

@pytest.mark.asyncio
async def test_daily_history_converts_wind_and_filters_range() -> None:
    from server.services.meteocat_climo import fetch_daily_history_for_periods

    def routes(kind, var, params):
        if kind != "diaris":
            return {"valors": []}
        if var == P.STAT_TEMP_MEAN:
            return _daily_payload(("2025-06-01", 18.0), ("2025-06-02", 19.0))
        if var == P.STAT_TEMP_MAX:
            return _daily_payload(("2025-06-01", 24.0), ("2025-06-02", 26.0))
        if var == P.STAT_TEMP_MIN:
            return _daily_payload(("2025-06-01", 12.0), ("2025-06-02", 14.0))
        if var == P.STAT_PRECIP:
            return _daily_payload(("2025-06-01", 0.0), ("2025-06-02", 3.4))
        # Viento 2m/6m vacíos → debe caer al de 10 m (m/s)
        if var == P.STAT_WIND_MEAN_10:
            return _daily_payload(("2025-06-01", 2.0))   # 2 m/s → 7.2 km/h
        if var == P.STAT_GUST_MAX_10:
            return _daily_payload(("2025-06-01", 5.0))   # 5 m/s → 18 km/h
        return {"valors": []}

    async with _mock_client(routes) as client:
        df = await fetch_daily_history_for_periods(
            client, STATION, "K", [(date(2025, 6, 1), date(2025, 6, 2))],
        )

    assert len(df) == 2
    row = df.iloc[0]
    assert row["temp_mean"] == pytest.approx(18.0)
    assert row["temp_max"] == pytest.approx(24.0)
    assert row["wind_mean"] == pytest.approx(7.2)   # 10 m elegido, m/s→km/h
    assert row["gust_max"] == pytest.approx(18.0)
    assert df.iloc[1]["precip_total"] == pytest.approx(3.4)


# =====================================================================
# Histórico mensual (selección de candidato de viento + extremos abs)
# =====================================================================

@pytest.mark.asyncio
async def test_monthly_history_picks_wind_candidate_and_parses_abs_extremes() -> None:
    from server.services.meteocat_climo import fetch_monthly_history_for_year

    def routes(kind, var, params):
        if kind != "mensuals":
            return {"valors": []}
        if var == P.STAT_MO_TEMP_MEAN:
            return _monthly_payload(("2024-01", 9.5), ("2024-02", 10.5))
        if var == P.STAT_MO_TEMP_ABS_MAX:
            return {"valors": [{"data": "2024-01T00:00Z", "valor": 18.2, "dataExtrem": "2024-01-27"}]}
        if var == P.STAT_MO_PRECIP_TOTAL:
            return _monthly_payload(("2024-01", 120.0))
        # Solo el anemómetro de 6 m tiene datos
        if var == P.STAT_MO_WIND_MEAN_6:
            return _monthly_payload(("2024-01", 3.0))   # 3 m/s → 10.8 km/h
        return {"valors": []}

    async with _mock_client(routes) as client:
        df = await fetch_monthly_history_for_year(client, STATION, "K", 2024)

    # Construye las 12 filas del año (los meses sin datos quedan NaN), según el contrato canónico.
    assert len(df) == 12
    by_date = df.set_index(df["date"].dt.strftime("%Y-%m-%d"))
    jan = by_date.loc["2024-01-01"]
    assert jan["temp_mean"] == pytest.approx(9.5)
    assert jan["temp_abs_max"] == pytest.approx(18.2)
    assert jan["temp_abs_max_date"] == "2024-01-27"
    assert jan["precip_total"] == pytest.approx(120.0)
    assert jan["wind_mean"] == pytest.approx(10.8)   # candidato 6 m, m/s→km/h
    assert by_date.loc["2024-02-01"]["temp_mean"] == pytest.approx(10.5)


# =====================================================================
# Histórico anual
# =====================================================================

@pytest.mark.asyncio
async def test_annual_history_for_years() -> None:
    from server.services.meteocat_climo import fetch_annual_history_for_years

    def routes(kind, var, params):
        if kind != "anuals":
            return {"valors": []}
        if var == P.STAT_AN_TEMP_MEAN:
            return _annual_payload((2022, 14.0), (2023, 15.0))
        if var == P.STAT_AN_PRECIP_TOTAL:
            return _annual_payload((2022, 600.0), (2023, 720.0))
        if var == P.STAT_AN_TEMP_ABS_MAX:
            return _annual_payload((2022, 38.0), (2023, 39.5))
        return {"valors": []}

    async with _mock_client(routes) as client:
        df = await fetch_annual_history_for_years(client, STATION, "K", [2022, 2023])

    assert df["date"].tolist() == [pd.Timestamp("2022-01-01"), pd.Timestamp("2023-01-01")]
    assert df.iloc[1]["temp_mean"] == pytest.approx(15.0)
    assert df.iloc[1]["precip_total"] == pytest.approx(720.0)
    assert df.iloc[1]["temp_abs_max"] == pytest.approx(39.5)


# =====================================================================
# Extremos derivados
# =====================================================================

@pytest.mark.asyncio
async def test_daily_extremes_for_year() -> None:
    from server.services.meteocat_climo import fetch_daily_extremes_for_year

    def routes(kind, var, params):
        if kind != "diaris":
            return {"valors": []}
        mes = params.get("mes")
        if var == P.STAT_TEMP_MAX and mes == "01":      # invierno → mín de máximas
            return _daily_payload(("2024-01-10", 8.0), ("2024-01-11", 5.5))
        if var == P.STAT_TEMP_MIN and mes == "07":      # verano → máx de mínimas
            return _daily_payload(("2024-07-20", 21.0), ("2024-07-21", 23.5))
        if var == P.STAT_WIND_MEAN_2 and mes == "03":   # 2 m presente → día ventoso
            return _daily_payload(("2024-03-05", 9.0))  # 9 m/s → 32.4 km/h
        return {"valors": []}

    async with _mock_client(routes) as client:
        extremes = await fetch_daily_extremes_for_year(client, STATION, "K", 2024)

    assert extremes["Mínima de máximas"]["Valor"] == "5.5 °C"
    assert extremes["Mínima de máximas"]["Fecha"] == "11/01/2024"
    assert extremes["Máxima de mínimas"]["Valor"] == "23.5 °C"
    assert extremes["Día más ventoso (viento medio)"]["Valor"] == "32.4 km/h"


# =====================================================================
# Errores
# =====================================================================

@pytest.mark.asyncio
async def test_unauthorized_cuts_immediately() -> None:
    from server.schemas.errors import ProviderError
    from server.services.meteocat_climo import fetch_daily_history_for_periods

    async with _mock_client(lambda *a: None, status=403) as client:
        with pytest.raises(ProviderError) as excinfo:
            await fetch_daily_history_for_periods(
                client, STATION, "BAD", [(date(2025, 6, 1), date(2025, 6, 2))],
            )
    assert excinfo.value.error_code == "provider_unauthorized"


# =====================================================================
# Rama async en POST /v1/climo/dataset (con extremes)
# =====================================================================

def test_endpoint_uses_async_port_and_returns_extremes() -> None:
    def routes(kind, var, params):
        if kind == "diaris":
            mes = params.get("mes")
            if var == P.STAT_TEMP_MEAN:
                return _daily_payload(("2025-06-01", 20.0))
            if var == P.STAT_TEMP_MAX and mes == "06":
                return _daily_payload(("2025-06-01", 27.0))
            if var == P.STAT_TEMP_MIN and mes == "06":
                return _daily_payload(("2025-06-01", 15.0))
        return {"valors": []}

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: _mock_client(routes)

    with patch(
        "utils.historical_dispatch.fetch_historical_dataset",
        side_effect=AssertionError("la rama METEOCAT no debe pasar por el dispatcher frontend"),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/v1/climo/dataset",
                json={
                    "provider": "METEOCAT",
                    "station_id": STATION,
                    "api_key": "K",
                    "summary_mode": "monthly",
                    "periods": [{"label": "jun", "start": "2025-06-01", "end": "2025-06-30"}],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    df = pd.read_json(io.StringIO(body["dataset"]), orient="table")
    assert df["temp_mean"].iloc[0] == pytest.approx(20.0)

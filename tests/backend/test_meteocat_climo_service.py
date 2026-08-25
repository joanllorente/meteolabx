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
        if var == P.STAT_PRECIP_MAX_1MIN:
            return _daily_payload(("2025-06-01", 0.2), ("2025-06-02", 0.8))
        if var == P.STAT_SOLAR_GLOBAL:
            return _daily_payload(("2025-06-01", 18.4), ("2025-06-02", 21.6))
        # Viento 2m/6m vacíos → debe caer al de 10 m (m/s)
        if var == P.STAT_WIND_MEAN_10:
            return _daily_payload(("2025-06-01", 2.0))   # 2 m/s → 7.2 km/h
        if var == P.STAT_WIND_DIR_MEAN_10:
            return _daily_payload(("2025-06-01", 270.0))
        if var == P.STAT_GUST_MAX_10:
            return _daily_payload(("2025-06-01", 5.0))   # 5 m/s → 18 km/h
        if var == P.STAT_GUST_DIR_10:
            return _daily_payload(("2025-06-01", 225.0))
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
    assert row["wind_dir_mean"] == pytest.approx(270.0)
    assert row["gust_max"] == pytest.approx(18.0)
    assert row["gust_dir_max"] == pytest.approx(225.0)
    assert df.iloc[1]["precip_total"] == pytest.approx(3.4)
    # PPTx1min llega en mm/1 min y se normaliza a una tasa en mm/h.
    assert df.iloc[1]["precip_rate_max"] == pytest.approx(48.0)
    # La irradiación global diaria ya llega en MJ/m² y no se convierte.
    assert row["solar_mean"] == pytest.approx(18.4)
    assert df.iloc[1]["solar_mean"] == pytest.approx(21.6)


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
        if var == P.STAT_MO_PRECIP_MAX_1MIN:
            return {
                "valors": [
                    {"data": "2024-01T00:00Z", "valor": 1.1, "dataExtrem": "2024-01-27"}
                ]
            }
        if var == P.STAT_MO_FROST_DAYS:
            return _monthly_payload(("2024-01", 4.0), ("2024-02", 1.0))
        # Solo el anemómetro de 6 m tiene datos
        if var == P.STAT_MO_WIND_MEAN_6:
            return _monthly_payload(("2024-01", 3.0))   # 3 m/s → 10.8 km/h
        if var == P.STAT_MO_WIND_DIR_MEAN_6:
            return _monthly_payload(("2024-01", 180.0))
        if var == P.STAT_MO_GUST_MAX_6:
            return _monthly_payload(("2024-01", 7.0))
        if var == P.STAT_MO_GUST_DIR_6:
            return _monthly_payload(("2024-01", 202.5))
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
    assert jan["precip_rate_max"] == pytest.approx(66.0)
    assert jan["precip_rate_max_date"] == "2024-01-27"
    assert jan["wind_mean"] == pytest.approx(10.8)   # candidato 6 m, m/s→km/h
    assert jan["wind_dir_mean"] == pytest.approx(180.0)
    assert jan["gust_max"] == pytest.approx(25.2)
    assert jan["gust_dir_max"] == pytest.approx(202.5)
    assert jan["frost_nights"] == pytest.approx(4.0)
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
        if var == P.STAT_AN_PRECIP_MAX_1MIN:
            return _annual_payload((2022, 0.9), (2023, 1.3))
        if var == P.STAT_AN_TEMP_ABS_MAX:
            return _annual_payload((2022, 38.0), (2023, 39.5))
        if var == P.STAT_AN_FROST_DAYS:
            return _annual_payload((2022, 12.0), (2023, 7.0))
        if var == P.STAT_AN_WIND_DIR_MEAN_10:
            return _annual_payload((2022, 90.0), (2023, 270.0))
        if var == P.STAT_AN_GUST_MAX_10:
            return _annual_payload((2022, 20.0), (2023, 25.0))
        if var == P.STAT_AN_GUST_DIR_10:
            return _annual_payload((2022, 180.0), (2023, 225.0))
        return {"valors": []}

    async with _mock_client(routes) as client:
        df = await fetch_annual_history_for_years(client, STATION, "K", [2022, 2023])

    assert df["date"].tolist() == [pd.Timestamp("2022-01-01"), pd.Timestamp("2023-01-01")]
    assert df.iloc[1]["temp_mean"] == pytest.approx(15.0)
    assert df.iloc[1]["precip_total"] == pytest.approx(720.0)
    assert df.iloc[1]["precip_rate_max"] == pytest.approx(78.0)
    assert df.iloc[1]["temp_abs_max"] == pytest.approx(39.5)
    assert df.iloc[1]["frost_nights"] == pytest.approx(7.0)
    assert df.iloc[1]["wind_dir_mean"] == pytest.approx(270.0)
    assert df.iloc[1]["gust_max"] == pytest.approx(90.0)
    assert df.iloc[1]["gust_dir_max"] == pytest.approx(225.0)


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
    assert extremes["Noches tropicales (mín > 20 °C)"]["Valor"] == "2 noches"
    assert extremes["Noches tórridas (mín > 25 °C)"]["Valor"] == "0 noches"
    assert extremes["Día más ventoso (viento medio)"]["Valor"] == "32.4 km/h"


@pytest.mark.asyncio
async def test_multiple_months_keep_characteristic_night_counts() -> None:
    from server.services.meteocat_climo import fetch_climo_dataset

    def routes(kind, var, params):
        if kind == "mensuals" and var == P.STAT_MO_TEMP_MEAN:
            return _monthly_payload(("2025-06", 22.0), ("2025-07", 25.0))
        if kind == "mensuals" and var == P.STAT_MO_FROST_DAYS:
            return _monthly_payload(("2025-06", 0.0), ("2025-07", 0.0))
        if kind == "diaris" and var == P.STAT_TEMP_MIN:
            if params.get("mes") == "06":
                return _daily_payload(("2025-06-01", 19.0), ("2025-06-02", 20.0))
            if params.get("mes") == "07":
                return _daily_payload(("2025-07-01", 25.0), ("2025-07-02", 26.0))
        return {"valors": []}

    periods = [
        (date(2025, 6, 1), date(2025, 6, 30)),
        (date(2025, 7, 1), date(2025, 7, 31)),
    ]
    async with _mock_client(routes) as client:
        df, _ = await fetch_climo_dataset(
            client,
            STATION,
            "K",
            summary_mode="monthly",
            periods=periods,
            selected_years=[2025],
        )

    assert df["tropical_nights"].sum(min_count=1) == pytest.approx(3.0)
    assert df["torrid_nights"].sum(min_count=1) == pytest.approx(2.0)
    assert df["frost_nights"].sum(min_count=1) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_multiple_months_fetch_direction_only_for_windiest_day_month() -> None:
    from server.services.meteocat_climo import fetch_daily_extremes_for_periods

    direction_months = []

    def routes(kind, var, params):
        if kind != "diaris":
            return {"valors": []}
        month = params.get("mes")
        if var == P.STAT_WIND_MEAN_2 and month == "06":
            return _daily_payload(("2025-06-10", 2.0))
        if var == P.STAT_WIND_MEAN_2 and month == "07":
            return _daily_payload(("2025-07-11", 4.0))
        if var == P.STAT_WIND_DIR_MEAN_2:
            direction_months.append(month)
            if month == "07":
                return _daily_payload(("2025-07-11", 172.0))
        return {"valors": []}

    periods = [
        (date(2025, 6, 1), date(2025, 6, 30)),
        (date(2025, 7, 1), date(2025, 7, 31)),
    ]
    async with _mock_client(routes) as client:
        extremes = await fetch_daily_extremes_for_periods(
            client, STATION, "K", periods
        )

    assert extremes["Día más ventoso (viento medio)"]["Dirección"] == "172.0"
    assert direction_months == ["07"]


@pytest.mark.asyncio
async def test_single_month_reuses_daily_series_for_extremes() -> None:
    """Las cards no deben volver a pedir máximas, mínimas ni viento."""
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.meteocat_climo import fetch_climo_dataset

    clear_climo_block_cache()
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        var = _var_from_path(request.url.path)
        calls[var] = calls.get(var, 0) + 1
        if var == P.STAT_TEMP_MAX:
            body = _daily_payload(("2025-06-01", 27.0))
        elif var == P.STAT_TEMP_MIN:
            body = _daily_payload(("2025-06-01", 21.0))
        elif var == P.STAT_WIND_MEAN_2:
            body = _daily_payload(("2025-06-01", 2.0))
        else:
            body = {"valors": []}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    ) as client:
        frame, extremes = await fetch_climo_dataset(
            client,
            STATION,
            "K",
            summary_mode="monthly",
            periods=[(date(2025, 6, 1), date(2025, 6, 30))],
            selected_years=[2025],
        )

    assert not frame.empty
    assert extremes["Mínima de máximas"]["Valor"] == "27.0 °C"
    assert extremes["Máxima de mínimas"]["Valor"] == "21.0 °C"
    assert calls[P.STAT_TEMP_MAX] == 1
    assert calls[P.STAT_TEMP_MIN] == 1
    assert calls[P.STAT_WIND_MEAN_2] == 1


@pytest.mark.asyncio
async def test_single_year_uses_native_monthly_summary_without_daily_enrichment() -> None:
    from server.services.meteocat_climo import fetch_climo_dataset

    def routes(kind, var, params):
        if kind == "mensuals" and var == P.STAT_MO_TEMP_MEAN:
            return _monthly_payload(("2025-01", 9.0), ("2025-07", 25.0))
        return {"valors": []}

    async with _mock_client(routes) as client:
        with patch(
            "server.services.meteocat_climo.fetch_daily_extremes_for_year",
            side_effect=AssertionError("el resumen anual no debe forzar consultas diarias"),
        ):
            frame, extremes = await fetch_climo_dataset(
                client,
                STATION,
                "K",
                summary_mode="annual",
                periods=[(date(2025, 1, 1), date(2025, 12, 31))],
                selected_years=[2025],
            )

    assert frame["temp_mean"].notna().sum() == 2
    assert extremes is None


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

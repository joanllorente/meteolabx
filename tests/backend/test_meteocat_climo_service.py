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


def _open_data_down():
    """Dades Obertes caído: ``fetch_climo_dataset`` recurre a la API XEMA."""
    from server.schemas.errors import ProviderError

    async def _fail(*_args, **_kwargs):
        raise ProviderError(
            "provider_timeout", provider="METEOCAT", detail="caído", status_code=504,
        )

    return patch("server.services.meteocat_climo.fetch_open_data_daily_for_periods", _fail)


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
        if kind == "diaris" and var == P.STAT_TEMP_MEAN:
            if params.get("mes") == "06":
                return _daily_payload(("2025-06-01", 21.0), ("2025-06-02", 22.0))
            if params.get("mes") == "07":
                return _daily_payload(("2025-07-01", 27.0), ("2025-07-02", 28.0))
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
        with _open_data_down():
            df, _ = await fetch_climo_dataset(
                client,
                STATION,
                "K",
                summary_mode="monthly",
                periods=periods,
                selected_years=[2025],
            )

    assert len(df) == 4
    assert df["temp_min"].tolist() == [19.0, 20.0, 25.0, 26.0]
    assert df["temp_mean"].tolist() == [21.0, 22.0, 27.0, 28.0]


@pytest.mark.asyncio
async def test_discontinuous_months_do_not_fetch_or_include_months_between_them() -> None:
    from server.services.meteocat_climo import fetch_daily_history_for_periods

    requested = []

    def routes(kind, var, params):
        if kind != "diaris":
            return {"valors": []}
        requested.append((params.get("any"), params.get("mes")))
        if var == P.STAT_TEMP_MEAN:
            year = params.get("any")
            return _daily_payload((f"{year}-08-01", 25.0))
        return {"valors": []}

    periods = [
        (date(2021, 8, 1), date(2021, 8, 31)),
        (date(2025, 8, 1), date(2025, 8, 31)),
    ]
    async with _mock_client(routes) as client:
        df = await fetch_daily_history_for_periods(client, STATION, "K", periods)

    assert sorted(set(requested)) == [("2021", "08"), ("2025", "08")]
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2021-08-01", "2025-08-01"]


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
        with _open_data_down():
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
        with _open_data_down(), patch(
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

def test_endpoint_serves_the_dataset_and_its_extremes() -> None:
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

    with _open_data_down(), TestClient(app) as client:
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


# =====================================================================
# Dades Obertes: diarios en cualquier modo, sin cuota
# =====================================================================

def _open_data_client(daily_rows, half_hourly_rows=None, seen=None):
    """Simula los dos datasets Socrata: diario (7bvh-jvq2) y semihorario."""
    from urllib.parse import parse_qs

    def handler(request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if seen is not None:
            seen.append((request.url.path, query.get("$where", "")))
        if request.url.path.endswith("/7bvh-jvq2.json"):
            where = query["$where"]
            lower = where.split("data_lectura >= '")[1][:10]
            upper = where.split("data_lectura <= '")[1][:10]
            rows = [row for row in daily_rows if lower <= row["data_lectura"][:10] <= upper]
            return httpx.Response(200, json=rows)
        if request.url.path.endswith("/nzvn-apee.json"):
            return httpx.Response(200, json=list(half_hourly_rows or []))
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _daily_row(day, code, value, estat="Representatiu"):
    return {
        "data_lectura": f"{day}T00:00:00.000", "codi_variable": str(code),
        "valor": str(value), "estat": estat,
    }


@pytest.mark.asyncio
async def test_open_data_serves_days_for_a_whole_year_without_api_key() -> None:
    """Un año entero llega como días, en una petición y sin key XEMA."""
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.meteocat_climo import fetch_climo_dataset

    clear_climo_block_cache()
    rows = [
        _daily_row("2025-01-10", P.STAT_TEMP_MAX, 9.5),
        _daily_row("2025-01-10", P.STAT_TEMP_MIN, 1.0),
        _daily_row("2025-01-11", P.STAT_TEMP_MAX, 7.0),
        _daily_row("2025-01-11", P.STAT_TEMP_MIN, -2.0),
        _daily_row("2025-07-20", P.STAT_TEMP_MAX, 35.0),
        _daily_row("2025-07-20", P.STAT_TEMP_MIN, 24.5),
        _daily_row("2025-07-21", P.STAT_TEMP_MAX, 33.0),
        _daily_row("2025-07-21", P.STAT_TEMP_MIN, 22.0),
        # 10 m sin 2 m: se usa la altura disponible, con su dirección.
        _daily_row("2025-07-20", P.STAT_WIND_MEAN_10, 5.0),
        _daily_row("2025-07-20", P.STAT_WIND_DIR_MEAN_10, 225.0),
        _daily_row("2025-07-21", P.STAT_PRECIP_MAX_1MIN, 0.5),
        # Lectura invalidada por Meteocat: fuera.
        _daily_row("2025-07-22", P.STAT_TEMP_MAX, 60.0, estat="No representatiu"),
    ]
    seen = []
    async with _open_data_client(rows, seen=seen) as client:
        frame, extremes = await fetch_climo_dataset(
            client, STATION, "",
            summary_mode="annual",
            periods=[(date(2025, 1, 1), date(2025, 12, 31))],
            selected_years=[2025],
        )

    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-01-10", "2025-01-11", "2025-07-20", "2025-07-21",
    ]
    assert frame["temp_max"].max() == pytest.approx(35.0)
    july = frame.set_index(frame["date"].dt.strftime("%Y-%m-%d"))
    assert july.loc["2025-07-20", "wind_mean"] == pytest.approx(18.0)
    assert july.loc["2025-07-20", "wind_dir_mean"] == pytest.approx(225.0)
    assert july.loc["2025-07-21", "precip_rate_max"] == pytest.approx(30.0)
    # Los hitos que con «mensuals» no se podían calcular.
    assert extremes["Mínima de máximas"]["Valor"] == "7.0 °C"
    assert extremes["Máxima de mínimas"]["Valor"] == "24.5 °C"
    assert [path for path, _ in seen] == ["/resource/7bvh-jvq2.json"]


@pytest.mark.asyncio
async def test_open_data_fetches_one_block_per_touched_year_only() -> None:
    """Agosto de 2015 y de 2025: dos bloques, no los nueve años de en medio."""
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.meteocat_climo import fetch_open_data_daily_for_periods

    clear_climo_block_cache()
    rows = [
        _daily_row("2015-08-03", P.STAT_TEMP_MAX, 30.0),
        _daily_row("2020-08-03", P.STAT_TEMP_MAX, 31.0),
        _daily_row("2025-08-03", P.STAT_TEMP_MAX, 32.0),
    ]
    seen = []
    async with _open_data_client(rows, seen=seen) as client:
        frame = await fetch_open_data_daily_for_periods(
            client, STATION,
            [(date(2015, 8, 1), date(2015, 8, 31)), (date(2025, 8, 1), date(2025, 8, 31))],
            today=date(2026, 9, 14),
        )

    assert frame["temp_max"].tolist() == [30.0, 32.0]
    years = sorted(where.split("data_lectura >= '")[1][:4] for _path, where in seen)
    assert years == ["2015", "2025"]


@pytest.mark.asyncio
async def test_open_data_fills_the_days_not_yet_published_from_half_hourly() -> None:
    """El diario va dos días por detrás: el hueco sale de las semihorarias."""
    from datetime import datetime, timezone

    from server.services import meteocat as mc
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.meteocat_climo import fetch_open_data_daily_for_periods

    clear_climo_block_cache()
    daily = [_daily_row("2026-09-12", P.STAT_TEMP_MAX, 28.1)]
    start = int(datetime(2026, 9, 13, tzinfo=timezone.utc).timestamp())
    half_hourly = []
    for step in range(48):
        stamp = datetime.fromtimestamp(start + step * 1800, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for code, value in (
            (mc.V_TEMP, 20.0 + step / 10),
            (mc.V_TEMP_MAX, 20.5 + step / 10),
            (mc.V_TEMP_MIN, 19.5 + step / 10),
            (mc.V_PRECIP, 0.1),
        ):
            half_hourly.append({
                "codi_estacio": STATION, "codi_variable": str(code),
                "data_lectura": stamp, "valor_lectura": str(value),
            })
    # Hoy, a medias: no debe aparecer.
    half_hourly.append({
        "codi_estacio": STATION, "codi_variable": str(mc.V_TEMP),
        "data_lectura": "2026-09-14T08:00:00", "valor_lectura": "30",
    })

    async with _open_data_client(daily, half_hourly) as client:
        frame = await fetch_open_data_daily_for_periods(
            client, STATION, [(date(2026, 9, 1), date(2026, 9, 30))],
            today=date(2026, 9, 14),
        )

    by_day = frame.set_index(frame["date"].dt.strftime("%Y-%m-%d"))
    assert list(by_day.index) == ["2026-09-12", "2026-09-13"]
    assert by_day.loc["2026-09-13", "temp_max"] == pytest.approx(20.5 + 4.7)
    assert by_day.loc["2026-09-13", "temp_min"] == pytest.approx(19.5)
    assert by_day.loc["2026-09-13", "precip_total"] == pytest.approx(4.8)


@pytest.mark.asyncio
async def test_open_data_failure_falls_back_to_xema() -> None:
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.meteocat_climo import fetch_climo_dataset

    clear_climo_block_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if "transparenciacatalunya" in str(request.url):
            return httpx.Response(503, text="mantenimiento")
        if "/estadistics/diaris/" in request.url.path and _var_from_path(request.url.path) == P.STAT_TEMP_MAX:
            return httpx.Response(200, json=_daily_payload(("2025-06-01", 27.0)))
        return httpx.Response(200, json={"valors": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        frame, _ = await fetch_climo_dataset(
            client, STATION, "K",
            summary_mode="monthly",
            periods=[(date(2025, 6, 1), date(2025, 6, 30))],
            selected_years=[2025],
        )

    assert frame["temp_max"].tolist() == [27.0]

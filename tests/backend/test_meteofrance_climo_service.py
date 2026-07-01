"""
Tests del port async puro de la climatología de Météo-France
(``server/services/meteofrance_climo.py``) y de su rama en
``POST /v1/climo/dataset``.
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app

DAILY_CSV = (
    "POSTE;DATE;RR;TN;TX;TM;FFM;FXI\n"
    "01014002;20250601;0,4;12,5;27,0;19,8;2,5;15,0\n"
    "01014002;20250602;12,0;21,0;26,0;;3,0;\n"
)

MONTHLY_CSV = (
    "POSTE;DATE;RR;TM;TX;TN;TXAB;TXDAT;TNAB;TNDAT;RRAB;RRABDAT;NBJRR1;NBJGELEE;FXIAB;FXIDAT\n"
    "01014002;202401;120,0;9,5;13,0;6,0;18,2;20240127;-1,5;20240103;25,0;20240115;14;3;21,1;20240107\n"
    "01014002;202402;80,0;10,5;14,0;7,0;21,0;20240211;0,5;20240220;18,0;20240224;10;1;;\n"
)


def _mock_client(daily_csv=DAILY_CSV, monthly_csv=MONTHLY_CSV, record=None) -> httpx.AsyncClient:
    state = {"pending": {}}
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if record is not None:
            record.setdefault("paths", []).append(path)
        if "commande-station/quotidienne" in path:
            counter["n"] += 1
            cmd = f"daily-{counter['n']}"
            state["pending"][cmd] = ("daily", 1)  # un 204 antes del CSV
            return httpx.Response(202, json={"elaboreProduitAvecDemandeResponse": {"return": cmd}})
        if "commande-station/mensuelle" in path:
            counter["n"] += 1
            cmd = f"monthly-{counter['n']}"
            state["pending"][cmd] = ("monthly", 0)
            return httpx.Response(202, json={"elaboreProduitAvecDemandeResponse": {"return": cmd}})
        if path.endswith("/commande/fichier"):
            cmd = request.url.params.get("id-cmde", "")
            kind, waits = state["pending"].get(cmd, (None, 0))
            if kind is None:
                return httpx.Response(404, text="")
            if waits > 0:
                state["pending"][cmd] = (kind, waits - 1)
                return httpx.Response(204, text="")
            return httpx.Response(201, text=daily_csv if kind == "daily" else monthly_csv)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.mark.asyncio
async def test_daily_periods_polls_commande_and_parses_csv() -> None:
    from server.services.meteofrance_climo import fetch_climo_daily_for_periods

    async with _mock_client() as client:
        df = await fetch_climo_daily_for_periods(
            client, "01014002", "K", [(date(2025, 6, 1), date(2025, 6, 30))],
        )

    assert len(df) == 2
    day1 = df.iloc[0]
    assert day1["temp_mean"] == pytest.approx(19.8)   # coma decimal
    assert day1["precip_total"] == pytest.approx(0.4)
    assert day1["gust_max"] == pytest.approx(15.0)
    day2 = df.iloc[1]
    assert day2["temp_mean"] == pytest.approx(23.5)   # sin TM → (TN+TX)/2
    assert day2["rain_days"] == pytest.approx(1.0)    # RR ≥ 1 mm
    assert day2["tropical_nights"] == pytest.approx(1.0)  # TN ≥ 20


@pytest.mark.asyncio
async def test_daily_periods_clips_to_requested_window() -> None:
    from server.services.meteofrance_climo import fetch_climo_daily_for_periods

    async with _mock_client() as client:
        df = await fetch_climo_daily_for_periods(
            client, "01014002", "K", [(date(2025, 6, 2), date(2025, 6, 2))],
        )
    # La commande cubre todo el rango anual pedido, pero el resultado se
    # recorta al periodo solicitado
    assert df["date"].tolist() == [pd.Timestamp("2025-06-02")]


@pytest.mark.asyncio
async def test_monthly_year_parses_absolute_extremes_with_dates() -> None:
    from server.services.meteofrance_climo import fetch_climo_monthly_for_year

    async with _mock_client() as client:
        df = await fetch_climo_monthly_for_year(client, "01014002", "K", 2024)

    assert len(df) == 2
    jan = df.iloc[0]
    assert jan["temp_abs_max"] == pytest.approx(18.2)
    assert jan["temp_abs_max_date"] == "2024-01-27"
    assert jan["temp_abs_min"] == pytest.approx(-1.5)
    assert jan["rain_days"] == pytest.approx(14.0)
    assert jan["frost_nights"] == pytest.approx(3.0)
    assert jan["gust_max"] == pytest.approx(21.1)
    assert jan["gust_abs_max_date"] == "2024-01-07"


@pytest.mark.asyncio
async def test_yearly_aggregates_monthlies() -> None:
    from server.services.meteofrance_climo import fetch_climo_yearly_for_years

    async with _mock_client() as client:
        df = await fetch_climo_yearly_for_years(client, "01014002", "K", [2024])

    assert len(df) == 1
    row = df.iloc[0]
    assert row["precip_total"] == pytest.approx(200.0)   # suma
    assert row["temp_abs_max"] == pytest.approx(21.0)    # máximo
    assert row["temp_abs_min"] == pytest.approx(-1.5)    # mínimo
    assert row["frost_nights"] == pytest.approx(4.0)     # suma 3+1
    assert row["rain_days"] == pytest.approx(24.0)


@pytest.mark.asyncio
async def test_unauthorized_cuts_immediately() -> None:
    from server.schemas.errors import ProviderError
    from server.services.meteofrance_climo import fetch_climo_daily_for_periods

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        with pytest.raises(ProviderError) as excinfo:
            await fetch_climo_daily_for_periods(
                client, "01014002", "BAD", [(date(2025, 6, 1), date(2025, 6, 5))],
            )
    assert excinfo.value.error_code == "provider_unauthorized"


@pytest.mark.asyncio
async def test_failed_commande_degrades_to_empty() -> None:
    from server.services.meteofrance_climo import fetch_climo_daily_for_periods

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with client:
        df = await fetch_climo_daily_for_periods(
            client, "01014002", "K", [(date(2025, 6, 1), date(2025, 6, 5))],
        )
    assert df.empty


def test_endpoint_uses_async_service_not_frontend_dispatch() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = _mock_client

    with patch(
        "utils.historical_dispatch.fetch_historical_dataset",
        side_effect=AssertionError("la rama METEOFRANCE no debe pasar por el dispatcher frontend"),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/v1/climo/dataset",
                json={
                    "provider": "METEOFRANCE",
                    "station_id": "01014002",
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
    assert df["temp_mean"].iloc[0] == pytest.approx(19.8)

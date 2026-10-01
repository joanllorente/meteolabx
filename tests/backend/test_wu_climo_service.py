"""
Tests del port async puro del histórico diario de WU
(``server/services/wu_climo.py``) y de su rama en
``POST /v1/climo/dataset``.
"""

from __future__ import annotations

import io
import math
from datetime import date, timedelta
from typing import Optional
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app


def _history_payload(*days: str) -> dict:
    return {
        "observations": [
            {
                "stationID": "IBARCE12345",
                "obsTimeLocal": f"{day} 23:59:00",
                "epoch": 1700000000 + i,
                "metric": {
                    "tempAvg": 15.0 + i,
                    "tempHigh": 20.0 + i,
                    "tempLow": 10.0 + i,
                    "windspeedAvg": 8.0,
                    "windgustHigh": 30.0,
                    "precipTotal": 1.2,
                },
                "winddirAvg": 180,
            }
            for i, day in enumerate(days)
        ]
    }


def _mock_client(
    record: Optional[dict] = None, status: int = 200, hourly: Optional[dict] = None,
) -> httpx.AsyncClient:
    """WU simulado. Las peticiones al horario, que solo sirve para rehacer la
    lluvia, se apuntan aparte: las cuentas de bloques miran el resumen diario."""
    def handler(request: httpx.Request) -> httpx.Response:
        es_horario = request.url.path.endswith("/hourly")
        if record is not None:
            record.setdefault("hourly" if es_horario else "requests", []).append(request)
        if status != 200:
            return httpx.Response(status, json={})
        if es_horario:
            return httpx.Response(200, json=hourly or {"observations": []})
        start = request.url.params.get("startDate", "")
        day = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        return httpx.Response(200, json=_history_payload(day))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.mark.asyncio
async def test_daily_history_normalizes_schema() -> None:
    from server.services.wu_climo import fetch_climo_daily_for_periods

    async with _mock_client() as client:
        df = await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [(date(2025, 6, 1), date(2025, 6, 30))],
            today_date=date(2025, 7, 15),
        )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["date"] == pd.Timestamp("2025-06-01")
    assert row["temp_mean"] == pytest.approx(15.0)
    assert row["temp_max"] == pytest.approx(20.0)
    assert row["wind_dir_mean"] == pytest.approx(180.0)
    # Precip cuantizada al tip del pluviómetro (1.2 mm → 3 tips de 0.4)
    assert row["precip_total"] == pytest.approx(1.2, abs=0.01)


@pytest.mark.asyncio
async def test_long_period_split_in_31_day_chunks() -> None:
    from server.services.wu_climo import fetch_climo_daily_for_periods

    record: dict = {}
    async with _mock_client(record) as client:
        await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [(date(2024, 1, 1), date(2024, 12, 31))],  # año entero
            today_date=date(2025, 7, 15),
        )

    requests = record["requests"]
    assert len(requests) == 12  # 366 días / 31 → 12 chunks
    first = requests[0].url.params
    assert first["startDate"] == "20240101"
    assert first["endDate"] == "20240131"
    assert first["apiKey"] == "K"


@pytest.mark.asyncio
async def test_expanding_month_selection_reuses_closed_months() -> None:
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.wu_climo import fetch_climo_daily_for_periods

    clear_climo_block_cache()
    record: dict = {}
    async with _mock_client(record) as client:
        await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [(date(2025, 1, 1), date(2025, 1, 31))],
            today_date=date(2025, 7, 15),
        )
        await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [
                (date(2025, 1, 1), date(2025, 1, 31)),
                (date(2025, 2, 1), date(2025, 2, 28)),
            ],
            today_date=date(2025, 7, 15),
        )

    assert len(record["requests"]) == 2
    assert [request.url.params["startDate"] for request in record["requests"]] == [
        "20250101", "20250201",
    ]


@pytest.mark.asyncio
async def test_future_periods_clipped_to_today() -> None:
    from server.services.wu_climo import fetch_climo_daily_for_periods

    record: dict = {}
    today = date(2025, 6, 10)
    async with _mock_client(record) as client:
        await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [
                (date(2025, 6, 1), date(2025, 6, 30)),   # se recorta al día 10
                (date(2025, 7, 1), date(2025, 7, 31)),   # futuro → fuera
            ],
            today_date=today,
        )

    requests = record["requests"]
    assert len(requests) == 1
    assert requests[0].url.params["endDate"] == "20250610"


@pytest.mark.asyncio
async def test_unauthorized_raises_provider_error() -> None:
    from server.schemas.errors import ProviderError
    from server.services.wu_climo import fetch_climo_daily_for_periods

    async with _mock_client(status=401) as client:
        with pytest.raises(ProviderError) as excinfo:
            await fetch_climo_daily_for_periods(
                client, "IBARCE12345", "BADKEY",
                [(date(2025, 6, 1), date(2025, 6, 5))],
                today_date=date(2025, 7, 1),
            )
    assert excinfo.value.error_code == "provider_unauthorized"


@pytest.mark.asyncio
async def test_http_error_chunk_degrades_to_empty() -> None:
    from server.services.wu_climo import fetch_climo_daily_for_periods

    async with _mock_client(status=502) as client:
        df = await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [(date(2025, 6, 1), date(2025, 6, 5))],
            today_date=date(2025, 7, 1),
        )
    assert df.empty


@pytest.mark.asyncio
async def test_http_error_chunk_is_not_cached_as_empty_history() -> None:
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.wu_climo import fetch_climo_daily_for_periods

    clear_climo_block_cache()
    record: dict = {}
    async with _mock_client(record, status=502) as client:
        for _ in range(2):
            df = await fetch_climo_daily_for_periods(
                client, "IBARCE12345", "K",
                [(date(2025, 6, 1), date(2025, 6, 5))],
                today_date=date(2025, 7, 1),
            )
            assert df.empty

    assert len(record["requests"]) == 2


def test_endpoint_serves_the_dataset_from_the_async_service() -> None:
    app = create_app()
    app.dependency_overrides[get_http_client] = _mock_client

    yesterday = date.today() - timedelta(days=1)
    with TestClient(app) as client:
        response = client.post(
            "/v1/climo/dataset",
            json={
                "provider": "WU",
                "station_id": "IBARCE12345",
                "api_key": "K",
                "summary_mode": "monthly",
                "periods": [{
                    "label": "ayer",
                    "start": yesterday.isoformat(),
                    "end": yesterday.isoformat(),
                }],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    df = pd.read_json(io.StringIO(body["dataset"]), orient="table")
    assert df["temp_mean"].iloc[0] == pytest.approx(15.0)



def _hora(local: str, total: float, epoch: int) -> dict:
    return {"obsTimeLocal": local, "epoch": epoch, "metric": {"precipTotal": total}}


def test_hourly_counter_rebuilds_rain_that_the_daily_summary_counts_twice() -> None:
    """ILHOSP26, 29-30/09/2026: la estación pone el contador a cero a la 01:00.

    El resumen diario daba 70,36 mm los dos días; el 30 fue seco. Con lluvia
    pasada la medianoche, esa hora es del día nuevo y no del anterior.
    """
    from domain.parsing.wu_climo import daily_precip_from_hourly

    horas = [
        _hora("2026-09-28 23:59:54", 0.0, 0),        # referencia: el día antes
        _hora("2026-09-29 00:59:54", 0.0, 1),
        _hora("2026-09-29 12:59:54", 70.36, 2),
        _hora("2026-09-29 23:59:54", 70.36, 3),
        _hora("2026-09-30 00:59:54", 72.0, 4),       # sigue lloviendo tras medianoche
        _hora("2026-09-30 01:59:54", 0.5, 5),        # puesta a cero y lluvia nueva
        _hora("2026-09-30 23:59:54", 0.5, 6),
    ]
    totales = daily_precip_from_hourly({"observations": horas})
    from domain.parsing.wu_climo import quantize_rain_mm_wu
    assert totales["2026-09-29"] == quantize_rain_mm_wu(70.36)
    assert totales["2026-09-30"] == quantize_rain_mm_wu(1.64 + 0.5)
    assert "2026-09-28" not in totales  # la referencia no se cuenta


@pytest.mark.asyncio
async def test_daily_rain_comes_from_the_hourly_counter_when_available() -> None:
    from server.services.climo_cache import clear_climo_block_cache
    from server.services.wu_climo import fetch_climo_daily_for_periods

    clear_climo_block_cache()
    record: dict = {}
    horario = {"observations": [
        _hora("2025-05-31 23:59:00", 4.0, 0),
        _hora("2025-06-01 00:59:00", 4.0, 1),   # arrastre del día anterior: no cuenta
        _hora("2025-06-01 01:59:00", 0.0, 2),
        _hora("2025-06-01 23:59:00", 0.0, 3),
    ]}
    async with _mock_client(record, hourly=horario) as client:
        df = await fetch_climo_daily_for_periods(
            client, "IBARCE12345", "K",
            [(date(2025, 6, 1), date(2025, 6, 1))],
            today_date=date(2025, 7, 1),
        )
    # El resumen diario simulado decía 1,2 mm; el horario, que no llovió.
    assert df["precip_total"].tolist() == [0.0]
    # El horario empieza el día antes, para tener el contador de partida.
    assert record["hourly"][0].url.params["startDate"] == "20250531"

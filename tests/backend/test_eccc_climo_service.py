"""
Tests del servicio climo ``server.services.eccc_climo``.

Cubren la resolución msc_id → climate_identifier, el parsing de fechas
diarias y mensuales de climate-daily/monthly y la selección de modos.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from server.services import eccc_climo


def test_climo_service_does_not_import_streamlit() -> None:
    source = Path("server/services/eccc_climo.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_resolve_climate_id_from_swob_and_climate_rows() -> None:
    # SWOB Bancroft: su climate_identifier viene del inventario.
    assert eccc_climo._resolve_climate_id("616I001") == "616I001"
    # Estación desconocida → sin serie.
    assert eccc_climo._resolve_climate_id("NO_EXISTE") == ""


def _daily_payload():
    return {
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "LOCAL_DATE": "2026-07-13 00:00:00",
                    "MEAN_TEMPERATURE": 24.0,
                    "MAX_TEMPERATURE": 31.4,
                    "MIN_TEMPERATURE": 17.2,
                    "TOTAL_PRECIPITATION": -0.5,  # traza → 0
                    "SPEED_MAX_GUST": 37.0,
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "LOCAL_DATE": "2026-07-14 00:00:00",
                    "MEAN_TEMPERATURE": None,
                    "MAX_TEMPERATURE": 31.9,
                    "MIN_TEMPERATURE": 19.9,
                    "TOTAL_PRECIPITATION": 0.0,
                    "SPEED_MAX_GUST": None,
                },
            },
        ],
    }


def test_fetch_climo_daily_dataset() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_daily_payload())

    async def _test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await eccc_climo.fetch_climo_dataset(
                client, "616I001", summary_mode="monthly",
                periods=[(date(2026, 7, 13), date(2026, 7, 14))], selected_years=[],
            )

    df = asyncio.run(_test())
    assert len(df) == 2
    assert captured["params"]["CLIMATE_IDENTIFIER"] == "616I001"
    assert df.iloc[0]["gust_max"] == pytest.approx(37.0)
    assert df.iloc[0]["precip_total"] == pytest.approx(0.0)  # traza saneada
    assert df.iloc[1]["temp_max"] == pytest.approx(31.9)


def test_monthly_local_date_gets_day_suffix() -> None:
    payload = {
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "LOCAL_DATE": "2010-01",
                    "MEAN_TEMPERATURE": -9.2,
                    "TOTAL_PRECIPITATION": 72.5,
                },
            }
        ],
    }
    rows = eccc_climo._rows_from_features(payload, eccc_climo.MONTHLY_FIELD_MAP)
    assert rows[0]["date"] == "2010-01-01"
    assert rows[0]["temp_mean"] == pytest.approx(-9.2)

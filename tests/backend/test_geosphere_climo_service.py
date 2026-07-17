"""
Tests del servicio climo ``server.services.geosphere_climo``.

Cubren la extracción de filas del Data Hub (centinela -1.0 de lluvia,
conversión m/s → km/h), la resolución TAWES → serie klima canónica y
los tres modos del dataset.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from domain.parsing.geosphere_climo import DAILY_PARAM_MAP, extract_climo_rows
from server.services import geosphere_climo


def _daily_payload():
    return {
        "timestamps": ["2026-07-13T00:00+00:00", "2026-07-14T00:00+00:00"],
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "station": 105,
                    "parameters": {
                        "tl_mittel": {"data": [26.2, 25.7]},
                        "tlmax": {"data": [31.4, 33.2]},
                        "tlmin": {"data": [21.0, 18.1]},
                        "rr": {"data": [-1.0, 0.1]},
                        "ffx": {"data": [10.1, 8.9]},
                        "vv_mittel": {"data": [3.0, 2.5]},
                        "so_h": {"data": [12.4, 11.0]},
                    },
                },
            }
        ],
    }


def test_climo_service_does_not_import_streamlit() -> None:
    source = Path("server/services/geosphere_climo.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_extract_rows_units_and_rain_sentinel() -> None:
    rows = extract_climo_rows(_daily_payload(), DAILY_PARAM_MAP)
    assert len(rows) == 2
    first, second = rows
    assert first["date"] == "2026-07-13"
    assert first["precip_total"] == pytest.approx(0.0)  # -1.0 → sin precipitación
    assert second["precip_total"] == pytest.approx(0.1)
    assert first["gust_max"] == pytest.approx(36.36)    # m/s → km/h
    assert first["wind_mean"] == pytest.approx(10.8)
    assert first["temp_max"] == pytest.approx(31.4)
    assert first["solar_hours"] == pytest.approx(12.4)


def test_resolve_klima_id_from_tawes_and_klima_rows() -> None:
    # TAWES Wien/Hohe Warte → serie combinada 105 del archivo.
    assert geosphere_climo._resolve_klima_id("11035") == "105"
    # Estación KLIMA con prefijo K → su propio id numérico.
    assert geosphere_climo._resolve_klima_id("K18617") == "18617"
    # Estación desconocida → sin serie.
    assert geosphere_climo._resolve_klima_id("00000") == ""


def test_fetch_climo_daily_dataset() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_daily_payload())

    async def _test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await geosphere_climo.fetch_climo_dataset(
                client, "11035", summary_mode="monthly",
                periods=[(date(2026, 7, 13), date(2026, 7, 14))], selected_years=[],
            )

    df = asyncio.run(_test())
    assert len(df) == 2
    assert captured["params"]["station_ids"] == "105"  # id klima, no el TAWES
    assert df["temp_max"].tolist() == pytest.approx([31.4, 33.2])
    assert df["precip_total"].tolist() == pytest.approx([0.0, 0.1])


def test_fetch_climo_dataset_without_klima_series_is_empty() -> None:
    async def _test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), timeout=5.0,
        ) as client:
            return await geosphere_climo.fetch_climo_dataset(
                client, "00000", summary_mode="monthly",
                periods=[(date(2026, 7, 1), date(2026, 7, 14))], selected_years=[],
            )

    df = asyncio.run(_test())
    assert df.empty

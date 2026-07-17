"""
Tests del servicio climo ``server.services.smhi_climo``.

Cubren el parsing del CSV del corrected-archive (bloques de metadatos,
notas en columnas extra, calidades), la costura con latest-months (la
fuente fresca gana en fechas repetidas) y la selección de modos.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from domain.parsing.smhi_climo import (
    merge_field_rows,
    parse_archive_csv,
    parse_recent_json,
)
from server.services import smhi_climo


ARCHIVE_CSV = """﻿Stationsnamn;Stationsnummer;Stationsnät;Mäthöjd (meter över marken)
Stockholm-Observatoriekullen A;98230;SMHIs stationsnät;2.0

Parameternamn;Beskrivning;Enhet
Lufttemperatur;medelvärde 1 dygn, 1 gång/dygn, kl 00;celsius

Från Datum Tid (UTC);Till Datum Tid (UTC);Representativt dygn;Lufttemperatur;Kvalitet;;Tidsutsnitt:
2026-03-29 00:00:01;2026-03-30 00:00:00;2026-03-29;5.3;Y;;Kvalitetskontrollerade
2026-03-30 00:00:01;2026-03-31 00:00:00;2026-03-30;4.5;G;;
2026-03-31 00:00:01;2026-04-01 00:00:00;2026-03-31;bad;G
2026-04-01 00:00:01;2026-04-02 00:00:00;2026-04-01;6.2;R
"""


def test_parse_archive_csv_skips_metadata_notes_and_bad_rows() -> None:
    rows = parse_archive_csv(ARCHIVE_CSV, "temp_mean")
    # 4 filas de datos: una con valor no numérico y una con calidad R fuera.
    assert [row["date"] for row in rows] == ["2026-03-29", "2026-03-30"]
    assert rows[0]["temp_mean"] == pytest.approx(5.3)
    assert rows[1]["temp_mean"] == pytest.approx(4.5)


def test_parse_recent_json_and_monthly_ref() -> None:
    payload = {"value": [
        {"from": 1, "to": 2, "ref": "2026-05", "value": "12.6", "quality": "Y"},
        {"from": 1, "to": 2, "ref": "2026-06", "value": "18.0", "quality": "Y"},
    ]}
    rows = parse_recent_json(payload, "temp_mean")
    assert [row["date"] for row in rows] == ["2026-05-01", "2026-06-01"]
    assert rows[1]["temp_mean"] == pytest.approx(18.0)


def test_merge_recent_wins_over_archive() -> None:
    archive = [{"date": "2026-03-30", "epoch": 1, "temp_mean": 4.5}]
    recent = [{"date": "2026-03-30", "epoch": 1, "temp_mean": 4.9}]
    merged = merge_field_rows([archive, recent])
    assert len(merged) == 1
    assert merged[0]["temp_mean"] == pytest.approx(4.9)


def test_climo_service_does_not_import_streamlit() -> None:
    source = Path("server/services/smhi_climo.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_fetch_climo_daily_dataset_filters_periods() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        match = re.search(r"/parameter/(\d+)/", url)
        parameter = match.group(1) if match else ""
        if url.endswith("data.csv"):
            if parameter == "2":
                return httpx.Response(200, text=ARCHIVE_CSV)
            return httpx.Response(404, text="")
        return httpx.Response(404, json={})

    async def _test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await smhi_climo.fetch_climo_dataset(
                client, "98230", summary_mode="monthly",
                periods=[(date(2026, 3, 30), date(2026, 3, 31))], selected_years=[],
            )

    df = asyncio.run(_test())
    # Solo el 30 de marzo cae dentro del periodo pedido.
    assert len(df) == 1
    assert df.iloc[0]["temp_mean"] == pytest.approx(4.5)


def test_fetch_climo_dataset_empty_station_is_empty() -> None:
    async def _test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404, text="")), timeout=5.0,
        ) as client:
            return await smhi_climo.fetch_climo_dataset(
                client, "98230", summary_mode="annual",
                periods=[], selected_years=[1990, 2000],
            )

    df = asyncio.run(_test())
    assert df.empty

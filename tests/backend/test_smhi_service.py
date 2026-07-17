"""
Tests del servicio puro ``server.services.smhi``.

SMHI publica un recurso POR PARÁMETRO; los tests cubren la alineación de
series por timestamp, las conversiones m/s → km/h, el filtro de calidad
(G/Y) y la rama MANUAL (red convencional con dato diario).
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import smhi


# Estaciones reales del catálogo local (data/data_estaciones_smhi.json):
# 98230 Stockholm-Observatoriekullen A (automática), 52520 Landskrona D
# (manual, solo precipitación diaria).
STATION = "98230"
MANUAL_STATION = "52520"
TZ = ZoneInfo("Europe/Stockholm")
NOW_LOCAL = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)


def _ms(hour: int) -> int:
    return int(NOW_LOCAL.replace(hour=hour, minute=0).timestamp()) * 1000


def _values(pairs):
    return [
        {"date": epoch_ms, "value": str(value), "quality": quality}
        for epoch_ms, value, quality in pairs
    ]


def _hourly_payloads():
    return {
        smhi.P_TEMP: _values([(_ms(10), 21.0, "G"), (_ms(11), 23.4, "Y")]),
        smhi.P_RH: _values([(_ms(10), 60.0, "G"), (_ms(11), 52.0, "G")]),
        smhi.P_MSL: _values([(_ms(10), 1013.2, "G")]),
        smhi.P_WIND: _values([(_ms(11), 3.0, "G")]),
        smhi.P_DIR: _values([(_ms(11), 220.0, "G")]),
        smhi.P_GUST: _values([(_ms(11), 8.5, "G")]),
        # La segunda hora llega con calidad no aceptada → se descarta.
        smhi.P_RAIN: _values([(_ms(10), 0.4, "G"), (_ms(11), 9.9, "R")]),
        smhi.P_SOLAR: _values([(_ms(11), 610.0, "G")]),
    }


def _client(payloads, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        match = re.search(r"/parameter/(\d+)/", str(request.url))
        parameter = match.group(1) if match else ""
        if status != 200:
            return httpx.Response(status, json={})
        if parameter not in payloads:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"value": payloads[parameter]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_smhi_service_does_not_import_streamlit() -> None:
    source = Path("server/services/smhi.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_fetch_current_canonical() -> None:
    async def _test():
        async with _client(_hourly_payloads()) as client:
            return await smhi.fetch_current(STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(23.4)  # calidad Y se acepta
    assert current["RH"] == pytest.approx(52.0)
    # MSL solo a las 10 → fallback a la última hora que lo trae.
    assert current["p_hpa"] == pytest.approx(1013.2)
    assert current["wind"] == pytest.approx(10.8)   # 3 m/s → km/h
    assert current["gust"] == pytest.approx(30.6)   # 8.5 m/s → km/h
    assert current["wind_dir_deg"] == pytest.approx(220.0)
    assert current["solar_radiation"] == pytest.approx(610.0)
    # Lluvia del día: 0.4 (la de calidad R se descarta).
    assert current["precip_total"] == pytest.approx(0.4)
    assert current["station_name"] == "Stockholm-Observatoriekullen A"
    assert current["elevation"] == pytest.approx(43.133)


def test_fetch_today_series_canonical() -> None:
    async def _test():
        async with _client(_hourly_payloads()) as client:
            return await smhi.fetch_today_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    assert len(series["epochs"]) == 2
    assert series["temps"] == pytest.approx([21.0, 23.4])
    assert series["winds"][1] == pytest.approx(10.8)
    assert math.isnan(series["pressures"][1])  # sin MSL a las 11
    assert all(math.isnan(v) for v in series["dewpts"])  # Td lo deriva la app


def test_fetch_current_missing_parameters_tolerated() -> None:
    # Estación que solo publica temperatura: el resto responde 404.
    payloads = {smhi.P_TEMP: _values([(_ms(11), 15.0, "G")])}

    async def _test():
        async with _client(payloads) as client:
            return await smhi.fetch_current(STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(15.0)
    assert math.isnan(current["gust"])


def test_manual_station_current_serves_latest_day() -> None:
    day_ms = int(NOW_LOCAL.replace(hour=6, minute=0).timestamp()) * 1000
    payloads = {
        smhi.PD_TMEAN: _values([(day_ms, 18.2, "G")]),
        smhi.PD_RAIN: _values([(day_ms, 3.1, "G")]),
    }

    async def _test():
        async with _client(payloads) as client:
            return await smhi.fetch_current(MANUAL_STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(18.2)
    assert current["precip_total"] == pytest.approx(3.1)
    assert current["station_name"] == "Landskrona D"


def test_manual_station_has_no_today_series() -> None:
    async def _test():
        async with _client({}) as client:
            return await smhi.fetch_today_series(MANUAL_STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is False


def test_fetch_current_without_data_raises() -> None:
    async def _test():
        async with _client({}) as client:
            return await smhi.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_current_http_error() -> None:
    async def _test():
        async with _client({}, status=503) as client:
            return await smhi.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_http_error"

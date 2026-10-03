"""
Tests del Port de Barcelona: almacén CKAN en JSON con valores de texto y
``"NAN"``, registros de 10 min en UTC que cierran el intervalo, viento en m/s,
presión de estación reducida al nivel del mar y ranking directo que no
publica un día al que le faltan registros.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import portbcn, ranking

TZ = ZoneInfo("Europe/Madrid")
# 3 de octubre, 12:00 CEST = 10:00 UTC; el día local empieza a las 22:00 UTC del 2.
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=TZ)
DAY_START = int(datetime(2026, 10, 3, tzinfo=TZ).timestamp())
ROW = {
    "id": "01", "name": "Port de Barcelona - Dispensari", "lat": 41.3818, "lon": 2.1832,
    "elev": 3.0, "tz": "Europe/Madrid", "municipality": "Barcelona",
    "resource_recent": "recent-id", "resource_years": {"2026": "year-id"},
}


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr(portbcn, "_load_stations", lambda: [ROW])
    portbcn._RECENT_CACHE.clear()
    yield
    portbcn._RECENT_CACHE.clear()


def _record(epoch: int, *, temp="20.0", rh="80.0", pres="1020.0", wind="2.0", direction="90.0",
            gust="5.0", rain="0.0", gust_column="VV10m3Seg_Max") -> dict:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "_id": epoch, "TIMESTAMP": stamp, "RECORD": "1", "VV_S_WVT": wind, "DV_D1_WVT": direction,
        gust_column: gust, "PRE_Avg": pres, "TEM_Avg": temp, "HUM_Avg": rh, "RAD_Avg": "NAN", "PLU_Tot": rain,
    }


def _handler(resources: Dict[str, List[dict]], requests: List[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        query = {key: values[0] for key, values in parse_qs(urlparse(str(request.url)).query).items()}
        requests.append(query)
        records = sorted(resources.get(query["resource_id"], []), key=lambda r: r["TIMESTAMP"], reverse=True)
        return httpx.Response(200, json={"success": True, "result": {"records": records[: int(query["limit"])]}})

    return handler


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_records_drops_nan_and_unifies_the_gust_column():
    rows = portbcn.parse_records([
        _record(1000 * 600, gust="7.5", gust_column="VV_Max", temp="NAN"),
        {"TIMESTAMP": "basura"},
    ])
    (epoch, values), = rows.items()
    assert epoch == 600000
    assert values["gust"] == 7.5
    assert "TEM_Avg" not in values and "RAD_Avg" not in values


def test_current_converts_units_and_reduces_pressure():
    now_epoch = int(NOW.timestamp())
    records = [
        _record(DAY_START, temp="30.0", rain="5.0"),           # 00:00: de ayer
        _record(DAY_START + 600, temp="18.0", gust="10.0", rain="0.4"),
        _record(now_epoch - 600, temp="22.5", wind="3.0", gust="6.0", rain="0.2", pres="1020.0"),
    ]
    requests: List[dict] = []

    async def run():
        async with _client(_handler({"recent-id": records}, requests)) as client:
            current = await portbcn.fetch_current("01", client=client, now=NOW)
            series = await portbcn.fetch_today_series("01", client=client, now=NOW)
        return current, series

    current, series = asyncio.run(run())
    assert current["Tc"] == 22.5
    assert current["wind"] == pytest.approx(10.8)
    assert current["gust"] == pytest.approx(21.6)
    assert current["p_abs_hpa"] == 1020.0
    assert current["p_hpa"] == pytest.approx(1020.0 * math.exp(3.0 / 8000.0))
    assert current["precip_total"] == pytest.approx(0.6)
    assert current["daily_extremes"] == {"temp_max": 22.5, "temp_min": 18.0, "gust_max": pytest.approx(36.0)}
    assert series["epochs"] == [DAY_START + 600, now_epoch - 600]
    assert series["precip_step_mm"] == [0.4, 0.2]
    assert series["gusts"] == pytest.approx([36.0, 21.6])
    # Observación y serie comparten la consulta.
    assert len(requests) == 1
    assert requests[0]["sort"] == "TIMESTAMP desc"


def test_a_stopped_station_has_no_current_observation():
    stale = [_record(int(NOW.timestamp()) - 4 * 3600)]

    async def run():
        async with _client(_handler({"recent-id": stale}, [])) as client:
            await portbcn.fetch_current("01", client=client, now=NOW)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(run())
    assert exc.value.error_code == "provider_no_current_data"


def test_recent_series_joins_the_year_file_and_the_last_days():
    now_epoch = int(NOW.timestamp())
    year = [_record(now_epoch - 2 * 86400, temp="15.0")]
    recent = [_record(now_epoch - 600, temp="22.0")]

    async def run():
        async with _client(_handler({"year-id": year, "recent-id": recent}, [])) as client:
            return await portbcn.fetch_recent_series("01", client=client, now=NOW, days_back=7)

    series = asyncio.run(run())
    assert series["temps"] == [15.0, 22.0]


def test_ranking_publishes_complete_days_and_skips_a_stopped_one():
    now_epoch = int(NOW.timestamp())
    yesterday_start = int(datetime(2026, 10, 2, tzinfo=TZ).timestamp())
    full_yesterday = [
        _record(epoch, temp=str(15.0 + (epoch - yesterday_start) / 86400 * 10), gust="5.0", rain="0.1")
        for epoch in range(yesterday_start + 600, DAY_START + 1, 600)
    ]
    # Hoy, solo la primera hora: la estación se paró.
    stopped_today = [_record(epoch) for epoch in range(DAY_START + 600, DAY_START + 3601, 600)]

    async def run():
        async with _client(_handler({"recent-id": full_yesterday + stopped_today}, [])) as client:
            return await ranking.fetch_portbcn_daily(client=client, now=NOW)

    records = asyncio.run(run())
    assert [record.local_date for record in records] == ["2026-10-02"]
    yesterday = records[0]
    assert yesterday.rain == pytest.approx(14.4)
    assert yesterday.gust == pytest.approx(18.0)
    assert yesterday.tmax == pytest.approx(25.0)
    assert yesterday.country == "ES" and yesterday.station_id == "01"


def test_ranking_today_has_current_values_and_the_rolling_24h():
    now_epoch = int(NOW.timestamp())
    rows = [
        _record(epoch, rain="0.1", wind="2.0", direction="370.0")
        for epoch in range(now_epoch - 30 * 3600, now_epoch + 1, 600)
    ]

    async def run():
        async with _client(_handler({"recent-id": rows}, [])) as client:
            return await ranking.fetch_portbcn_daily(client=client, now=NOW)

    today = next(record for record in asyncio.run(run()) if record.local_date == "2026-10-03")
    assert today.rain == pytest.approx(72 * 0.1)
    assert today.rain_24h == pytest.approx(144 * 0.1)
    assert today.rain_24h_at == now_epoch
    assert today.tcur == 20.0 and today.tcur_at == now_epoch
    assert today.wind == pytest.approx(7.2) and today.wind_dir == pytest.approx(10.0)

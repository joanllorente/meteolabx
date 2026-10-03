"""
Tests de la ACA (pluviómetros de Catalunya): intensidad de 5 min convertida en
lluvia, día local con la marca que cierra el intervalo, tramos por debajo de
los topes de la API, reintento tras un corte del servidor y ranking directo
con el acumulado del día, la ventana móvil de 24 h y el cierre de ayer.
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
from server.services import aca, ranking

TZ = ZoneInfo("Europe/Madrid")
# 3 de octubre, 12:00 CEST = 10:00 UTC; el día local empieza a las 22:00 UTC del 2.
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=TZ)
DAY_START = int(datetime(2026, 10, 3, tzinfo=TZ).timestamp())
ROW = {
    "id": "082172-002", "sensor": "082172-002-ANA014", "name": "Sant Joan Despí (Llobregat)",
    "lat": 41.37, "lon": 2.06, "elev": 17.0, "tz": "Europe/Madrid", "municipality": "Sant Joan Despí",
}


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr(aca, "_load_stations", lambda: [ROW])
    monkeypatch.setattr(aca, "RETRY_DELAYS_S", (0.0, 0.0))
    aca._TODAY_CACHE.clear()
    yield
    aca._TODAY_CACHE.clear()


def _stamp_of(text: str) -> int:
    return int(datetime.strptime(text, "%d/%m/%YT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def _obs(epoch: int, value: float) -> dict:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d/%m/%YT%H:%M:%S")
    return {"value": str(value), "timestamp": stamp, "time": epoch * 1000}


def _handler(series: Dict[str, Dict[int, float]], requests: List[dict], *, cap: int):
    """Imita Sentilo: devuelve los ``cap`` datos MÁS RECIENTES del intervalo."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        start, end = _stamp_of(query["from"]), _stamp_of(query["to"])
        requests.append({"path": url.path, "from": start, "to": end})

        def _window(points: Dict[int, float]) -> list:
            inside = sorted((epoch for epoch in points if start <= epoch <= end), reverse=True)[:cap]
            return [_obs(epoch, points[epoch]) for epoch in inside]

        if url.path.endswith("/PLUVIOMETREACA-EST"):
            return httpx.Response(200, json={"sensors": [
                {"sensor": sensor, "observations": _window(points)} for sensor, points in series.items()
            ]})
        sensor = url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"observations": _window(series.get(sensor, {}))})

    return handler


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _every_5_min(since: int, until: int, value: float) -> Dict[int, float]:
    return {epoch: value for epoch in range(since, until + 1, aca.STEP_S)}


def test_step_mm_converts_the_5_minute_intensity():
    # 1,2 mm/h durante 5 minutos son los 0,1 mm de un balancín.
    assert aca.step_mm(1.2) == pytest.approx(0.1)
    assert math.isnan(aca.step_mm(-1.0))
    assert math.isnan(aca.step_mm(float("nan")))


def test_parse_observations_uses_epoch_and_falls_back_to_the_utc_stamp():
    parsed = aca.parse_observations([
        {"value": " 2.4", "time": 1790713200000},
        {"value": "1.2", "timestamp": "29/09/2026T20:15:00"},
        {"value": "", "time": 1790712600000},
        "basura",
    ])
    assert parsed == {1790713200: 2.4, 1790712900: 1.2}


def test_windows_are_contiguous_and_below_the_api_cap():
    windows = aca._windows(0, 10 * 3600, aca.NETWORK_WINDOW_S)
    assert windows[0] == (0, 4 * 3600 - 1)
    assert all(b[0] == a[1] + 1 for a, b in zip(windows, windows[1:]))
    assert windows[-1][1] == 10 * 3600
    # 4 h a 5 min son 48 datos: por debajo de los 50 por sensor de la red.
    assert (aca.NETWORK_WINDOW_S // aca.STEP_S) < 50
    assert (aca.SENSOR_WINDOW_S // aca.STEP_S) < 200


def test_current_and_today_series_split_the_day_at_local_midnight():
    points = {
        DAY_START - 600: 60.0,       # 23:50 de ayer: no cuenta
        DAY_START: 12.0,             # 00:00: lluvia de 23:55-00:00, de ayer
        DAY_START + 300: 6.0,        # 0,5 mm
        DAY_START + 600: 0.0,
        int(NOW.timestamp()) - 300: 2.4,  # 0,2 mm, última lectura
    }
    requests: List[dict] = []

    async def run():
        async with _client(_handler({ROW["sensor"]: points}, requests, cap=200)) as client:
            current = await aca.fetch_current(ROW["id"], client=client, now=NOW)
            series = await aca.fetch_today_series(ROW["id"], client=client, now=NOW)
        return current, series

    current, series = asyncio.run(run())
    assert current["precip_total"] == pytest.approx(0.7)
    assert current["precip_rate"] == pytest.approx(2.4)
    assert current["epoch"] == int(NOW.timestamp()) - 300
    assert math.isnan(current["Tc"]) and math.isnan(current["RH"])
    assert current["station_name"] == ROW["name"]
    assert series["epochs"] == [DAY_START + 300, DAY_START + 600, int(NOW.timestamp()) - 300]
    assert series["precip_step_mm"] == pytest.approx([0.5, 0.0, 0.2])
    assert series["has_data"] is True
    # Observación y serie comparten la consulta: una sola petición (12 h + 1 h
    # de ayer caben en dos tramos de 12 h).
    assert len(requests) == 2


def test_current_without_recent_readings_is_an_error():
    stale = {int(NOW.timestamp()) - 4 * 3600: 0.0}

    async def run():
        async with _client(_handler({ROW["sensor"]: stale}, [], cap=200)) as client:
            await aca.fetch_current(ROW["id"], client=client, now=NOW)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(run())
    assert exc.value.error_code == "provider_no_current_data"


def test_unknown_station_is_a_404():
    with pytest.raises(ProviderError) as exc:
        asyncio.run(aca.fetch_today_series("000000-000"))
    assert exc.value.status_code == 404


def test_server_disconnects_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"observations": [_obs(int(NOW.timestamp()) - 300, 1.2)]})

    async def run():
        async with _client(handler) as client:
            return await aca.fetch_current(ROW["id"], client=client, now=NOW)

    assert asyncio.run(run())["precip_rate"] == pytest.approx(1.2)
    assert calls["n"] >= 2


def test_daily_totals_refuse_a_day_with_too_many_gaps():
    full = _every_5_min(DAY_START + 300, DAY_START + 6 * 3600, 1.2)
    total = aca.daily_totals(full, day_start=DAY_START, day_end=DAY_START + 6 * 3600, min_coverage=0.8)
    assert total is not None
    assert total[0] == pytest.approx(72 * 0.1)
    assert total[1] == 72
    half = {epoch: value for epoch, value in full.items() if epoch <= DAY_START + 3 * 3600}
    assert aca.daily_totals(half, day_start=DAY_START, day_end=DAY_START + 6 * 3600, min_coverage=0.8) is None


def test_ranking_publishes_today_the_rolling_24h_and_closes_yesterday():
    # 01:00 CEST: aún de madrugada, el ciclo cierra también el día de ayer.
    now = datetime(2026, 10, 3, 1, 0, tzinfo=TZ)
    now_epoch = int(now.timestamp())
    yesterday_start = int(datetime(2026, 10, 2, tzinfo=TZ).timestamp())
    points = _every_5_min(yesterday_start + 300, now_epoch, 0.0)
    for epoch in range(yesterday_start + 12 * 3600, yesterday_start + 13 * 3600, aca.STEP_S):
        points[epoch] = 12.0                       # 12 pasos de 1 mm a mediodía de ayer
    for epoch in range(DAY_START + 300, now_epoch + 1, aca.STEP_S):
        points[epoch] = 6.0                        # 0,5 mm por paso desde medianoche
    requests: List[dict] = []

    async def run():
        async with _client(_handler({ROW["sensor"]: points, "OTRO": {now_epoch: 1.0}}, requests, cap=50)) as client:
            return await ranking.fetch_aca_daily(client=client, now=now)

    records = {record.local_date: record for record in asyncio.run(run())}
    assert set(records) == {"2026-10-02", "2026-10-03"}
    today, yesterday = records["2026-10-03"], records["2026-10-02"]
    assert today.rain == pytest.approx(6.0)        # 12 pasos de 0,5 mm
    assert today.rain_24h == pytest.approx(18.0)   # + los 12 mm de ayer a mediodía
    assert today.rain_24h_at == now_epoch
    assert today.local_time == "01:00"
    assert today.station_id == ROW["id"] and today.country == "ES"
    assert yesterday.rain == pytest.approx(12.0)
    # Todas las consultas de red caben en el tope de 50 datos por sensor.
    assert all(r["to"] - r["from"] < 50 * aca.STEP_S for r in requests)
    assert all(r["path"].endswith("/PLUVIOMETREACA-EST") for r in requests)


def test_ranking_during_the_day_skips_yesterday():
    now_epoch = int(NOW.timestamp())
    points = _every_5_min(now_epoch - 24 * 3600, now_epoch, 1.2)

    async def run():
        async with _client(_handler({ROW["sensor"]: points}, [], cap=50)) as client:
            return await ranking.fetch_aca_daily(client=client, now=NOW)

    records = asyncio.run(run())
    assert [record.local_date for record in records] == ["2026-10-03"]
    assert records[0].rain == pytest.approx(12 * 12 * 0.1)
    assert records[0].rain_24h == pytest.approx(288 * 0.1)

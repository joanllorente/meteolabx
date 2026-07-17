"""
Tests del servicio puro ``server.services.eccc``.

SWOB entrega observaciones minutales con propiedades planas; los tests
cubren el muestreo a 10 min, la presión MSL derivada de la de estación,
los fallbacks de viento y la rama CLIMATE (dato diario con decalaje).
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import eccc


# Estación real del catálogo local: 616I001 Bancroft Auto (ON, partner),
# altitud ~330 m; y una CLIMATE cualquiera para la rama manual.
STATION = "616I001"
TZ = ZoneInfo("America/Toronto")
NOW_LOCAL = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)


def _iso(hour: int, minute: int) -> str:
    return (
        NOW_LOCAL.replace(hour=hour, minute=minute)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )


def _feature(when: str, **props):
    return {"type": "Feature", "properties": {"obs_date_tm": when, **props}}


def _swob_payload():
    return {
        "features": [
            _feature(_iso(10, 0), air_temp=21.0, rel_hum=60.0, stn_pres=973.0,
                     avg_wnd_spd_10m_pst10mts=10.0, avg_wnd_dir_10m_pst10mts=180.0,
                     max_wnd_spd_10m_pst1hr=25.0, pcpn_amt_pst1hr=0.4),
            _feature(_iso(10, 5), air_temp=21.2, rel_hum=59.0, stn_pres=None,
                     avg_wnd_spd_10m_pst10mts=None, avg_wnd_spd_10m_pst2mts=12.0,
                     avg_wnd_dir_10m_pst2mts=190.0),
            _feature(_iso(11, 0), air_temp=24.9, rel_hum=53.0, stn_pres=972.5,
                     avg_wnd_spd_10m_pst10mts=14.4, avg_wnd_dir_10m_pst10mts=200.0,
                     max_wnd_spd_10m_pst1hr=30.3, pcpn_amt_pst1hr=0.2),
        ],
    }


def _client(payload=None, status: int = 200) -> httpx.AsyncClient:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(status, json=payload if payload is not None else _swob_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_eccc_service_does_not_import_streamlit() -> None:
    source = Path("server/services/eccc.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_fetch_current_canonical() -> None:
    async def _test():
        async with _client() as client:
            return await eccc.fetch_current(STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(24.9)
    assert current["RH"] == pytest.approx(53.0)
    # Presión de estación nativa; la MSL derivada queda por encima.
    assert current["p_abs_hpa"] == pytest.approx(972.5)
    assert current["p_hpa"] > current["p_abs_hpa"]
    # SWOB ya reporta viento en km/h (sin conversión).
    assert current["wind"] == pytest.approx(14.4)
    assert current["gust"] == pytest.approx(30.3)
    # Lluvia del día: pcpn_amt_pst1hr solo en cortes de hora (0.4 + 0.2).
    assert current["precip_total"] == pytest.approx(0.6)
    assert current["station_name"] == "BANCROFT AUTO"


def test_fetch_today_series_samples_ten_minutes() -> None:
    async def _test():
        async with _client() as client:
            return await eccc.fetch_today_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    # 10:00 y 10:05 caen en el mismo bloque de 10 min → gana la última.
    assert len(series["epochs"]) == 2
    assert series["temps"] == pytest.approx([21.2, 24.9])
    # Fallback de viento: a las 10:05 no hay pst10mts → usa pst2mts.
    assert series["winds"][0] == pytest.approx(12.0)
    assert all(math.isnan(v) for v in series["dewpts"])  # Td lo deriva la app


def test_fetch_current_without_data_raises() -> None:
    async def _test():
        async with _client(payload={"features": []}) as client:
            return await eccc.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_bad_response"


def _climate_payload(days_ago: int):
    day = (NOW_LOCAL - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "LOCAL_DATE": f"{day} 00:00:00",
                    "MEAN_TEMPERATURE": 18.5,
                    "MAX_TEMPERATURE": 26.0,
                    "MIN_TEMPERATURE": 11.0,
                    "TOTAL_PRECIPITATION": 2.4,
                    "SPEED_MAX_GUST": 41.0,
                },
            }
        ],
    }


def _climate_station_id():
    import json

    rows = json.load(open("data/data_estaciones_eccc.json"))["stations"]
    return next(r["id"] for r in rows if r["network"] == "CLIMATE")


def test_climate_station_current_serves_latest_day() -> None:
    sid = _climate_station_id()

    async def _test():
        async with _client(payload=_climate_payload(1)) as client:
            return await eccc.fetch_current(sid, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(18.5)
    assert current["precip_total"] == pytest.approx(2.4)
    assert current["gust"] == pytest.approx(41.0)  # SPEED_MAX_GUST en km/h


def test_climate_station_stale_days_raise() -> None:
    sid = _climate_station_id()

    async def _test():
        async with _client(payload=_climate_payload(20)) as client:
            return await eccc.fetch_current(sid, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert "sin días publicados" in str(excinfo.value)


def test_climate_station_has_no_today_series() -> None:
    sid = _climate_station_id()

    async def _test():
        async with _client(payload={"features": []}) as client:
            return await eccc.fetch_today_series(sid, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is False

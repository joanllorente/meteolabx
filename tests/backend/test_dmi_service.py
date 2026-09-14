"""
Tests de DMI (Dinamarca, Groenlandia y Feroe): parámetros en consultas
paralelas, reintento del 429, observación y series con el día local de cada
estación, ranking acumulable con país por estación e histórico de climateData.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import dmi, dmi_climo

CODE = "06180"
TZ = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)
ROW = {
    "id": CODE, "name": "Københavns Lufthavn", "lat": 55.614, "lon": 12.645, "elev": 5.0,
    "tz": "Europe/Copenhagen", "country_code": "DK", "realtime": True,
    "parameters": ["temp_dry", "humidity", "pressure_at_sea", "wind_speed", "wind_max",
                   "wind_dir", "precip_past10min", "temp_max_past1h", "temp_min_past1h",
                   "wind_gust_always_past1h", "precip_past1h"],
}


def _stamp(hour: int, minute: int = 0, *, day: int = 16, tz: ZoneInfo = TZ) -> str:
    return datetime(2026, 7, day, hour, minute, tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feature(parameter: str, value: float, observed: str, station: str = CODE) -> dict:
    return {"type": "Feature", "properties": {
        "parameterId": parameter, "value": value, "observed": observed, "stationId": station,
    }}


# Parámetro → filas (hora local, minuto, valor, día).
SERIES = {
    "temp_dry": [(0, 0, 14.0, 16), (11, 50, 21.0, 16), (12, 20, 22.5, 16)],
    "humidity": [(12, 20, 60.0, 16)],
    "pressure_at_sea": [(12, 20, 1016.1, 16)],
    "wind_speed": [(12, 20, 3.0, 16)],
    "wind_max": [(12, 20, 8.0, 16)],
    "wind_dir": [(12, 20, 230.0, 16)],
    # El tramo de las 00:00 es de los últimos 10 min de ayer.
    "precip_past10min": [(0, 0, 3.0, 16), (11, 50, 0.4, 16), (12, 20, 0.1, 16)],
    "temp_max_past1h": [(0, 0, 25.0, 16), (12, 0, 23.1, 16)],
    "temp_min_past1h": [(0, 0, 12.0, 16), (12, 0, 20.0, 16)],
    "wind_gust_always_past1h": [(12, 0, 11.0, 16)],
}


def _handler(requested: list, *, failing: int = 0):
    state = {"429": failing}

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        parameter = query["parameterId"][0]
        requested.append(parameter)
        if state["429"] > 0:
            state["429"] -= 1
            return httpx.Response(429, json={})
        rows = SERIES.get(parameter, [])
        return httpx.Response(200, json={"features": [
            _feature(parameter, value, _stamp(hour, minute, day=day)) for hour, minute, value, day in rows
        ]})

    return handler


@pytest.fixture(autouse=True)
def _aislado(monkeypatch):
    dmi._TODAY_CACHE.clear()
    monkeypatch.setattr(dmi, "_station_row", lambda _sid: ROW)
    monkeypatch.setattr(dmi, "RETRY_DELAYS_S", (0.0, 0.0))


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_dmi_service_does_not_import_streamlit() -> None:
    for name in ("dmi.py", "dmi_climo.py"):
        assert "import streamlit" not in Path(f"server/services/{name}").read_text(encoding="utf-8")


def test_fetch_current_merges_parameters_and_local_day_extremes() -> None:
    requested: list = []

    async def _test():
        async with _client(_handler(requested)) as client:
            obs = await dmi.fetch_current(CODE, client=client, now=NOW)
            series = await dmi.fetch_today_series(CODE, client=client, now=NOW)
            return obs, series

    obs, series = _run(_test())
    # Solo lo que la estación publica, y la serie reutiliza las consultas.
    assert sorted(requested) == sorted(set(ROW["parameters"]) & set(dmi.TODAY_PARAMETERS))
    assert obs["Tc"] == pytest.approx(22.5)
    assert obs["RH"] == pytest.approx(60.0)
    assert obs["p_hpa"] == pytest.approx(1016.1)
    assert obs["wind"] == pytest.approx(10.8)
    assert obs["gust"] == pytest.approx(28.8)
    assert obs["wind_dir_deg"] == pytest.approx(230.0)
    assert obs["precip_total"] == pytest.approx(0.5)
    # La máxima horaria de las 00:00 (25,0) es de ayer y no cuenta.
    assert obs["daily_extremes"]["temp_max"] == pytest.approx(23.1)
    assert obs["daily_extremes"]["temp_min"] == pytest.approx(14.0)
    assert obs["daily_extremes"]["gust_max"] == pytest.approx(39.6)
    assert not math.isnan(obs["Td"])

    assert series["temps"] == [14.0, 21.0, 22.5]
    assert math.isnan(series["precip_step_mm"][0])
    assert series["precip_step_mm"][1:] == [0.4, 0.1]


def test_rate_limit_is_retried_and_then_reported() -> None:
    requested: list = []

    async def _ok():
        async with _client(_handler(requested, failing=2)) as client:
            return await dmi.fetch_parameters(client, parameters=["temp_dry"], since_epoch=0, station_id=CODE)

    data = _run(_ok())
    assert requested == ["temp_dry"] * 3
    assert len(data[CODE]["temp_dry"]) == 3

    async def _ko():
        async with _client(_handler([], failing=5)) as client:
            return await dmi.fetch_parameters(client, parameters=["temp_dry"], since_epoch=0, station_id=CODE)

    with pytest.raises(ProviderError) as excinfo:
        _run(_ko())
    assert excinfo.value.status_code == 429


def test_manual_station_has_no_current_data_without_calling_dmi(monkeypatch) -> None:
    monkeypatch.setattr(dmi, "_station_row", lambda _sid: {"id": "34320", "realtime": False})

    async def _test():
        async with _client(lambda r: (_ for _ in ()).throw(AssertionError(str(r.url)))) as client:
            return await dmi.fetch_current("34320", client=client, now=NOW)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"


def test_recent_series_keeps_readings_on_the_hour() -> None:
    async def _test():
        async with _client(_handler([])) as client:
            return await dmi.fetch_recent_series(CODE, days_back=2, client=client, now=NOW)

    recent = _run(_test())
    # 00:00 local es en punto; 11:50 y 12:20 no.
    assert recent["temps"] == [14.0]


def test_fetch_dmi_records_uses_station_timezone_and_country(monkeypatch) -> None:
    from server.services import ranking

    nuuk = ZoneInfo("America/Nuuk")
    monkeypatch.setattr(ranking, "_dmi_catalog", lambda: {
        CODE: {**ROW},
        "04250": {"id": "04250", "name": "Nuuk", "lat": 64.17, "lon": -51.7,
                  "tz": "America/Nuuk", "country_code": "GL"},
    })
    rows = {
        "temp_max_past1h": [(CODE, TZ, 12, 23.1), ("04250", nuuk, 7, 6.2)],
        "temp_min_past1h": [(CODE, TZ, 12, 20.0), ("04250", nuuk, 7, 4.0)],
        "wind_gust_always_past1h": [(CODE, TZ, 12, 11.0)],
        "precip_past1h": [(CODE, TZ, 12, 0.6)],
        "temp_dry": [(CODE, TZ, 12, 22.5), ("04250", nuuk, 7, 5.9)],
        "wind_speed": [(CODE, TZ, 12, 3.0)],
        "wind_dir": [(CODE, TZ, 12, 230.0)],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parameter = parse_qs(urlparse(str(request.url)).query)["parameterId"][0]
        return httpx.Response(200, json={"features": [
            _feature(parameter, value, _stamp(hour, tz=tz), station) for station, tz, hour, value in rows.get(parameter, [])
        ]})

    async def _test():
        async with _client(handler) as client:
            return await ranking.fetch_dmi_records(ranking.RankingStore(), client=client, now=NOW)

    recs = {rec.station_id: rec for rec in _run(_test())}
    copenhagen = recs[CODE]
    assert copenhagen.country == "DK"
    assert copenhagen.tmax == pytest.approx(23.1)
    assert copenhagen.gust == pytest.approx(39.6)
    assert copenhagen.rain == pytest.approx(0.6)
    assert copenhagen.tcur == pytest.approx(22.5)
    assert copenhagen.wind == pytest.approx(10.8)
    assert recs["04250"].country == "GL"
    assert recs["04250"].tmin == pytest.approx(4.0)


def test_climo_maps_daily_station_values() -> None:
    from server.services.climo_cache import clear_climo_block_cache

    values = {
        "mean_temp": 14.2, "max_temp_w_date": 18.0, "min_temp": 8.9, "acc_precip": 1.5,
        "mean_wind_speed": 3.2, "max_wind_speed_3sec": 8.8, "mean_wind_dir": 200.0,
    }
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        parameter = query["parameterId"][0]
        requested.append((parameter, query["datetime"][0][:4]))
        return httpx.Response(200, json={"features": [
            {"properties": {"parameterId": parameter, "value": values[parameter], "validity": True,
                            "from": "2025-09-11T00:00:00.001000+02:00", "to": "2025-09-12T00:00:00+02:00"}},
            # Un día de otro año que entra por la holgura de la consulta.
            {"properties": {"parameterId": parameter, "value": 99.0, "validity": True,
                            "from": "2024-12-31T00:00:00.001000+01:00"}},
        ]})

    async def _test():
        clear_climo_block_cache()
        async with _client(handler) as client:
            return await dmi_climo.fetch_climo_dataset(
                client, CODE, summary_mode="monthly",
                periods=[(date(2025, 9, 1), date(2025, 9, 30)), (date(2005, 1, 1), date(2005, 1, 31))],
                selected_years=[2025],
            )

    frame = _run(_test())
    # 2005 es anterior a climateData (2011): ni se pide.
    assert {year for _parameter, year in requested} == {"2024"}  # 2025 con un día de holgura
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["temp_max"] == pytest.approx(18.0)
    assert row["precip_total"] == pytest.approx(1.5)
    assert row["wind_mean"] == pytest.approx(3.2 * 3.6)
    assert row["gust_max"] == pytest.approx(8.8 * 3.6)
    assert row["wind_dir_mean"] == pytest.approx(200.0)


def test_inventory_timezones_for_greenland() -> None:
    from scripts.build_dmi_inventory import station_timezone

    assert station_timezone("DK", 55.6, 12.6) == "Europe/Copenhagen"
    assert station_timezone("FO", 62.0, -6.8) == "Atlantic/Faroe"
    assert station_timezone("GL", 64.17, -51.7) == "America/Nuuk"
    assert station_timezone("GL", 76.53, -68.75) == "America/Thule"
    assert station_timezone("GL", 76.77, -18.67) == "America/Danmarkshavn"
    assert station_timezone("GL", 70.48, -21.97) == "America/Scoresbysund"

"""
Tests de LHMT (Lituania, API Meteo.lt): servicio de observación, adaptador del
ranking y histórico derivado del archivo horario de data.gov.lt.

La API sirve un día por petición con todas las variables juntas; los tests
cubren la conversión m/s → km/h, el día local (Europe/Vilnius), que la lluvia
de cada muestra cuente para la hora precedente y que los ids en mayúsculas del
schema lleguen en minúsculas a la API.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import lhmt, lhmt_climo

STATION = "vilniaus-ams"
TZ = ZoneInfo("Europe/Vilnius")
# 12:30 locales = 09:30 UTC; el día local empezó a las 21:00 UTC de ayer.
NOW_LOCAL = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)


def _utc_text(local_hour: int, *, day: int = 16) -> str:
    local = datetime(2026, 7, day, local_hour, 0, tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _obs(local_hour: int, *, day: int = 16, **values):
    base = {
        "observationTimeUtc": _utc_text(local_hour, day=day),
        "airTemperature": None, "feelsLikeTemperature": None, "windSpeed": None,
        "windGust": None, "windDirection": None, "cloudCover": None,
        "seaLevelPressure": None, "relativeHumidity": None, "precipitation": None,
        "snowDepth": None, "conditionCode": None,
    }
    base.update(values)
    return base


def _latest_payload():
    return {
        "station": {"code": STATION, "name": "Vilniaus AMS"},
        "observations": [
            # Ayer por la tarde: fuera del día local.
            _obs(23, day=15, airTemperature=30.0, precipitation=5.0, windGust=20.0),
            # Medianoche: su temperatura es de hoy, su lluvia de la última hora de ayer.
            _obs(0, airTemperature=18.0, precipitation=2.0),
            _obs(11, airTemperature=24.0, relativeHumidity=60, seaLevelPressure=1015.2,
                 windSpeed=3.0, windGust=8.0, windDirection=220, precipitation=0.4),
            _obs(12, airTemperature=25.5, relativeHumidity=55, windSpeed=None,
                 windGust=6.0, precipitation=0.1),
        ],
    }


@pytest.fixture(autouse=True)
def _sin_pausas(monkeypatch):
    monkeypatch.setattr(lhmt_climo, "API_REQUEST_PAUSE_S", 0.0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_lhmt_service_does_not_import_streamlit() -> None:
    source = Path("server/services/lhmt.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source


def test_fetch_current_takes_last_values_and_local_day_totals() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_latest_payload())

    async def _test():
        async with _client(handler) as client:
            # El schema del backend manda el id en mayúsculas.
            return await lhmt.fetch_current("VILNIAUS-AMS", client=client, now=NOW_LOCAL)

    obs = _run(_test())
    assert seen == ["https://api.meteo.lt/v1/stations/vilniaus-ams/observations/latest"]
    assert obs["Tc"] == pytest.approx(25.5)
    assert obs["RH"] == pytest.approx(55)
    assert obs["p_hpa"] == pytest.approx(1015.2)  # último valor no nulo
    assert obs["wind"] == pytest.approx(3.0 * 3.6)
    assert obs["gust"] == pytest.approx(6.0 * 3.6)
    assert obs["wind_dir_deg"] == pytest.approx(220)
    # 0.4 + 0.1: la lluvia de la muestra de medianoche es de ayer.
    assert obs["precip_total"] == pytest.approx(0.5)
    assert obs["daily_extremes"]["temp_max"] == pytest.approx(25.5)
    assert obs["daily_extremes"]["temp_min"] == pytest.approx(18.0)
    assert obs["daily_extremes"]["gust_max"] == pytest.approx(8.0 * 3.6)
    assert obs["station_name"] == "Vilnius"
    assert obs["epoch"] == int(datetime(2026, 7, 16, 12, tzinfo=TZ).timestamp())
    # El punto de rocío lo deriva el pipeline, no el proveedor.
    assert not math.isnan(obs["Td"])


def test_fetch_current_without_samples_is_no_current_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    async def _test():
        async with _client(handler) as client:
            return await lhmt.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"


def test_fetch_current_rate_limit_is_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    async def _test():
        async with _client(handler) as client:
            return await lhmt.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.status_code == 429


def test_fetch_today_series_keeps_local_day_and_hour_precipitation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_latest_payload())

    async def _test():
        async with _client(handler) as client:
            return await lhmt.fetch_today_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    assert len(series["epochs"]) == 3
    assert series["temps"] == [18.0, 24.0, 25.5]
    assert math.isnan(series["precip_step_mm"][0])
    assert series["precip_step_mm"][1:] == [0.4, 0.1]
    assert math.isnan(series["winds"][2])
    assert series["gusts"][1] == pytest.approx(8.0 * 3.6)
    assert all(math.isnan(v) for v in series["dewpts"])


def test_fetch_recent_series_requests_utc_days_and_caches_closed_ones() -> None:
    lhmt._CLOSED_DAY_CACHE.clear()
    requested = []
    now = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        when = str(request.url).rsplit("/", 1)[-1]
        requested.append(when)
        if when == "2026-07-14":
            return httpx.Response(404, json={})  # día sin datos guardados
        return httpx.Response(200, json={
            "observations": [
                {"observationTimeUtc": f"{when} 08:00:00", "airTemperature": 20.0,
                 "relativeHumidity": 70, "seaLevelPressure": 1010.0},
            ],
        })

    async def _test():
        async with _client(handler) as client:
            first = await lhmt.fetch_recent_series(STATION, days_back=2, client=client, now=now)
            second = await lhmt.fetch_recent_series(STATION, days_back=2, client=client, now=now)
            return first, second

    first, second = _run(_test())
    # El 14 y el 15 están cerrados (también el 404): solo hoy se repite.
    assert sorted(requested) == ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-16"]
    # 14/07 08:00 queda fuera de la ventana de 2 días (empieza el 14 a las 09:30).
    assert first["epochs"] == second["epochs"]
    assert len(first["epochs"]) == 2
    assert first["pressures"] == [1010.0, 1010.0]


def test_fetch_lhmt_records_accumulates_full_day_from_latest(monkeypatch) -> None:
    from server.services import ranking

    monkeypatch.setattr(ranking, "_lhmt_catalog", lambda: {
        STATION: {"id": STATION, "name": "Vilnius", "lat": 54.6, "lon": 25.1, "active_now": True},
        "offline-ams": {"id": "offline-ams", "name": "Off", "active_now": False},
    })
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=_latest_payload())

    async def _test(store):
        async with _client(handler) as client:
            return await ranking.fetch_lhmt_records(store, client=client, now=NOW_LOCAL)

    store = ranking.RankingStore()
    recs = _run(_test(store))
    assert requested == ["https://api.meteo.lt/v1/stations/vilniaus-ams/observations/latest"]
    rec = next(r for r in recs if r.station_id == STATION)
    assert rec.local_date == "2026-07-16"
    assert rec.tmax == pytest.approx(25.5)
    assert rec.tmin == pytest.approx(18.0)
    assert rec.gust == pytest.approx(28.8)  # 8 m/s de la hora de las 11
    assert rec.rain == pytest.approx(0.5)
    assert rec.tcur == pytest.approx(25.5)
    assert rec.wind == pytest.approx(10.8)
    assert rec.wind_dir == pytest.approx(220)

    # Un segundo ciclo con las mismas 24 h no duplica la lluvia.
    recs = _run(_test(store))
    rec = next(r for r in recs if r.station_id == STATION)
    assert rec.rain == pytest.approx(0.5)


def test_fetch_lhmt_records_raises_when_whole_network_fails(monkeypatch) -> None:
    from server.services import ranking

    monkeypatch.setattr(ranking, "_lhmt_catalog", lambda: {
        STATION: {"id": STATION, "active_now": True},
    })

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    async def _test():
        async with _client(handler) as client:
            return await ranking.fetch_lhmt_records(ranking.RankingStore(), client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError):
        _run(_test())


def test_climo_aggregates_local_days_from_hourly_samples() -> None:
    def sample(local_hour, day=16, **values):
        epoch = int(datetime(2026, 7, day, local_hour, tzinfo=TZ).timestamp())
        return {"epoch": epoch, **values}

    samples = [
        sample(0, airTemperature=10.0, precipitation=3.0),  # lluvia de ayer
        sample(6, airTemperature=8.0, windSpeed=2.0, windDirection=90, windGust=5.0),
        sample(15, airTemperature=22.0, windSpeed=4.0, windDirection=100, windGust=12.0,
               precipitation=1.5),
        sample(0, day=17, airTemperature=12.0, precipitation=0.5),  # lluvia del 16
    ]
    frame = lhmt_climo.aggregate_daily(samples, [(date(2026, 7, 16), date(2026, 7, 16))])
    assert list(frame["date"].dt.date) == [date(2026, 7, 16)]
    row = frame.iloc[0]
    assert row["temp_max"] == pytest.approx(22.0)
    assert row["temp_min"] == pytest.approx(8.0)
    assert row["temp_mean"] == pytest.approx((10.0 + 8.0 + 22.0) / 3)
    assert row["precip_total"] == pytest.approx(2.0)
    assert row["wind_mean"] == pytest.approx(3.0 * 3.6)
    assert row["gust_max"] == pytest.approx(12.0 * 3.6)
    assert row["gust_dir_max"] == pytest.approx(100)
    assert row["wind_dir_mean"] == pytest.approx(90.0)


def test_climo_splits_periods_by_year_and_queries_local_window() -> None:
    chunks = lhmt_climo.split_by_year([(date(2023, 11, 1), date(2025, 2, 28))])
    assert chunks == [
        (date(2023, 11, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 2, 28)),
    ]
    query = unquote(lhmt_climo.archive_query("VILNIAUS-AMS", date(2025, 1, 1), date(2025, 1, 31)))
    assert 'stoties_kodas="vilniaus-ams"' in query
    # Enero en Vilnius es UTC+2: el día local 1 empieza el 31/12 a las 22:00 UTC
    # y se pide hasta la 01:00 local del 1/2 por la lluvia de la última hora.
    assert 'stebejimo_laikas>="2024-12-31T22:00:00"' in query
    assert 'stebejimo_laikas<"2025-01-31T23:00:00"' in query


def test_climo_dataset_merges_archive_and_recent_api_days() -> None:
    from server.services.climo_cache import clear_climo_block_cache

    clear_climo_block_cache()
    lhmt._CLOSED_DAY_CACHE.clear()
    today = datetime.now(TZ).date()
    archive_calls = []
    api_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = unquote(str(request.url))
        if "data.gov.lt" in url:
            archive_calls.append(url)
            return httpx.Response(200, json={"_data": [
                {"stebejimo_laikas": "2024-03-10T10:00:00", "oro_temp": 6.5,
                 "vejo_greitis": 3.0, "vejo_gusis": 7.0, "vejo_kryptis": 180,
                 "kritutliu_kiekis": 0.2},
            ]})
        api_calls.append(url)
        return httpx.Response(404, json={})

    async def _test():
        async with _client(handler) as client:
            return await lhmt_climo.fetch_climo_dataset(
                client, "VILNIAUS-AMS", summary_mode="monthly",
                periods=[(date(2024, 3, 1), date(2024, 3, 31))], selected_years=[2024],
            )

    frame = _run(_test())
    assert len(archive_calls) == 1
    # Los días sin archivo se piden a la API, que aquí tampoco los tiene.
    assert api_calls
    assert list(frame["date"].dt.date) == [date(2024, 3, 10)]
    assert frame.iloc[0]["temp_max"] == pytest.approx(6.5)
    assert today > date(2024, 3, 31)


def test_inventory_exposes_lhmt_catalog_rows() -> None:
    rows = lhmt._load_stations()
    assert len(rows) >= 50
    vilnius = lhmt._station_row(STATION)
    assert vilnius["country_code"] == "LT"
    assert vilnius["manual"] is False
    assert vilnius["has_historical"] is True
    assert vilnius["sensors"]["thermometer"] is True


def test_climo_gaps_are_local_days_with_few_hours_mapped_to_utc_days() -> None:
    def sample(day, local_hour):
        return {"epoch": int(datetime(2025, 6, day, local_hour, tzinfo=TZ).timestamp())}

    samples = [sample(10, hour) for hour in range(24)] + [sample(11, hour) for hour in range(5)]
    gaps = lhmt_climo.local_day_gaps(samples, [(date(2025, 6, 10), date(2025, 6, 12))])
    assert gaps == [date(2025, 6, 11), date(2025, 6, 12)]
    # Junio en Vilnius es UTC+3: el día local 11 va del 10 a las 21:00 UTC al
    # 11 a las 22:00 UTC (con la hora extra de la lluvia).
    assert lhmt_climo.utc_days_for_local_days([date(2025, 6, 11)]) == [
        date(2025, 6, 10), date(2025, 6, 11),
    ]


def test_climo_fills_archive_gaps_from_api(monkeypatch) -> None:
    from server.services.climo_cache import clear_climo_block_cache

    clear_climo_block_cache()
    lhmt._CLOSED_DAY_CACHE.clear()
    monkeypatch.setattr(lhmt, "_station_row", lambda _sid: {"data_start_utc": "2016-09-11 00:00:00"})
    today = datetime.now(TZ).date()
    year = today.year - 1
    missing = date(year, 3, 10)
    api_calls = []

    def archive_rows():
        rows = []
        for offset in range(31):
            day = date(year, 3, 1) + timedelta(days=offset)
            if day == missing:
                continue
            for hour in range(24):
                local = datetime(day.year, day.month, day.day, hour, tzinfo=TZ)
                rows.append({
                    "stebejimo_laikas": local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    "oro_temp": 1.0,
                })
        return rows

    def handler(request: httpx.Request) -> httpx.Response:
        url = unquote(str(request.url))
        if "data.gov.lt" in url:
            return httpx.Response(200, json={"_data": archive_rows()})
        when = url.rsplit("/", 1)[-1]
        api_calls.append(when)
        return httpx.Response(200, json={"observations": [
            {"observationTimeUtc": f"{when} 10:00:00", "airTemperature": 9.0},
        ]})

    async def _test():
        async with _client(handler) as client:
            return await lhmt_climo.fetch_climo_dataset(
                client, "vilniaus-ams", summary_mode="monthly",
                periods=[(date(year, 3, 1), date(year, 3, 31))], selected_years=[year],
            )

    frame = _run(_test())
    # Marzo: UTC+2 → el día local 10 son los días UTC 9 y 10.
    assert sorted(api_calls) == [f"{year}-03-09", f"{year}-03-10"]
    assert len(frame) == 31
    row = frame[frame["date"].dt.date == missing].iloc[0]
    assert row["temp_max"] == pytest.approx(9.0)

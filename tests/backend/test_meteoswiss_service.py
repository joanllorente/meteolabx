"""
Tests de MeteoSwiss (Suiza y Liechtenstein): ``now`` más la cola de
``recent`` pedida con Range, día local suizo, pluviómetros manuales sin dato
actual, ranking acumulable con arranque en frío e histórico diario combinado.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import meteoswiss, meteoswiss_climo

TZ = ZoneInfo("Europe/Zurich")
# 16 de julio, 12:30 CEST = 10:30 UTC; el día local empieza a las 22:00 UTC del 15.
NOW = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)
ROW = {
    "id": "ABO", "name": "Adelboden", "lat": 46.49, "lon": 7.56, "elev": 1321.0,
    "tz": "Europe/Zurich", "country_code": "CH", "collection": "ogd-smn", "realtime": True,
}
T_HEADER = "station_abbr;reference_timestamp;tre200s0;ure200s0;pp0qffs0;pp0qnhs0;fu3010z0;fu3010z1;dkl010z0;rre150z0"


def _t_line(stamp: str, temp="", rh="", qff="", qnh="", wind="", gust="", direction="", rain="") -> str:
    return f"ABO;{stamp};{temp};{rh};{qff};{qnh};{wind};{gust};{direction};{rain}"


T_NOW = "\r\n".join([
    T_HEADER,
    _t_line("16.07.2026 00:00", 14.0, 80, "", 1020.0, 5.0, 12.0, 180, 0.2),
    _t_line("16.07.2026 10:20", 21.0, 60, "", 1019.0, 7.0, 20.0, 200, 0.3),
    _t_line("16.07.2026 10:30", 22.5, 55, "", 1018.5, 8.0, 18.0, 210, 0.1),
]) + "\r\n"
# Cola sin cabecera y con la primera línea cortada, como llega con Range.
T_RECENT_TAIL = "\r\n".join([
    "0;1;2;3",
    _t_line("15.07.2026 21:50", 12.0, 85, "", 1021.0, 3.0, 9.0, 170, 9.0),
    _t_line("15.07.2026 22:00", 11.5, 86, "", 1021.0, 3.0, 30.0, 170, 4.0),
    _t_line("15.07.2026 22:10", 11.0, 87, "", 1021.0, 3.0, 10.0, 170, 0.5),
]) + "\r\n"


def _handler(requests: list):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append((url.rsplit("/", 1)[-1], request.headers.get("range")))
        if url.endswith("_t_now.csv"):
            return httpx.Response(200, content=T_NOW.encode("cp1252"))
        if url.endswith("_t_recent.csv"):
            return httpx.Response(206, content=T_RECENT_TAIL.encode("cp1252"))
        return httpx.Response(403, content=b"AccessDenied")

    return handler


@pytest.fixture(autouse=True)
def _aislado(monkeypatch):
    meteoswiss._TODAY_CACHE.clear()
    monkeypatch.setattr(meteoswiss, "_station_row", lambda _sid: ROW)
    monkeypatch.setattr(meteoswiss, "RETRY_DELAYS_S", (0.0, 0.0))


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_meteoswiss_service_does_not_import_streamlit() -> None:
    for name in ("meteoswiss.py", "meteoswiss_climo.py"):
        assert "import streamlit" not in Path(f"server/services/{name}").read_text(encoding="utf-8")


def test_current_joins_now_with_recent_tail_on_the_local_day() -> None:
    requests: list = []

    async def _test():
        async with _client(_handler(requests)) as client:
            obs = await meteoswiss.fetch_current("abo", client=client, now=NOW)
            series = await meteoswiss.fetch_today_series("ABO", client=client, now=NOW)
            return obs, series

    obs, series = _run(_test())
    # ``now`` empieza a las 00 UTC: la cola de ``recent`` se pide con Range, y
    # la serie del día reutiliza las mismas descargas.
    assert [name for name, _range in requests] == ["ogd-smn_abo_t_now.csv", "ogd-smn_abo_t_recent.csv"]
    assert requests[1][1].startswith("bytes=-")
    assert obs["Tc"] == pytest.approx(22.5)
    assert obs["RH"] == pytest.approx(55)
    assert obs["p_hpa"] == pytest.approx(1018.5)  # sin QFF, QNH
    assert obs["wind"] == pytest.approx(8.0)  # ya en km/h
    assert obs["gust"] == pytest.approx(18.0)
    assert obs["time_local"].startswith("2026-07-16T12:30")
    assert not math.isnan(obs["Td"])
    # La racha de las 22:00 UTC (00:00 local) cierra el intervalo de ayer.
    assert obs["daily_extremes"]["gust_max"] == pytest.approx(20.0)
    assert obs["daily_extremes"]["temp_min"] == pytest.approx(11.0)
    assert obs["daily_extremes"]["temp_max"] == pytest.approx(22.5)
    # Lluvia: 0,5 + 0,2 + 0,3 + 0,1; los 4,0 de las 00:00 locales son de ayer.
    assert obs["precip_total"] == pytest.approx(1.1)

    assert series["temps"] == [11.5, 11.0, 14.0, 21.0, 22.5]
    assert math.isnan(series["precip_step_mm"][0])
    assert series["precip_step_mm"][1:] == [0.5, 0.2, 0.3, 0.1]
    assert all(math.isnan(value) for value in series["dewpts"])


def test_manual_gauge_has_no_current_data_without_downloads(monkeypatch) -> None:
    monkeypatch.setattr(meteoswiss, "_station_row", lambda _sid: {"id": "ABG", "realtime": False})

    async def _test():
        async with _client(lambda r: (_ for _ in ()).throw(AssertionError(str(r.url)))) as client:
            return await meteoswiss.fetch_current("ABG", client=client, now=NOW)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"


def test_missing_files_read_as_empty_and_stale_station_has_no_current() -> None:
    async def _test():
        async with _client(lambda r: httpx.Response(403)) as client:
            return await meteoswiss.fetch_current("ABO", client=client, now=NOW)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"


def test_recent_series_reads_hourly_files() -> None:
    header = "station_abbr;reference_timestamp;tre200h0;ure200h0;pp0qffh0;pp0qnhh0"
    h_now = f"{header}\nABO;16.07.2026 00:00;15;70;;1019\nABO;16.07.2026 10:00;21;50;1016;1018\n"
    h_tail = "cortada\nABO;14.07.2026 12:00;18;60;;1020\n"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("_h_now.csv"):
            return httpx.Response(200, content=h_now.encode())
        if url.endswith("_h_recent.csv"):
            return httpx.Response(206, content=h_tail.encode())
        return httpx.Response(403)

    async def _test():
        async with _client(handler) as client:
            return await meteoswiss.fetch_recent_series("ABO", days_back=2, client=client, now=NOW)

    recent = _run(_test())
    assert recent["temps"] == [18.0, 15.0, 21.0]
    assert recent["pressures"] == [1020.0, 1019.0, 1016.0]


def test_ranking_backfills_cold_start_and_uses_local_day(monkeypatch) -> None:
    from server.services import ranking

    monkeypatch.setattr(ranking, "_meteoswiss_catalog", lambda: {
        "ABO": {**ROW},
        "ABE": {"id": "ABE", "name": "Aarberg", "lat": 47.05, "lon": 7.28,
                "collection": "ogd-smn-precip", "country_code": "CH", "realtime": True},
        "VAD": {"id": "VAD", "name": "Vaduz", "lat": 47.12, "lon": 9.51,
                "collection": "ogd-nime", "country_code": "LI", "realtime": False},
    })
    now = datetime(2026, 7, 16, 10, 35, tzinfo=timezone.utc)
    h_header = "station_abbr;reference_timestamp;tre200hx;tre200hn;fu3010h1;rre150h0"
    files = {
        "ogd-smn_abo_h_now.csv": f"{h_header}\nABO;16.07.2026 00:00;13;11;30;2\nABO;16.07.2026 10:00;24;18;40;0.5\n",
        # 22:00 UTC del 15 cierra la última hora del día anterior (local).
        "ogd-smn_abo_h_recent.csv": "x\nABO;15.07.2026 11:00;19;17;22;1\nABO;15.07.2026 22:00;30;9;90;3\n",
        # Bulk de valores actuales: una sola llamada para toda SwissMetNet.
        "VQHA80.csv": (
            "Station/Location;Date;tre200s0;rre150z0;fu3010z0;dkl010z0\n"
            "ABO;202607161030;22.5;0.00;8.0;210\n"
            "BER;202607161030;-;-;-;-\n"
        ),
        "ogd-smn-precip_abe_h_now.csv": "station_abbr;reference_timestamp;rre150h0\nABE;16.07.2026 09:00;1.5\n",
        "ogd-smn-precip_abe_h_recent.csv": "x\nABE;15.07.2026 20:00;0.2\n",
    }
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1]
        requested.append(name)
        if name in files:
            return httpx.Response(206 if request.headers.get("range") else 200, content=files[name].encode())
        return httpx.Response(403)

    async def _test(store):
        async with _client(handler) as client:
            return await ranking.fetch_meteoswiss_records(store, client=client, now=now)

    store = ranking.RankingStore()
    recs = {rec.station_id: rec for rec in _run(_test(store))}
    assert "VAD" not in recs and not any("vad" in name for name in requested)
    assert not any(name.endswith("_t_now.csv") for name in requested)
    assert requested.count("VQHA80.csv") == 1
    abo = recs["ABO"]
    assert abo.country == "CH"
    # Día local del 16: horas desde las 22:00 UTC del 15 (la de las 22:00
    # cierra la de ayer y no cuenta).
    assert abo.tmax == pytest.approx(24.0)
    assert abo.tmin == pytest.approx(11.0)
    assert abo.gust == pytest.approx(40.0)
    assert abo.tcur == pytest.approx(22.5)
    assert abo.wind == pytest.approx(8.0)
    assert abo.rain_24h == pytest.approx(6.5)
    assert recs["ABE"].rain == pytest.approx(1.5)

    # Con las horas de ayer ya en el store, basta con ``h_now``.
    requested.clear()
    _run(_test(store))
    assert "ogd-smn_abo_h_recent.csv" not in requested
    assert sorted(requested) == ["VQHA80.csv", "ogd-smn-precip_abe_h_now.csv", "ogd-smn_abo_h_now.csv"]


def test_current_bulk_skips_missing_values() -> None:
    bulk = meteoswiss.parse_current_bulk(
        "Station/Location;Date;tre200s0;fu3010z0\nTAE;202609131850;17.70;3.20\nOTL;202609131850;-;-\n"
    )
    assert bulk == {"TAE": (1789325400, {"tre200s0": 17.7, "fu3010z0": 3.2})}


def test_climo_combines_historical_and_recent() -> None:
    from server.services.climo_cache import clear_climo_block_cache

    header = "station_abbr;reference_timestamp;tre200d0;tre200dx;tre200dn;fu3010d0;fu3010d1;dkl010d0;rre150d0;rka150d0"
    historical = (
        f"{header}\nABO;31.12.2025 00:00;-1.5;2;-4;5;20;180;9;3\n"
        "ABO;01.01.2026 00:00;99;99;99;;;;;\n"
    )
    recent = f"{header}\nABO;01.01.2026 00:00;0.5;3;-2;;;;4;\nABO;02.01.2026 00:00;;;;;;;;\n"
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1]
        requested.append(name)
        body = {"ogd-smn_abo_d_historical.csv": historical, "ogd-smn_abo_d_recent.csv": recent}.get(name)
        return httpx.Response(200, content=body.encode()) if body else httpx.Response(403)

    async def _test():
        clear_climo_block_cache()
        async with _client(handler) as client:
            return await meteoswiss_climo.fetch_climo_daily_for_periods(
                client, "ABO", [(date(2025, 12, 31), date(2026, 1, 5))], today_date=date(2026, 9, 1),
            )

    frame = _run(_test())
    assert sorted(requested) == ["ogd-smn_abo_d_historical.csv", "ogd-smn_abo_d_recent.csv"]
    assert len(frame) == 2  # el 2 de enero no trae nada
    first, second = frame.iloc[0], frame.iloc[1]
    assert first["temp_mean"] == pytest.approx(-1.5)
    assert first["gust_max"] == pytest.approx(20.0)
    assert first["precip_total"] == pytest.approx(3.0)  # rka150d0 (día UTC) antes que 6-6
    assert second["temp_max"] == pytest.approx(3.0)  # recent pisa al histórico
    assert second["precip_total"] == pytest.approx(4.0)  # sin rka150d0, rre150d0


def test_inventory_reads_last_reading_from_tail(monkeypatch) -> None:
    from scripts import build_meteoswiss_inventory as inventory

    monkeypatch.setattr(inventory, "_get_text", lambda url, tail_bytes=None: (
        "rtada\r\nOTL;10.09.2026 22:40;18\r\nOTL;10.09.2026 22:50;;\r\n"
    ))
    assert inventory.last_reading("ogd-smn", "OTL") == datetime(2026, 9, 10, 22, 40, tzinfo=timezone.utc)

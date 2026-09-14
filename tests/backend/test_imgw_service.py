"""
Tests de IMGW (Polonia): bulk telemétrico y sinóptico, almacén de series que
alimenta el poller, observación y series desde él, ranking y el histórico del
archivo diario (CSV cp1250 dentro de ZIP, con agrupaciones que cambian según
el año).
"""

from __future__ import annotations

import asyncio
import io
import math
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import imgw, imgw_climo

CODE = "352220385"
TZ = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 7, 16, 12, 30, tzinfo=TZ)
ROW = {
    "id": CODE, "name": "Siedlce", "lat": 52.181, "lon": 22.245, "elev": 152.0,
    "wmo_id": "12385", "archive_kinds": ["synop"],
}


def _utc(local_hour: int, minute: int = 0, *, day: int = 16) -> str:
    local = datetime(2026, 7, day, local_hour, minute, tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _meteo_item(local_hour: int, minute: int = 0, **values):
    item = {"kod_stacji": CODE, "nazwa_stacji": "SIEDLCE", "lat": "52.181", "lon": "22.245"}
    for field in imgw.METEO_FIELDS:
        item[field] = None
        item[f"{field}_data"] = None
    for field, value in values.items():
        item[field] = None if value is None else str(value)
        item[f"{field}_data"] = _utc(local_hour, minute)
    return item


@pytest.fixture(autouse=True)
def _aislado(monkeypatch):
    imgw.STORE.clear()
    imgw._BULK.clear()
    monkeypatch.setattr(imgw, "_load_stations", lambda: [ROW])
    monkeypatch.setattr(imgw, "_station_row", lambda _sid: ROW)
    yield
    imgw.STORE.clear()
    imgw._BULK.clear()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def _fill_store():
    """Una instantánea de ayer a las 23:50 y tres del día, como las dejaría el
    poller."""
    yesterday = _meteo_item(23, 50, temperatura_powietrza=30.0, opad_10min=5.0)
    yesterday["temperatura_powietrza_data"] = _utc(23, 50, day=15)
    yesterday["opad_10min_data"] = _utc(23, 50, day=15)
    snapshots = [
        [yesterday],
        [_meteo_item(0, 0, temperatura_powietrza=18.0, opad_10min=2.0)],
        [_meteo_item(11, 0, temperatura_powietrza=24.0, wilgotnosc_wzgledna=60,
                     wiatr_srednia_predkosc=3.0, wiatr_predkosc_maksymalna=8.0,
                     wiatr_kierunek=220, opad_10min=0.4)],
        [_meteo_item(12, 10, temperatura_powietrza=25.5, wilgotnosc_wzgledna=55,
                     wiatr_predkosc_maksymalna=6.0, opad_10min=0.1)],
    ]
    for payload in snapshots:
        imgw.STORE.ingest_meteo(imgw.parse_meteo(payload))


def test_imgw_service_does_not_import_streamlit() -> None:
    for name in ("imgw.py", "imgw_climo.py"):
        assert "import streamlit" not in Path(f"server/services/{name}").read_text(encoding="utf-8")


def test_parse_meteo_keeps_each_variable_with_its_own_timestamp() -> None:
    item = _meteo_item(11, 0, wilgotnosc_wzgledna=60, opad_10min=0)
    item["temperatura_powietrza"] = "17.2"
    item["temperatura_powietrza_data"] = _utc(10, 10)
    parsed = imgw.parse_meteo([item, {"kod_stacji": "1", "lat": None}])
    assert set(parsed) == {CODE}
    assert parsed[CODE]["temp"][1] == pytest.approx(17.2)
    assert parsed[CODE]["temp"][0] < parsed[CODE]["rh"][0]
    assert parsed[CODE]["precip10"][1] == 0.0


def test_store_ignores_repeated_snapshots_and_matches_synop_by_wmo_suffix() -> None:
    payload = [_meteo_item(11, 0, temperatura_powietrza=24.0)]
    assert imgw.STORE.ingest_meteo(imgw.parse_meteo(payload)) == 1
    assert imgw.STORE.ingest_meteo(imgw.parse_meteo(payload)) == 0
    synop = [{"id_stacji": "12385", "stacja": "Siedlce", "data_pomiaru": "2026-07-16",
              "godzina_pomiaru": "9", "cisnienie": "1015.3"}]
    assert imgw.STORE.ingest_synop(imgw.parse_synop(synop)) == 1
    assert imgw.STORE.last(CODE, "msl")[1] == pytest.approx(1015.3)


def test_store_compacts_old_samples_to_hours_and_prunes(tmp_path) -> None:
    store = imgw.ImgwSeriesStore()
    # Relativo al reloj real: la carga desde disco compacta con la hora actual.
    base = (int(time.time()) // 3600 - 72) * 3600
    for step in range(7):  # 09:00 … 10:00, cada 10 min
        store.add(CODE, "precip10", base + step * 600, 1.0)
        store.add(CODE, "temp", base + step * 600, 10.0 + step)
    store.add(CODE, "temp", base - 9 * 86400, 5.0)  # fuera de la ventana
    store.compact(now=base + 3 * 86400)
    rain = store.samples(CODE, "precip10")
    # 09:00 cierra la hora anterior; 09:10-10:00 suman en la de las 10.
    assert rain == [(base, 1.0), (base + 3600, 6.0)]
    assert store.samples(CODE, "temp") == [(base, 10.0), (base + 3600, 16.0)]

    path = tmp_path / "imgw.json.gz"
    store.save_to_disk(str(path))
    restored = imgw.ImgwSeriesStore()
    assert restored.load_from_disk(str(path))
    assert restored.samples(CODE, "precip10") == rain


def test_fetch_current_reads_the_store_and_local_day_totals() -> None:
    _fill_store()
    imgw.STORE.updated_at = time.time()

    def handler(request):  # el almacén está fresco: no debe llamar
        raise AssertionError(str(request.url))

    async def _test():
        async with _client(handler) as client:
            return await imgw.fetch_current(CODE, client=client, now=NOW)

    obs = _run(_test())
    assert obs["Tc"] == pytest.approx(25.5)
    assert obs["RH"] == pytest.approx(55)
    assert obs["wind"] == pytest.approx(10.8)
    assert obs["gust"] == pytest.approx(6.0 * 3.6)
    assert obs["wind_dir_deg"] == pytest.approx(220)
    # 0.4 + 0.1: los tramos de 10 min de las 23:50 y las 00:00 son de ayer.
    assert obs["precip_total"] == pytest.approx(0.5)
    assert obs["daily_extremes"]["temp_max"] == pytest.approx(25.5)
    assert obs["daily_extremes"]["temp_min"] == pytest.approx(18.0)
    assert obs["daily_extremes"]["gust_max"] == pytest.approx(8.0 * 3.6)
    assert obs["station_name"] == "Siedlce"
    assert not math.isnan(obs["Td"])


def test_fetch_current_without_poller_reads_the_bulk_once() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "synop" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_meteo_item(12, 10, temperatura_powietrza=21.0)])

    async def _test():
        async with _client(handler) as client:
            first = await imgw.fetch_current(CODE, client=client, now=NOW)
            second = await imgw.fetch_today_series(CODE, client=client, now=NOW)
            return first, second

    obs, series = _run(_test())
    assert obs["Tc"] == pytest.approx(21.0)
    assert series["temps"] == [21.0]
    assert calls == [imgw.METEO_URL, imgw.SYNOP_URL]


def test_fetch_current_with_stale_station_is_no_current_data() -> None:
    old = _meteo_item(1, 0, temperatura_powietrza=10.0)
    old["temperatura_powietrza_data"] = "2026-07-10 01:00:00"
    imgw.STORE.ingest_meteo(imgw.parse_meteo([old]))
    imgw.STORE.updated_at = time.time()

    async def _test():
        async with _client(lambda r: httpx.Response(500)) as client:
            return await imgw.fetch_current(CODE, client=client, now=NOW)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"


def test_today_and_recent_series_come_from_the_store() -> None:
    _fill_store()
    imgw.STORE.updated_at = time.time()

    async def _test():
        async with _client(lambda r: httpx.Response(500)) as client:
            today = await imgw.fetch_today_series(CODE, client=client, now=NOW)
            recent = await imgw.fetch_recent_series(CODE, days_back=2, client=client, now=NOW)
            return today, recent

    today, recent = _run(_test())
    assert today["temps"] == [18.0, 24.0, 25.5]
    assert math.isnan(today["precip_step_mm"][0])  # medianoche: tramo de ayer
    assert today["precip_step_mm"][1:] == pytest.approx([0.4, 0.1])
    assert today["gusts"][1] == pytest.approx(28.8)
    assert len(recent["epochs"]) == 4
    assert recent["temps"][0] == pytest.approx(30.0)


def test_build_imgw_daily_aggregates_the_local_day() -> None:
    from server.services import ranking

    _fill_store()
    recs = ranking.build_imgw_daily(now=NOW)
    rec = next(r for r in recs if r.station_id == CODE)
    assert rec.local_date == "2026-07-16"
    assert rec.tmax == pytest.approx(25.5)
    assert rec.tmin == pytest.approx(18.0)
    assert rec.gust == pytest.approx(28.8)
    assert rec.rain == pytest.approx(0.5)
    assert rec.tcur == pytest.approx(25.5)
    assert rec.rain_24h is None  # menos de 20 h de cobertura
    assert rec.name == "Siedlce"


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text.encode("cp1250"))
    return buffer.getvalue()


def test_climo_parses_statuses_and_picks_the_grouping_by_year() -> None:
    synop_d = (
        f'"{CODE}","SIEDLCE","2010","07","01",30.1,"",15.2,"",22.0,"",12.0,"",'
        '.0,"9","",0,"9"\n'
        f'"{CODE}","SIEDLCE","2010","07","02",28.0,"",14.0,"",20.0,"",11.0,"",'
        '4.5,"","W",0,"9"\n'
        '"353230295","BIAŁYSTOK","2010","07","01",1.0,"",1.0,"",1.0,"",1.0,"",1.0,"","",0,"9"\n'
    )
    synop_t = f'"{CODE}","SIEDLCE","2010","07","01",7.9,"",3.0,"",22.0,""\n'
    opad = '"249180020","WARSZOWICE","2015","03","04",3.0,,W,,"9"\n'
    files = {
        "/synop/2010/2010_385_s.zip": _zip("s_d_385_2010.csv", synop_d) + b"",
        "/opad/2015/2015_03_o.zip": _zip("o_d_03_2015.csv", opad),
    }
    # El diario y las medias van en el mismo ZIP.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("s_d_385_2010.csv", synop_d.encode("cp1250"))
        archive.writestr("s_d_t_385_2010.csv", synop_t.encode("cp1250"))
    files["/synop/2010/2010_385_s.zip"] = buffer.getvalue()
    requested = []

    def handler(request):
        path = str(request.url).split("/dobowe", 1)[1]
        requested.append(path)
        payload = files.get(path)
        return httpx.Response(200, content=payload) if payload else httpx.Response(404)

    from server.services.climo_cache import clear_climo_block_cache

    async def _test():
        clear_climo_block_cache()
        async with _client(handler) as client:
            synop = await imgw_climo.fetch_climo_daily_for_periods(
                client, CODE, [(date(2010, 7, 1), date(2010, 7, 2))], today_date=date(2026, 7, 16),
            )
            imgw._station_row = lambda _sid: {"archive_kinds": ["opad"]}  # noqa: B023
            opad_frame = await imgw_climo.fetch_climo_daily_for_periods(
                client, "249180020", [(date(2015, 3, 1), date(2015, 3, 31))],
                today_date=date(2026, 7, 16),
            )
            return synop, opad_frame

    synop, opad_frame = _run(_test())
    # Año cerrado de una sinóptica: primero el fichero de la estación.
    assert requested[0] == "/synop/2010/2010_385_s.zip"
    first = synop.iloc[0]
    assert first["temp_max"] == pytest.approx(30.1)
    assert first["temp_mean"] == pytest.approx(22.0)
    assert first["precip_total"] == 0.0  # estado 9: sin fenómeno
    assert first["wind_mean"] == pytest.approx(3.0 * 3.6)
    assert synop.iloc[1]["precip_total"] == pytest.approx(4.5)
    # Pluviométrica: los días que no salen en el mes son días sin lluvia.
    assert len(opad_frame) == 31
    assert opad_frame["precip_total"].sum() == pytest.approx(3.0)


def test_climo_candidate_urls_follow_the_archive_layout() -> None:
    async def _test():
        imgw_climo._FOLDERS["synop"] = ["1996_2000"]
        imgw_climo._FOLDERS["klimat"] = ["1996_2000"]
        async with _client(lambda r: httpx.Response(500)) as client:
            today = date(2026, 9, 13)
            return (
                await imgw_climo.candidate_urls(client, "synop", CODE, 2026, 7, today=today),
                await imgw_climo.candidate_urls(client, "synop", CODE, 1998, 7, today=today),
                await imgw_climo.candidate_urls(client, "klimat", CODE, 1998, 7, today=today),
                await imgw_climo.candidate_urls(client, "klimat", CODE, 2014, 7, today=today),
            )

    current, old_synop, old_klimat, klimat = _run(_test())
    assert current[0].endswith("/synop/2026/2026_07_s.zip")
    assert old_synop == [f"{imgw_climo.ARCHIVE_URL}/synop/1996_2000/1996_2000_385_s.zip"]
    assert old_klimat == [f"{imgw_climo.ARCHIVE_URL}/klimat/1996_2000/1998_k.zip"]
    assert klimat == [f"{imgw_climo.ARCHIVE_URL}/klimat/2014/2014_07_k.zip"]


def test_read_zip_member_keeps_complete_lines_of_a_damaged_file() -> None:
    text = "".join(f'"249180020","WARSZOWICE","2023","04","{day:02d}",1.0,"","W"\r\n' for day in range(1, 31))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("o_d_04_2023.csv", text.encode("cp1250"))
    raw = bytearray(buffer.getvalue())
    info = zipfile.ZipFile(io.BytesIO(bytes(raw))).infolist()[0]
    # CRC equivocado en las dos cabeceras, como en los ficheros de IMGW.
    bad_crc = (info.CRC ^ 0xFFFF).to_bytes(4, "little")
    good_crc = info.CRC.to_bytes(4, "little")
    raw = bytearray(bytes(raw).replace(good_crc, bad_crc))
    damaged = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    with pytest.raises(zipfile.BadZipFile):
        damaged.read(damaged.infolist()[0])
    data = imgw_climo.read_zip_member(damaged, damaged.infolist()[0])
    assert data.decode("cp1250") == text


def test_ranking_reuses_the_poller_store_and_only_fetches_when_stale() -> None:
    from server.services import ranking

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "synop" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_meteo_item(12, 10, temperatura_powietrza=21.0)])

    async def _cycle():
        async with _client(handler) as client:
            return await ranking.fetch_imgw_daily(client=client, now=NOW)

    _fill_store()
    imgw.STORE.updated_at = time.time()  # el poller acaba de pasar
    recs = _run(_cycle())
    assert calls == []
    assert next(r for r in recs if r.station_id == CODE).tmax == pytest.approx(25.5)

    imgw.STORE.updated_at = time.time() - imgw.STORE_FRESH_S - 60  # poller parado
    _run(_cycle())
    assert calls == [imgw.METEO_URL, imgw.SYNOP_URL]


def test_manual_archive_station_has_no_current_data_without_calling_imgw(monkeypatch) -> None:
    monkeypatch.setattr(imgw, "_station_row", lambda _sid: {"id": "252170070", "realtime": False, "manual": True})

    async def _test():
        async with _client(lambda r: (_ for _ in ()).throw(AssertionError(str(r.url)))) as client:
            return await imgw.fetch_current("252170070", client=client, now=NOW)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_no_current_data"

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from server.services.ranking import RankingStore, StationDaily, _daily_gust_max_from_series


def test_daily_gust_max_discards_isolated_temporal_spike():
    values = [86.0, 91.0, 84.0, 267.5, 88.0, 79.0, 73.0]

    assert _daily_gust_max_from_series(values) == pytest.approx(91.0)


def test_daily_gust_max_keeps_real_high_wind_cluster():
    values = [92.0, 118.0, 143.0, 168.0, 181.0, 174.0, 151.0]

    assert _daily_gust_max_from_series(values) == pytest.approx(181.0)


def test_accumulable_ranking_uses_temporally_filtered_gust_max():
    store = RankingStore()
    now = datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    day = now.date().isoformat()
    gusts = [84.0, 88.0, 91.0, 267.5, 86.0, 79.0, 67.0]

    for hour, gust in enumerate(gusts):
        store.upsert_hourly(
            "AEMET",
            "1437P",
            day=day,
            hour_key=f"{day}T{hour:02d}",
            name="EVC_NOIA",
            locality="",
            lat=42.7208,
            lon=-8.9233,
            values={"tmax": 23.0, "tmin": 14.0, "gust": gust, "rain": 0.0},
        )

    records = store.reduce_accumulable_records("AEMET", now=now)

    assert len(records) == 1
    assert records[0].gust == pytest.approx(91.0)


def test_countries_normalizes_legacy_codes():
    """El selector de países no debe mostrar códigos legacy del catálogo:
    TU (FIPS de Turquía) → TR; AN (Antillas Neerlandesas, disueltas) fuera —
    sus estaciones ya entran con el país real vía point-in-polygon. Cubre los
    registros antiguos que entren por el snapshot persistido."""

    def _rec(station_id: str, country: str) -> StationDaily:
        return StationDaily(
            provider="IEM",
            station_id=station_id,
            name=station_id,
            tmax=30.0,
            country=country,
            local_date="2026-07-02",
        )

    store = RankingStore()
    store.replace_daily(
        "IEM",
        [_rec("TR__ASOS|LTFG", "TU"), _rec("AN__ASOS|TNCC", "AN"), _rec("DE__X|A", "DE")],
    )

    assert store.countries() == ["DE", "TR"]


def test_top_descending_override_returns_opposite_extreme():
    """``descending`` fuerza el sentido del orden: la Tmáx natural va de mayor a
    menor (día más caluroso primero); forzada ascendente devuelve la máxima MÁS
    BAJA (mínimas de máximas), no solo el top-N invertido. Simétrico para Tmín."""

    def _rec(sid: str, tmax: float, tmin: float) -> StationDaily:
        return StationDaily(
            provider="IEM", station_id=sid, name=sid, tmax=tmax, tmin=tmin,
            country="DE", local_date="2026-07-05",
        )

    store = RankingStore()
    store.replace_daily(
        "IEM",
        [_rec("a", 40.0, 20.0), _rec("b", 30.0, 10.0), _rec("c", 35.0, 15.0)],
    )

    # Natural: Tmáx desc (más alta primero), Tmín asc (más baja primero).
    assert [r.station_id for r in store.top("tmax", day="2026-07-05")] == ["a", "c", "b"]
    assert [r.station_id for r in store.top("tmin", day="2026-07-05")] == ["b", "c", "a"]

    # Forzado: Tmáx asc → máxima más baja primero; Tmín desc → mínima más alta.
    assert [
        r.station_id for r in store.top("tmax", day="2026-07-05", descending=False)
    ] == ["b", "c", "a"]
    assert [
        r.station_id for r in store.top("tmin", day="2026-07-05", descending=True)
    ] == ["a", "c", "b"]


def test_tropical_tmax_ceiling_drops_broken_spikes_keeps_real_heat():
    """Estación IEM tropical con serie del día rota (Koh Kong, lat ~11) reporta
    una máxima de 54°C, imposible en el trópico → se anula (sale del ranking de
    máximas), pero su mínima real se conserva. El calor REAL subtropical/tropical
    (Death Valley 54°C @lat36, Sahel 45°C @lat18) NO se toca."""
    from server.services.ranking import _clean_iem_extremes, _tmax_ceiling

    assert _tmax_ceiling(11.6) == 50.0        # trópico profundo
    assert _tmax_ceiling(36.0) == _tmax_ceiling(None)  # subtropical = récord mundial

    # Koh Kong: máxima rota → None; mínima real intacta.
    tmax, tmin, _ = _clean_iem_extremes(54.2, 23.1, None, 11.6, 24.0)
    assert tmax is None
    assert tmin == 23.1
    # Sahel (lat 18) 45°C real y Death Valley (lat 36) 54°C real → se conservan.
    assert _clean_iem_extremes(45.0, 30.0, None, 18.0, 44.0)[0] == 45.0
    assert _clean_iem_extremes(54.0, 35.0, None, 36.0, 52.0)[0] == 54.0
    # Falso positivo evitado: España 37,6°C @lat40 con actual fría (amanecer).
    assert _clean_iem_extremes(37.6, 10.9, None, 40.6, 12.4)[0] == 37.6


def test_cold_minima_never_filtered():
    """El lado FRÍO no se filtra en ningún saneador: el suelo por latitud
    anulaba récords antárticos reales (Concordia −84°C, que IEM ya recorta
    a ~−73 en BUFR y solo llega vía Climantartide)."""
    from server.services.ranking import _clean_iem_extremes, _sanitize_record_extremes

    # IEM: mínima antártica extrema con actual coherente → intacta.
    assert _clean_iem_extremes(-77.9, -84.0, None, -75.1, -79.1)[1] == -84.0
    # Incluso una mínima "sospechosa" en latitudes templadas se conserva.
    assert _clean_iem_extremes(20.0, -40.0, None, 45.0, 18.0)[1] == -40.0

    rec = StationDaily(
        provider="CLIMANTARTIDE", station_id="Concordia",
        name="Concordia (Dome C)", lat=-75.1, lon=123.4,
        tmax=-77.9, tmin=-84.0, country="AQ", local_date="2026-07-19",
    )
    _sanitize_record_extremes(rec)
    assert rec.tmin == -84.0


def test_fetch_climantartide_daily_parses_jsonp_and_buckets_by_local_day():
    """El feed JSONP de climantartide.it (temperatura horaria de las AWS
    italianas) se trocea por día local nominal (huso solar por longitud):
    Concordia (lon 123,4 → UTC+8) mete las 18Z del día D en el día D+1 local.
    La instantánea (tcur) solo va en el último día."""
    import asyncio

    import httpx

    from server.services.ranking import fetch_climantartide_daily

    def _ms(iso: str) -> int:
        return int(datetime.fromisoformat(iso + "+00:00").timestamp()) * 1000

    payload = (
        '({"par":{"Titleg":"Temperature"},"data":['
        '{"name":"Concordia","data":[[%d,-78.9],[%d,-82.9],[%d,-84.0],[%d,-79.1]]},'
        '{"name":"Desconocida","data":[[%d,-10.0]]},'
        '{"name":"Modesta","data":[[%d,null]]}'
        ']});'
    ) % (
        _ms("2026-07-18T06:00:00"), _ms("2026-07-18T12:00:00"),
        _ms("2026-07-18T18:00:00"), _ms("2026-07-19T06:00:00"),
        _ms("2026-07-19T06:00:00"), _ms("2026-07-19T06:00:00"),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    async def _test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await fetch_climantartide_daily(client=client)

    recs = asyncio.run(_test())
    # Solo Concordia (la desconocida se ignora; Modesta sin puntos válidos).
    assert {r.station_id for r in recs} == {"Concordia"}
    by_day = {r.local_date: r for r in recs}
    # 06Z y 12Z del 18 → día local 18; 18Z del 18 (02:00 local del 19) y
    # 06Z del 19 → día local 19.
    assert by_day["2026-07-18"].tmin == -82.9
    assert by_day["2026-07-18"].tmax == -78.9
    assert by_day["2026-07-18"].tcur is None
    assert by_day["2026-07-19"].tmin == -84.0
    assert by_day["2026-07-19"].tcur == -79.1
    assert by_day["2026-07-19"].country == "AQ"


def test_day_options_prefers_recent_day_with_reasonable_coverage():
    """La fecha principal debe ser la más reciente con cobertura razonable,
    no la más poblada: un día pasado COMPLETO ganaba siempre al día en curso
    (el ranking abría enseñando el viernes un domingo)."""

    def _rec(sid: str, day: str) -> StationDaily:
        return StationDaily(
            provider="IEM", station_id=sid, name=sid, tmax=30.0,
            country="DE", local_date=day,
        )

    store = RankingStore()
    # Día pasado completo: 40 estaciones; día en curso: 12 (≥25% → gana por
    # ser más reciente).
    records = [_rec(f"a{i}", "2026-07-04") for i in range(40)]
    records += [_rec(f"b{i}", "2026-07-05") for i in range(12)]
    store.replace_daily("IEM", records)
    days, main = store.day_options()
    assert days == ["2026-07-04", "2026-07-05"]
    assert main == "2026-07-05"

    # Día en curso casi vacío (<25%): cae al pasado completo.
    store2 = RankingStore()
    records = [_rec(f"a{i}", "2026-07-04") for i in range(40)]
    records += [_rec(f"b{i}", "2026-07-05") for i in range(3)]
    store2.replace_daily("IEM", records)
    _, main2 = store2.day_options()
    assert main2 == "2026-07-04"


def test_fetch_smhi_records_accumulates_hourly_bulk():
    import asyncio
    import re

    import httpx

    from server.services.ranking import RankingStore, fetch_smhi_records

    now = datetime(2026, 7, 16, 14, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
    epoch_ms = int(now.replace(hour=13).timestamp()) * 1000

    def _payload(value):
        return {
            "station": [
                {"key": "98230", "value": [{"date": epoch_ms, "value": str(value), "quality": "G"}]},
            ],
        }

    payloads = {"1": _payload(24.5), "21": _payload(10.0), "7": _payload(0.3)}

    def handler(request: httpx.Request) -> httpx.Response:
        match = re.search(r"/parameter/(\d+)/station-set", str(request.url))
        return httpx.Response(200, json=payloads.get(match.group(1) if match else "", {}))

    async def _test(store):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await fetch_smhi_records(store, client=client, now=now)

    store = RankingStore()
    recs = asyncio.run(_test(store))
    assert len(recs) == 1
    rec = recs[0]
    assert rec.station_id == "98230"
    assert rec.name == "Stockholm-Observatoriekullen A"
    assert rec.tmax == pytest.approx(24.5)
    assert rec.gust == pytest.approx(36.0)  # 10 m/s → km/h
    assert rec.rain == pytest.approx(0.3)
    assert rec.tcur == pytest.approx(24.5)

    # Segunda hora con más calor: los extremos acumulan, no se pisan.
    epoch2 = int(now.replace(hour=14).timestamp()) * 1000
    payloads["1"] = {"station": [{"key": "98230", "value": [{"date": epoch2, "value": "27.1", "quality": "G"}]}]}
    payloads["7"] = {"station": [{"key": "98230", "value": [{"date": epoch2, "value": "0.2", "quality": "G"}]}]}
    payloads["21"] = {"station": [{"key": "98230", "value": [{"date": epoch2, "value": "6.0", "quality": "G"}]}]}
    recs = asyncio.run(_test(store))
    rec = recs[0]
    assert rec.tmax == pytest.approx(27.1)
    assert rec.tmin == pytest.approx(24.5)
    assert rec.rain == pytest.approx(0.5)   # suma de horas
    assert rec.gust == pytest.approx(36.0)  # máx del día
    assert rec.tcur == pytest.approx(27.1)


def test_eccc_hourly_precipitation_enters_rolling_24h_flow():
    import asyncio
    from datetime import timedelta, timezone

    import httpx

    from server.services.ranking import RankingStore, fetch_eccc_records

    station_id = "1012475"  # DISCOVERY ISLAND, catalogo ECCC local
    current = {"epoch": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        observed = datetime.fromtimestamp(current["epoch"], tz=timezone.utc)
        return httpx.Response(200, json={
            "features": [{
                "properties": {
                    "obs_date_tm": observed.isoformat().replace("+00:00", "Z"),
                    "msc_id-value": station_id,
                    "air_temp": 12.0,
                    "max_wnd_spd_10m_pst1hr": 25.0,
                    "pcpn_amt_pst1hr": 0.5,
                },
            }],
        })

    async def _test():
        store = RankingStore()
        start = datetime(2026, 7, 15, 12, 10, tzinfo=timezone.utc)
        records = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=5.0,
        ) as client:
            for offset in range(24):
                now = start + timedelta(hours=offset)
                current["epoch"] = int(now.replace(minute=0).timestamp())
                records = await fetch_eccc_records(store, client=client, now=now)
        return store, records, start + timedelta(hours=23)

    store, records, now = asyncio.run(_test())

    assert records
    record = next(rec for rec in records if rec.station_id == station_id)
    assert record.country in ("", "CA")  # el commit del scheduler estampa CA
    assert record.rain_24h == pytest.approx(12.0)
    assert record.rain_24h_at == int(now.replace(minute=0).timestamp())
    store.commit({"ECCC": records}, now=now)
    assert store.current_precipitation_points(now=now) == [
        (pytest.approx(48.4246), pytest.approx(-123.225833), pytest.approx(12.0)),
    ]


def test_retry_backoff_escalates_after_four_failures():
    from server.services.ranking import _retry_backoff_s

    # Fallos transitorios: ritmo base.
    assert [_retry_backoff_s(n) for n in (1, 2, 3, 4)] == [60.0] * 4
    # A partir del 5º se duplica por fallo…
    assert _retry_backoff_s(5) == 120.0
    assert _retry_backoff_s(6) == 240.0
    assert _retry_backoff_s(7) == 480.0
    # …hasta el techo de 15 min.
    assert _retry_backoff_s(8) == 900.0
    assert _retry_backoff_s(20) == 900.0
    # Base configurable (retry_interval_s del loop).
    assert _retry_backoff_s(5, base_s=30.0) == 60.0


# ----------------------------------------------------------------------
# Adaptador IPMA (bulk de 24 h)
# ----------------------------------------------------------------------

def _ipma_client(payload):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _ipma_payload(now_local, station_ids):
    """Feed sintético: ayer 1 lectura fría, hoy dos horas por estación."""
    from datetime import timedelta, timezone as _tz

    def _ts(dt):
        return dt.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M")

    def _reading(temp, prec):
        return {
            "temperatura": temp, "humidade": 50.0, "pressao": -99.0,
            "intensidadeVentoKM": 0.0, "intensidadeVento": 0.0,
            "idDireccVento": 0, "precAcumulada": prec, "radiacao": -99.0,
        }

    yesterday = now_local - timedelta(days=1)
    return {
        _ts(yesterday.replace(hour=23, minute=0)): {
            sid: _reading(5.0, 2.0) for sid in station_ids
        },
        _ts(now_local.replace(hour=9, minute=0)): {
            sid: _reading(18.5, 0.4) for sid in station_ids
        },
        _ts(now_local.replace(hour=13, minute=0)): {
            sid: _reading(29.34, 0.2) for sid in station_ids
        },
    }


def test_fetch_ipma_daily_reduces_local_day():
    import asyncio

    from server.services.ranking import fetch_ipma_daily

    now = datetime(2026, 7, 15, 14, 30, tzinfo=ZoneInfo("Europe/Lisbon"))
    # ≥15 estaciones con dato hoy para no activar el fallback de madrugada.
    sids = [f"99000{i:02d}" for i in range(16)] + ["1210883"]
    payload = _ipma_payload(now, sids)

    async def _test():
        async with _ipma_client(payload) as client:
            return await fetch_ipma_daily(client=client, now=now)

    recs = asyncio.run(_test())
    assert len(recs) == len(sids)
    by_id = {r.station_id: r for r in recs}

    rec = by_id["1210883"]
    # Solo las horas de HOY local: la lectura fría de ayer queda fuera.
    assert rec.tmax == pytest.approx(29.3)
    assert rec.tmin == pytest.approx(18.5)
    assert rec.rain == pytest.approx(0.6)
    assert rec.rain_24h == pytest.approx(2.6)
    assert rec.rain_24h_at is not None
    assert rec.gust is None  # IPMA no reporta ráfaga
    assert rec.tcur == pytest.approx(29.3)
    assert rec.local_date == "2026-07-15"
    assert rec.local_time == "13:00"
    # Metadatos del catálogo local.
    assert rec.name == "Tavira"
    assert rec.locality == "Continente"
    assert rec.lat == pytest.approx(37.1217, abs=1e-3)


def test_fetch_ipma_daily_falls_back_to_previous_day_before_dawn():
    import asyncio

    from server.services.ranking import fetch_ipma_daily
    from datetime import timedelta

    # A las 00:10 el día local aún no tiene lecturas → se mantiene ayer.
    now = datetime(2026, 7, 15, 0, 10, tzinfo=ZoneInfo("Europe/Lisbon"))
    sids = [f"99000{i:02d}" for i in range(16)]
    yesterday = now - timedelta(days=1)
    payload = _ipma_payload(yesterday, sids)

    async def _test():
        async with _ipma_client(payload) as client:
            return await fetch_ipma_daily(client=client, now=now)

    recs = asyncio.run(_test())
    assert recs, "el fallback debe publicar el día anterior"
    assert all(r.local_date == "2026-07-14" for r in recs)


# ----------------------------------------------------------------------
# Adaptador GeoSphere (bulk de 10 min)
# ----------------------------------------------------------------------

def _gs_payload(now_local, station_ids):
    from datetime import timezone as _tz

    def _ts(hour, minute):
        return now_local.replace(hour=hour, minute=minute).astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M+00:00")

    features = []
    for sid in station_ids:
        features.append({
            "type": "Feature",
            "properties": {
                "station": sid,
                "parameters": {
                    # Medias 21.0/24.0 pero extremos por bloque 20.1..25.3:
                    # el diario debe salir de TLMAX/TLMIN, no de TL.
                    "TL": {"data": [21.0, 24.0]},
                    "TLMAX": {"data": [21.4, 25.3]},
                    "TLMIN": {"data": [20.1, 23.6]},
                    "FFX": {"data": [5.0, 10.0]},   # m/s → racha diaria 36 km/h
                    "RR": {"data": [0.2, 0.3]},
                },
            },
        })
    return {"timestamps": [_ts(10, 0), _ts(14, 0)], "features": features}


def test_fetch_geosphere_daily_uses_block_extremes():
    import asyncio
    import httpx

    from server.services.ranking import fetch_geosphere_daily

    now = datetime(2026, 7, 15, 15, 0, tzinfo=ZoneInfo("Europe/Vienna"))
    sids = [f"77{i:03d}" for i in range(16)] + ["11035"]
    payload = _gs_payload(now, sids)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["station_ids"] = request.url.params.get("station_ids", "")
        return httpx.Response(200, json=payload)

    async def _test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            return await fetch_geosphere_daily(client=client, now=now)

    recs = asyncio.run(_test())
    # Solo IDs TAWES numéricos: un id KLIMA (K…) en el bulk provoca HTTP 400.
    assert captured["station_ids"]
    assert all(sid.isdigit() for sid in captured["station_ids"].split(","))
    by_id = {r.station_id: r for r in recs}
    rec = by_id["11035"]
    assert rec.tmax == pytest.approx(25.3)   # extremo del bloque, no la media
    assert rec.tmin == pytest.approx(20.1)
    assert rec.gust == pytest.approx(36.0)   # 10 m/s → km/h
    assert rec.rain == pytest.approx(0.5)
    assert rec.rain_24h == pytest.approx(0.5)
    assert rec.rain_24h_at is not None
    assert rec.tcur == pytest.approx(24.0)
    assert rec.local_date == "2026-07-15"
    assert rec.local_time == "14:00"
    assert rec.name == "WIEN/HOHE WARTE"
    assert rec.locality == "Wien"

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

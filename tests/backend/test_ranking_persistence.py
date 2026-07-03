"""Persistencia en disco del RankingStore (snapshot gzip JSON).

El store es memoria pura; el snapshot es lo que hace que un redeploy en
Railway (con Volume) no pierda los días anteriores del selector ni las
horas acumuladas de AEMET/Meteo-France.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from server.services.ranking import RankingStore, StationDaily


def _populated_store() -> RankingStore:
    store = RankingStore()
    now = datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    store.replace_daily(
        "METEOGALICIA",
        [
            StationDaily(
                provider="METEOGALICIA",
                station_id="10045",
                name="CIS Ferrol",
                locality="Ferrol",
                lat=43.48,
                lon=-8.25,
                tmax=27.3,
                tmin=15.1,
                gust=54.0,
                rain=0.2,
            )
        ],
        now=now,
    )
    for hour in range(3):
        store.upsert_hourly(
            "AEMET",
            "1437P",
            day=now.date().isoformat(),
            hour_key=f"{now.date().isoformat()}T{hour:02d}",
            name="EVC_NOIA",
            locality="",
            lat=42.7208,
            lon=-8.9233,
            values={"tmax": 20.0 + hour, "tmin": 14.0, "gust": 60.0, "rain": 0.5},
        )
    store.updated_at = datetime(2026, 7, 2, 12, 5, tzinfo=timezone.utc)
    return store


def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "ranking_state.json.gz")
    original = _populated_store()
    original.save_to_disk(path)

    restored = RankingStore()
    assert restored.load_from_disk(path) is True

    assert restored.updated_at == original.updated_at

    # Agregados diarios directos intactos (y siguen siendo StationDaily).
    day = "2026-07-02"
    rows = restored.top("tmax", providers=["METEOGALICIA"], day=day, limit=5)
    assert [r.station_id for r in rows] == ["10045"]
    assert rows[0].tmax == pytest.approx(27.3)
    assert rows[0].local_date == day

    # Horas acumuladas intactas: el próximo ciclo pide solo las que faltan.
    assert restored.accumulated_hours("AEMET", day) == {
        f"{day}T00", f"{day}T01", f"{day}T02",
    }
    now = datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    records = restored.reduce_accumulable_records("AEMET", now=now)
    assert len(records) == 1
    assert records[0].tmax == pytest.approx(22.0)


def test_load_missing_file_returns_false(tmp_path):
    store = RankingStore()
    assert store.load_from_disk(str(tmp_path / "no_existe.json.gz")) is False
    assert store.updated_at is None


def test_load_corrupt_file_keeps_store_empty(tmp_path):
    path = tmp_path / "ranking_state.json.gz"
    path.write_bytes(b"esto no es un gzip")
    store = RankingStore()
    assert store.load_from_disk(str(path)) is False
    assert store.providers() == []


def test_load_ignores_unknown_snapshot_version(tmp_path):
    import gzip
    import json

    path = tmp_path / "ranking_state.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"version": 99, "daily": [], "hourly": []}, fh)
    store = RankingStore()
    assert store.load_from_disk(str(path)) is False


def test_load_ignores_unknown_station_fields(tmp_path):
    """Snapshot escrito por una versión más nueva (campos extra) no revienta."""
    import gzip
    import json

    path = tmp_path / "ranking_state.json.gz"
    record = {
        "provider": "METEOGALICIA",
        "station_id": "10045",
        "name": "CIS Ferrol",
        "tmax": 27.3,
        "campo_del_futuro": "x",
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(
            {
                "version": 1,
                "updated_at": None,
                "daily": [["METEOGALICIA", "2026-07-02", {"10045": record}]],
                "hourly": [],
            },
            fh,
        )
    store = RankingStore()
    assert store.load_from_disk(str(path)) is True
    rows = store.top("tmax", providers=["METEOGALICIA"], day="2026-07-02", limit=5)
    assert [r.station_id for r in rows] == ["10045"]


def test_save_is_atomic_leaves_no_tmp(tmp_path):
    path = str(tmp_path / "ranking_state.json.gz")
    _populated_store().save_to_disk(path)
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == ["ranking_state.json.gz"]

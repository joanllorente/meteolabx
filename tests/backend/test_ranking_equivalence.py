"""El ranking da lo mismo antes y después de compactar su memoria.

Se escribieron ANTES de reorganizar el estado horario por columnas y los
registros diarios con ``__slots__``, y sus referencias se generaron con el
código anterior. Un escenario determinista ejercita los caminos raros —horas
reescritas y mezcladas, huecos, ``None``, centinelas, NaN, enteros donde se
esperan decimales, marcas de tiempo ilegibles, lluvia imposible— y se compara
TODO lo observable: tipos incluidos, porque un ``0`` y un ``0.0`` no se
serializan igual en la API.

Para regenerar las referencias (solo si el cambio de comportamiento es
deliberado): ``METEOLABX_REGENERATE_RANKING_GOLDEN=1 pytest`` sobre este fichero.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
import os
from pathlib import Path
import random
import shutil

import pytest

from server.services import station_silence, suspect_data
from server.services.ranking import RankingStore, StationDaily

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "ranking_equivalence_golden.json"
# Snapshot escrito por el código anterior a la compactación: el nuevo tiene
# que seguir leyéndolo, o el primer despliegue perdería el día acumulado.
LEGACY_SNAPSHOT = FIXTURES / "ranking_state_v1.json.gz"
REGENERATE = os.getenv("METEOLABX_REGENERATE_RANKING_GOLDEN", "").lower() in {"1", "true", "yes"}

NOW = datetime(2026, 9, 30, 12, 34, tzinfo=timezone.utc)
HOURLY_PROVIDERS = ("AEMET", "METEOFRANCE", "IEM", "SMHI", "METEOSWISS", "ECCC")
DAILY_PROVIDERS = ("IEM", "METEOCAT", "METEOGALICIA")
FLOAT_FIELDS = ("rain", "tmax", "tmin", "gust", "tcur", "wind", "wind_dir")
EPOCH_FIELDS = ("rain_at", "tcur_at", "wind_at")


@pytest.fixture(autouse=True)
def _clean_registries():
    suspect_data.clear()
    station_silence.clear()
    yield
    suspect_data.clear()
    station_silence.clear()


def _day_offsets(store: RankingStore, provider: str) -> list[str]:
    today = datetime.fromisoformat(store.local_day(provider, NOW)).date()
    # Hoy, ayer, anteayer y uno viejo que las podas tienen que quitar.
    return [(today - timedelta(days=offset)).isoformat() for offset in (0, 1, 2, 5)]


def _float_value(rng: random.Random, field: str):
    roll = rng.random()
    if roll < 0.08:
        return None
    if roll < 0.10 and field == "rain":
        return 0  # Entero donde se espera un decimal.
    if roll < 0.11:
        return float("nan")
    if roll < 0.12 and field == "rain":
        return -9999.0  # Centinela de «sin dato».
    if roll < 0.14 and field == "rain":
        return -0.2  # Ruido negativo del pluviómetro.
    if roll < 0.15:
        return -0.0
    base = {
        "rain": (0.0, 4.0), "tmax": (-5.0, 38.0), "tmin": (-12.0, 25.0),
        "gust": (5.0, 120.0), "tcur": (-8.0, 36.0), "wind": (0.0, 60.0),
        "wind_dir": (0.0, 720.0),
    }[field]
    return round(rng.uniform(*base), rng.choice((1, 2, 3)))


def _epoch_value(rng: random.Random, epoch: int):
    roll = rng.random()
    if roll < 0.05:
        return None
    if roll < 0.07:
        return str(epoch)  # Legible con int().
    if roll < 0.08:
        return "ilegible"
    return epoch + rng.choice((0, 0, 0, 600, -1200))


def _values(rng: random.Random, epoch: int, *, partial: bool) -> dict:
    fields = list(FLOAT_FIELDS + EPOCH_FIELDS)
    rng.shuffle(fields)
    count = rng.randint(1, 4) if partial else rng.randint(2, len(fields))
    values = {}
    for field in fields[:count]:
        values[field] = (
            _epoch_value(rng, epoch) if field in EPOCH_FIELDS else _float_value(rng, field)
        )
    return values


def _populate(store: RankingStore) -> None:
    rng = random.Random(20260930)
    for provider in HOURLY_PROVIDERS:
        for day_index, day in enumerate(_day_offsets(store, provider)):
            midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
            for number in range(18):
                sid = f"{provider[:3]}{number:03d}"
                # Solo lluvia, como casi todo IEM; o de todo, como AEMET.
                rain_only = provider == "IEM" and number % 3
                hours = list(range(24))
                rng.shuffle(hours)
                hours = hours[: rng.randint(3, 24)]
                if provider == "IEM" and number % 4 == 0:
                    hours += [f"{hour}:30" for hour in hours[:5]]  # Semihorarios.
                for hour in hours:
                    if isinstance(hour, str):
                        hour_key = f"{day}T{int(hour.split(':')[0]):02d}:30"
                        epoch = int(midnight.timestamp()) + int(hour.split(":")[0]) * 3600 + 1800
                    else:
                        hour_key = f"{day}T{hour:02d}"
                        epoch = int(midnight.timestamp()) + hour * 3600
                    if rain_only:
                        values = {"rain": _float_value(rng, "rain"), "rain_at": _epoch_value(rng, epoch)}
                    else:
                        values = _values(rng, epoch, partial=False)
                    if number == 7 and hour == hours[0]:
                        values.update(rain=260.0, rain_at=epoch)  # Salto imposible.
                    store.upsert_hourly(
                        provider, sid, day=day, hour_key=hour_key,
                        name=f"Estación {sid}" if day_index or number % 5 else "",
                        locality=rng.choice(("", "Loc", f"Loc {number}")),
                        lat=round(rng.uniform(-60, 70), 4) if rng.random() > 0.05 else None,
                        lon=round(rng.uniform(-170, 170), 4),
                        values=values,
                    )
                    # Otra pasada de la misma hora: completa o pisa campos.
                    if rng.random() < 0.3:
                        store.upsert_hourly(
                            provider, sid, day=day, hour_key=hour_key,
                            name=f"Estación {sid} (renombrada)", locality="",
                            lat=10.0, lon=20.0,
                            values=_values(rng, epoch, partial=True),
                        )
                    # Una hora sin ningún valor también cuenta como acumulada.
                    if rng.random() < 0.03:
                        store.upsert_hourly(
                            provider, sid, day=day, hour_key=f"{hour_key}:vacía",
                            name="", locality="", lat=None, lon=None, values={},
                        )
    for provider in DAILY_PROVIDERS:
        records = []
        for number in range(30):
            day = _day_offsets(store, provider)[number % 3]
            records.append(StationDaily(
                provider=provider, station_id=f"D{provider[:2]}{number:03d}",
                name=f"Diaria {number}", locality=rng.choice(("", "Pueblo")),
                lat=round(rng.uniform(35, 60), 3), lon=round(rng.uniform(-10, 20), 3),
                tmax=_float_value(rng, "tmax"), tmin=_float_value(rng, "tmin"),
                gust=_float_value(rng, "gust"), rain=_float_value(rng, "rain"),
                rain_24h=_float_value(rng, "rain"),
                rain_24h_at=int(NOW.timestamp()) - rng.randint(0, 20000),
                tcur=_float_value(rng, "tcur"), tcur_at=int(NOW.timestamp()) - rng.randint(0, 12000),
                wind=_float_value(rng, "wind"), wind_dir=_float_value(rng, "wind_dir"),
                wind_at=int(NOW.timestamp()) - rng.randint(0, 12000),
                country=rng.choice(("ES", "FR", "US", "")), local_date=day,
                local_time=rng.choice(("", "10:20")),
            ))
        store.replace_daily(provider, records, now=NOW)


def _encode(value):
    """Forma comparable que conserva el tipo: 0, 0.0, -0.0 y NaN difieren."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return {"int": value}
    if isinstance(value, float):
        return {"float": "nan" if math.isnan(value) else repr(value)}
    if isinstance(value, StationDaily):
        return {"record": {key: _encode(item) for key, item in asdict(value).items()}}
    if isinstance(value, dict):
        return {"dict": [[_encode(key), _encode(item)] for key, item in value.items()]}
    if isinstance(value, set):
        return {"set": sorted((_encode(item) for item in value), key=json.dumps)}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    raise TypeError(type(value))


def _all_days(store: RankingStore, provider: str) -> list[str]:
    return _day_offsets(store, provider)


def _digest(store: RankingStore) -> dict:
    """Todo lo que el resto del programa puede ver del ranking, en orden fijo.

    El orden importa: rolling y reduce podan días, y las lecturas del mapa
    redondean los registros al leerlos.
    """
    out: dict = {"hours_before": {}, "stations_before": {}}
    for provider in HOURLY_PROVIDERS:
        for day in _all_days(store, provider):
            out["hours_before"][f"{provider}|{day}"] = _encode(store.accumulated_hours(provider, day))
            out["stations_before"][f"{provider}|{day}"] = _encode(store.hourly_station_ids(provider, day))
    out["rolling"] = {
        provider: _encode(store.rolling_rain_24h_by_station(provider, now=NOW))
        for provider in HOURLY_PROVIDERS
    }
    out["reduce"] = {
        provider: _encode(store.reduce_accumulable_records(provider, now=NOW))
        for provider in HOURLY_PROVIDERS
    }
    out["hours_after"] = {
        f"{provider}|{day}": _encode(store.accumulated_hours(provider, day))
        for provider in HOURLY_PROVIDERS for day in _all_days(store, provider)
    }
    out["quarantine"] = sorted(
        (_encode(flag) for flag in suspect_data.export_state()["flags"]), key=json.dumps
    )
    out["providers"] = store.providers()
    out["countries"] = store.countries()
    out["day_options"] = _encode(store.day_options())
    out["top"] = {}
    for metric in ("tmax", "tmin", "gust", "rain", "rain_24h"):
        for country in (None, "ES", "US"):
            for descending in (None, True):
                key = f"{metric}|{country}|{descending}"
                out["top"][key] = _encode(store.top(
                    metric, country=country, descending=descending, limit=15, now=NOW))
    out["station_daily"] = _encode([
        store.station_daily(provider, f"D{provider[:2]}{number:03d}")
        for provider in DAILY_PROVIDERS for number in (0, 4, 29)
    ])
    out["temperature"] = _encode(store.current_temperature_records(now=NOW))
    out["wind"] = _encode(store.current_wind_records(now=NOW))
    out["precipitation"] = _encode(store.current_precipitation_records(now=NOW))
    return out


def _check(digest: dict, name: str) -> None:
    if REGENERATE:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        stored = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        stored[name] = digest
        GOLDEN.write_text(json.dumps(stored, ensure_ascii=False, sort_keys=True, indent=0))
        return
    expected = json.loads(GOLDEN.read_text())[name]
    for section in expected:
        assert digest[section] == expected[section], f"cambia «{section}»"
    assert digest.keys() == expected.keys()


def _fresh_store() -> RankingStore:
    store = RankingStore()
    store.updated_at = NOW
    _populate(store)
    return store


def test_live_store_matches_the_previous_code():
    _check(_digest(_fresh_store()), "live")


def test_a_saved_and_restored_store_matches_the_previous_code(tmp_path):
    path = tmp_path / "ranking_state.json.gz"
    store = _fresh_store()
    store.save_to_disk(str(path))
    exported_quarantine = suspect_data.export_state()
    restored = RankingStore()
    suspect_data.clear()
    assert restored.load_from_disk(str(path)) is True
    assert suspect_data.export_state()["flags"] == exported_quarantine["flags"]
    assert restored.updated_at == NOW
    _check(_digest(restored), "restored")


def test_a_snapshot_written_by_the_previous_code_still_loads(tmp_path):
    if REGENERATE:
        _fresh_store().save_to_disk(str(LEGACY_SNAPSHOT))
    path = tmp_path / "ranking_state.json.gz"
    shutil.copy(LEGACY_SNAPSHOT, path)
    restored = RankingStore()
    assert restored.load_from_disk(str(path)) is True
    _check(_digest(restored), "restored")


def test_an_old_quality_schema_still_discards_iem_from_a_legacy_snapshot(tmp_path):
    payload = json.loads(gzip.decompress(LEGACY_SNAPSHOT.read_bytes()))
    payload["quality_filter_schema"] = 3
    path = tmp_path / "ranking_state.json.gz"
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    restored = RankingStore()
    assert restored.load_from_disk(str(path)) is True
    assert all(not restored.accumulated_hours("IEM", day) for day in _all_days(restored, "IEM"))
    assert all(not restored.hourly_station_ids("IEM", day) for day in _all_days(restored, "IEM"))
    assert restored.accumulated_hours("AEMET", _all_days(restored, "AEMET")[0])
    _check(_digest(restored), "legacy_schema_3")


def test_a_reader_that_only_knows_the_previous_format_keeps_the_daily_part(tmp_path):
    """Si hubiera que volver atrás, el código anterior tiene que poder arrancar
    con el snapshot nuevo sin perder al menos los agregados diarios."""
    path = tmp_path / "ranking_state.json.gz"
    store = _fresh_store()
    store.save_to_disk(str(path))
    payload = json.loads(gzip.decompress(path.read_bytes()))
    assert payload["version"] == 1
    daily = {
        (provider, day): {sid: StationDaily(**record) for sid, record in stations.items()}
        for provider, day, stations in payload["daily"]
    }
    assert set(daily) == set(store._daily)
    for key, stations in daily.items():
        # Con _encode: los NaN no son iguales a sí mismos en un ==.
        assert _encode(stations) == _encode(store._daily[key])
    # El formato anterior de las horas sigue siendo una lista, aunque vaya vacía.
    assert isinstance(payload["hourly"], list)


def test_meteoswiss_reads_the_stations_it_already_accumulated():
    store = _fresh_store()
    yesterday = _all_days(store, "METEOSWISS")[1]
    assert store.hourly_station_ids("METEOSWISS", yesterday) == {
        f"MET{number:03d}" for number in range(18)
    }
    assert store.hourly_station_ids("METEOSWISS", "1999-01-01") == set()

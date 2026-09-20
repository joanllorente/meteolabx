"""Cuarentena de lluvia y viento decidida por el propio bulk.

Hasta ahora solo la ficha juzgaba el pluviómetro, así que una estación que
nadie abría no se marcaba nunca: PVG (Norfolk, Virginia) quedó primera de
lluvia del 11 de septiembre de 2026 con 254 mm que no existieron. Y el bulk
descartaba rachas imposibles en silencio: el ranking quedaba limpio, pero ni el
panel ni la ficha sabían que el anemómetro había mentido.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from domain import observation_warnings
from server.services import ranking, suspect_data
from server.services.ranking import RankingStore, StationDaily

DIA = "2026-09-12"
T0 = 1_789_200_000
# `reduce_accumulable_records` solo mira el día en curso y el anterior según su
# reloj, así que sin fijarlo el test caducaba: a los cuatro días de escribirlo,
# DIA dejaba de entrar y la cuarentena no se anotaba nunca.
AHORA = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _limpio():
    suspect_data.clear()
    yield
    suspect_data.clear()


def _cada(minutos: int, cantidades):
    return [(T0 + i * minutos * 60, mm) for i, mm in enumerate(cantidades)]


# --- A · lluvia -------------------------------------------------------------


def test_a_sensor_jump_in_a_ten_minute_series_is_quarantined() -> None:
    """GeoSphere o MeteoHub: 250 mm en diez minutos no los da ninguna nube."""
    marcada = ranking._flag_implausible_rain(
        _cada(10, [0.2, 0.0, 250.0, 0.4]), provider="GEOSPHERE", station_id="11035", day=DIA,
    )
    assert marcada
    params = suspect_data.flags_for("GEOSPHERE", "11035", DIA)[suspect_data.PRECIPITATION]
    assert params["reason"] == "intensity"
    assert params["amount_mm"] == 250.0
    assert params["minutes"] == 10.0


def test_a_real_downpour_is_left_alone() -> None:
    """Un aguacero serio —60 mm en diez minutos, 150 en una hora— es lluvia."""
    assert not ranking._flag_implausible_rain(
        _cada(10, [5.0, 60.0, 40.0, 25.0, 15.0, 5.0]),
        provider="GEOSPHERE", station_id="11035", day=DIA,
    )
    assert suspect_data.flags_for("GEOSPHERE", "11035", DIA) == {}


def test_a_jump_in_the_first_interval_is_judged_too() -> None:
    """Sin un punto previo, el primer intervalo del día no tendría duración."""
    assert ranking._flag_implausible_rain(
        _cada(10, [250.0, 0.0, 0.0]), provider="METEOCAT", station_id="X4", day=DIA,
    )


def test_missing_hours_only_make_the_curve_more_permissive() -> None:
    """Con huecos, la duración real crece: la curva afloja, nunca aprieta."""
    muestras = [(T0, 0.0), (T0 + 600, 0.0), (T0 + 1200, 0.0), (T0 + 4 * 3600, 150.0)]
    assert not ranking._flag_implausible_rain(
        muestras, provider="GEOSPHERE", station_id="11035", day=DIA,
    )


def test_without_identity_nothing_is_flagged() -> None:
    assert not ranking._flag_implausible_rain(
        _cada(10, [0.0, 250.0]), provider="GEOSPHERE", station_id="11035", day="",
    )


def test_the_accumulable_bulk_quarantines_its_hourly_rain() -> None:
    """SMHI, Frost, LHMT… pasan por las horas del store: 700 mm en una hora."""
    store = RankingStore()
    for hora, mm in ((10, 0.0), (11, 700.0), (12, 0.2)):
        store.upsert_hourly(
            "SMHI", "97400", day=DIA, hour_key=f"{DIA}T{hora:02d}",
            name="Test", locality="", lat=59.3, lon=18.0,
            values={"rain": mm, "rain_at": T0 + hora * 3600},
        )
    store.reduce_accumulable_records("SMHI", now=AHORA)
    assert suspect_data.is_flagged("SMHI", "97400", DIA, suspect_data.PRECIPITATION)


def test_an_impossible_daily_total_leaves_the_quarantine_set() -> None:
    rec = StationDaily(provider="IPMA", station_id="1200545", name="Test")
    rec.rain, rec.local_date = 2500.0, DIA
    ranking._sanitize_record_extremes(rec)
    assert rec.rain is None
    params = suspect_data.flags_for("IPMA", "1200545", DIA)[suspect_data.PRECIPITATION]
    assert params == {"reason": "world_record", "amount_mm": 2500.0}


# --- C · viento -------------------------------------------------------------


def test_a_gust_above_the_world_record_leaves_the_quarantine_set() -> None:
    rec = StationDaily(provider="FROST", station_id="SN18700", name="Test")
    rec.gust, rec.local_date = 468.6, DIA
    ranking._sanitize_record_extremes(rec)
    assert rec.gust is None
    params = suspect_data.flags_for("FROST", "SN18700", DIA)[suspect_data.WIND]
    assert params["reason"] == "world_record"


def test_impossible_values_inside_a_gust_series_are_flagged() -> None:
    """Se tiraban al filtrar la serie, antes incluso del pico aislado."""
    maxima = ranking._daily_gust_max_from_series(
        [40.0, 45.0, 900.0, 42.0], provider="METEOCAT", station_id="X4", day=DIA,
    )
    assert maxima == 45.0
    params = suspect_data.flags_for("METEOCAT", "X4", DIA)[suspect_data.WIND]
    assert params["reason"] == "world_record"
    assert params["maximum_kmh"] == 900.0


def test_iem_gust_denied_by_its_own_sustained_wind_is_flagged() -> None:
    """Atlantic City: 200 nudos de racha con 10 de viento medio."""
    fila = {
        "station": "ACY", "name": "Atlantic City", "state": "NJ",
        "lat": 39.45, "lon": -74.57, "local_date": DIA,
        "local_valid": f"{DIA}T14:54:00", "utc_valid": f"{DIA}T18:54:00Z",
        "tmpf": 77.0, "max_tmpf": 81.0, "min_tmpf": 68.0,
        "sknt": 8.0, "drct": 200.0, "max_gust": 200.0, "max_sknt": 10.0,
    }
    registros = ranking._parse_iem_network("NJ_ASOS", [fila], {"NJ_ASOS|ACY": "US"})
    assert registros and registros[0].gust is None
    params = suspect_data.flags_for("IEM", "NJ_ASOS|ACY", DIA)[suspect_data.WIND]
    assert params["reason"] == "sustained_mismatch"
    assert params["sustained_kmh"] == pytest.approx(18.5, abs=0.1)


def test_a_normal_iem_gust_is_not_flagged() -> None:
    fila = {
        "station": "ACY", "name": "Atlantic City", "state": "NJ",
        "lat": 39.45, "lon": -74.57, "local_date": DIA,
        "local_valid": f"{DIA}T14:54:00", "utc_valid": f"{DIA}T18:54:00Z",
        "tmpf": 77.0, "max_tmpf": 81.0, "min_tmpf": 68.0,
        "sknt": 8.0, "drct": 200.0, "max_gust": 35.0, "max_sknt": 20.0,
    }
    registros = ranking._parse_iem_network("NJ_ASOS", [fila], {"NJ_ASOS|ACY": "US"})
    assert registros and registros[0].gust is not None
    assert suspect_data.flags_for("IEM", "NJ_ASOS|ACY", DIA) == {}


# --- La ficha avisa de lo que marcó el bulk ---------------------------------


def _avisos(provider: str, station_id: str, *, ya=()):
    from server.routers import observations

    return observations._warnings_from_bulk_quarantine(
        provider, station_id, {"epochs": [], "temps": []},
        already=list(ya), tz_name="Europe/Madrid", observation_epoch=T0,
    )


def test_the_station_page_warns_about_bulk_rain_quarantine() -> None:
    suspect_data.flag(
        "GEOSPHERE", "11035", "2026-09-12", suspect_data.PRECIPITATION,
        params={"reason": "intensity", "amount_mm": 250.0, "minutes": 10.0},
    )
    avisos = _avisos("GEOSPHERE", "11035")
    assert [aviso["code"] for aviso in avisos] == [observation_warnings.SUSPECT_PRECIPITATION]


def test_the_station_page_warns_about_bulk_wind_quarantine() -> None:
    suspect_data.flag(
        "IEM", "NJ_ASOS|ACY", "2026-09-12", suspect_data.WIND,
        params={"reason": "sustained_mismatch", "maximum_kmh": 370.4},
    )
    avisos = _avisos("IEM", "NJ_ASOS|ACY")
    assert avisos == [observation_warnings.suspect_wind(370.4)]


def test_the_station_page_does_not_repeat_its_own_warning() -> None:
    suspect_data.flag(
        "GEOSPHERE", "11035", "2026-09-12", suspect_data.PRECIPITATION,
        params={"amount_mm": 250.0, "minutes": 10.0},
    )
    propio = observation_warnings.suspect_precipitation(250.0, 10.0)
    assert _avisos("GEOSPHERE", "11035", ya=[propio]) == []


def test_a_clean_station_gets_no_warning() -> None:
    assert _avisos("GEOSPHERE", "11035") == []

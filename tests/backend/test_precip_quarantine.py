"""Control de calidad del pluviómetro y cuarentena de la variable sospechosa.

El caso que lo motivó: PAKF (IEM, False Pass, Alaska) con el pluviómetro
disparándose —el acumulado del día subía a saltos hasta 255 mm mientras el
resto de Alaska no pasaba de 6—. Ese acumulado no llega al récord de 24 h, así
que el filtro de récords del ranking no lo veía; y su pico, 47,7 mm en cinco
minutos, tampoco supera el récord mundial de esa duración (63 mm). Lo que sí
lo delata es que ninguno de los 79 partes del día reportaba precipitación.
"""

from __future__ import annotations

import math

import pytest

from domain import observation_warnings
from domain.precip_quality import (
    RECORD_SAFETY_FACTOR,
    WORLD_RECORD_CURVE,
    max_plausible_precip_mm,
    sanitize_precip_series,
    worst_jump,
)
from server.services import iem, ranking, suspect_data


@pytest.fixture(autouse=True)
def _clean_registry():
    suspect_data.clear()
    yield
    suspect_data.clear()


# --------------------------------------------------------------------------
# Capa 1: curva intensidad-duración
# --------------------------------------------------------------------------

def test_the_ceiling_falls_with_duration() -> None:
    """El techo NO es una constante en mm/min: un aguacero da 38 mm en un
    minuto, pero nadie sostiene ese ritmo una hora. Si el límite fuese plano,
    filtraría lluvia real en los intervalos cortos y dejaría pasar basura en
    los largos."""
    rate_1min = max_plausible_precip_mm(1.0) / 1.0
    rate_1hour = max_plausible_precip_mm(60.0) / 60.0
    rate_1day = max_plausible_precip_mm(1440.0) / 1440.0

    assert rate_1min > rate_1hour > rate_1day


def test_every_world_record_survives_with_margin() -> None:
    """Ningún récord vigente puede quedar descartado: el margen existe
    precisamente porque los récords se baten."""
    for minutes, record_mm in WORLD_RECORD_CURVE:
        limit = max_plausible_precip_mm(minutes)
        assert limit == pytest.approx(record_mm * RECORD_SAFETY_FACTOR)
        assert limit > record_mm

    # Y un récord batido por un margen razonable tampoco cae.
    assert max_plausible_precip_mm(60.0) > 401.0 * 1.4


def test_impossible_jump_is_trimmed_and_the_rest_of_the_day_shifts_down() -> None:
    """La serie es ACUMULADA: al descartar un escalón hay que bajar también
    todo lo que viene detrás, o el recorte deja un diente de sierra."""
    limit = max_plausible_precip_mm(5.0)
    epochs = [0, 300, 600, 900]
    precips = [1.0, 1.0 + limit * 2, 3.0 + limit * 2, 5.0 + limit * 2]
    cleaned, jumps = sanitize_precip_series(epochs, precips)

    assert len(jumps) == 1
    assert jumps[0].amount_mm == pytest.approx(limit * 2)
    assert jumps[0].limit_mm == pytest.approx(limit)
    assert cleaned == pytest.approx([1.0, 1.0, 3.0, 5.0])


def test_real_rain_survives_the_curve() -> None:
    """Un chaparrón muy fuerte de verdad no se toca: 12 mm en cinco minutos
    están muy por debajo del récord mundial de esa duración."""
    epochs = [0, 300, 600]
    precips = [0.0, 12.0, 20.0]
    cleaned, jumps = sanitize_precip_series(epochs, precips)

    assert jumps == []
    assert cleaned == pytest.approx(precips)


def test_pakf_peak_is_not_impossible_only_absurd() -> None:
    """Documenta por qué hizo falta la segunda capa: el pico de PAKF (47,7 mm
    en cinco minutos) NO supera el récord mundial de esa duración, así que
    ninguna curva honesta lo descarta."""
    _cleaned, jumps = sanitize_precip_series([0, 300], [6.9, 54.6])
    assert jumps == []


def test_counter_reset_and_gaps_are_not_intensity() -> None:
    # Un descenso del acumulado (reinicio del contador) no es intensidad.
    cleaned, jumps = sanitize_precip_series([0, 300], [40.0, 0.0])
    assert jumps == []
    assert cleaned == pytest.approx([40.0, 0.0])

    # Dos partes con el mismo sello temporal no pueden dar una intensidad
    # infinita: el intervalo mínimo los descarta.
    _cleaned, jumps = sanitize_precip_series([0, 0], [0.0, 900.0])
    assert jumps == []

    # Un hueco largo reparte el acumulado: 300 mm en 3 h son normales.
    _cleaned, jumps = sanitize_precip_series([0, 10800], [0.0, 300.0])
    assert jumps == []

    # Los huecos de la serie (NaN) se conservan como huecos.
    cleaned, _jumps = sanitize_precip_series([0, 300, 600], [0.0, float("nan"), 2.0])
    assert math.isnan(cleaned[1])


def test_worst_jump_picks_the_one_that_overshoots_the_most() -> None:
    limit_5 = max_plausible_precip_mm(5.0)
    _cleaned, jumps = sanitize_precip_series(
        [0, 300, 600, 900], [0.0, limit_5 * 1.2, limit_5 * 1.2, limit_5 * 4.2],
    )
    assert len(jumps) == 2
    assert worst_jump(jumps).amount_mm == pytest.approx(limit_5 * 3.0)
    assert worst_jump([]) is None


# --------------------------------------------------------------------------
# Capa 2: el pluviómetro contradice al resto del parte
# --------------------------------------------------------------------------

def _metar_rows(count: int, raw: str) -> list:
    return [{"raw": raw, "wxcodes": None} for _ in range(count)]


def test_gauge_without_a_single_wet_report_is_flagged() -> None:
    """PAKF en una línea: acumula lluvia todo el día y ninguno de sus partes
    reporta precipitación."""
    rows = _metar_rows(
        79,
        "PAKF 061430Z AUTO 32008KT 10SM BKN022 12/08 A2960 RMK AO2 P0099 T01200080",
    )
    assert iem._precip_contradiction(rows, 255.3) == {
        "amount_mm": 255.3, "reports": 79,
    }


def test_any_evidence_of_rain_clears_the_gauge() -> None:
    """En cuanto algo corrobora la lluvia, el pluviómetro deja de ser
    sospechoso: el grupo de tiempo presente, los ``wxcodes`` de IEM, los
    chubascos de las inmediaciones o las marcas de inicio/fin del remark."""
    base = "PAKF 061430Z AUTO 32008KT 10SM {} BKN022 12/08 A2960 RMK AO2 P0099"
    for token in ("-RA", "+SHRA", "TSRA", "VCSH", "SN", "-FZDZ", "RASN", "UP"):
        rows = _metar_rows(79, base.format(token))
        assert iem._precip_contradiction(rows, 255.3) is None, token

    # Marca de comienzo/fin de lluvia en los remarks.
    rows = _metar_rows(79, "PAKF 061430Z AUTO 10SM 12/08 A2960 RMK AO2 RAB25E48 P0099")
    assert iem._precip_contradiction(rows, 255.3) is None

    # ``wxcodes`` de IEM, aunque el crudo no lo lleve.
    rows = _metar_rows(79, "PAKF 061430Z AUTO 10SM 12/08 A2960 RMK AO2 P0099")
    rows[3]["wxcodes"] = "RA"
    assert iem._precip_contradiction(rows, 255.3) is None


def test_stations_without_a_precipitation_discriminator_are_not_accused() -> None:
    """Una AO1 no lleva discriminador: no puede reportar lluvia aunque esté
    cayendo, así que su silencio no acusa a nadie."""
    rows = _metar_rows(79, "PAKF 061430Z AUTO 10SM 12/08 A2960 RMK AO1 P0099")
    assert iem._precip_contradiction(rows, 255.3) is None


def test_small_amounts_and_thin_days_are_not_judged() -> None:
    raw = "PAKF 061430Z AUTO 10SM 12/08 A2960 RMK AO2 P0099"
    # Un chubasco corto puede colarse entre dos partes horarios sin dejar
    # rastro en el tiempo presente.
    assert iem._precip_contradiction(_metar_rows(79, raw), 4.0) is None
    # Con cuatro lecturas sueltas no hay evidencia de que el silencio sea anómalo.
    assert iem._precip_contradiction(_metar_rows(4, raw), 255.3) is None
    # Y sin acumulado válido no hay nada que juzgar.
    assert iem._precip_contradiction(_metar_rows(79, raw), float("nan")) is None


# --------------------------------------------------------------------------
# Cuarentena, ranking y aviso
# --------------------------------------------------------------------------

def test_warnings_carry_what_was_measured() -> None:
    warning = observation_warnings.suspect_precipitation(121.4, 5.0)
    assert warning["code"] == observation_warnings.SUSPECT_PRECIPITATION
    assert warning["params"] == {"amount": 121.4, "minutes": 5}

    warning = observation_warnings.unreported_precipitation(255.3, 79)
    assert warning["code"] == observation_warnings.UNREPORTED_PRECIPITATION
    assert warning["params"] == {"amount": 255.3, "reports": 79}


def test_quarantine_is_per_station_variable_and_day() -> None:
    suspect_data.flag(
        "IEM", "AK_ASOS|PAKF", "2026-09-06", suspect_data.PRECIPITATION,
        params={"amount_mm": 255.3, "reports": 79},
    )
    assert suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-06", "rain")
    # Otro día de la misma estación, otra estación y otra variable siguen limpios.
    assert not suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-05", "rain")
    assert not suspect_data.is_flagged("IEM", "AK_ASOS|PANC", "2026-09-06", "rain")
    assert not suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-06", "gust")
    assert suspect_data.flags_for("IEM", "AK_ASOS|PAKF", "2026-09-06") == {
        "rain": {"amount_mm": 255.3, "reports": 79}
    }


def test_quarantine_drops_only_rain_from_the_ranking() -> None:
    """La estación sigue clasificando por temperatura y racha: solo cae la
    variable sospechosa."""
    rec = ranking.StationDaily(
        provider="IEM",
        station_id="AK_ASOS|PAKF",
        name="False Pass Airport",
        locality="AK",
        lat=54.8474,
        lon=-163.4103,
        tmax=12.0,
        tmin=10.0,
        gust=33.3,
        rain=255.3,
        rain_24h=255.3,
        rain_24h_at=1788703800,
        local_date="2026-09-06",
    )
    suspect_data.flag("IEM", "AK_ASOS|PAKF", "2026-09-06", suspect_data.PRECIPITATION)
    ranking._drop_quarantined_variables(rec)

    assert rec.rain is None
    assert rec.rain_24h is None
    assert rec.rain_24h_at is None
    assert rec.tmax == pytest.approx(12.0)
    assert rec.gust == pytest.approx(33.3)


def test_ranking_keeps_rain_of_stations_not_in_quarantine() -> None:
    rec = ranking.StationDaily(
        provider="IEM", station_id="AK_ASOS|PANC", name="Anchorage", locality="AK",
        lat=61.17, lon=-150.0, rain=6.4, local_date="2026-09-06",
    )
    ranking._drop_quarantined_variables(rec)
    assert rec.rain == pytest.approx(6.4)

    # Sin fecha local no hay clave de cuarentena: no se toca nada.
    undated = ranking.StationDaily(
        provider="IEM", station_id="AK_ASOS|PAKF", name="False Pass", locality="AK",
        lat=54.8, lon=-163.4, rain=255.3,
    )
    suspect_data.flag("IEM", "AK_ASOS|PAKF", "2026-09-06", suspect_data.PRECIPITATION)
    ranking._drop_quarantined_variables(undated)
    assert undated.rain == pytest.approx(255.3)


def test_router_flags_the_local_day_of_the_station_not_the_utc_one() -> None:
    """La cuarentena es del DÍA LOCAL de la estación: PAKF va ocho horas por
    detrás de UTC, así que un salto de las 06:15 UTC pertenece al día anterior
    en Alaska y el aviso tiene que salir ese día, no el siguiente."""
    from server.routers.observations import _quarantine_suspect_precipitation

    # 2026-09-06 06:15Z → 2026-09-05 22:15 en America/Nome.
    epoch = 1788675300
    limit = max_plausible_precip_mm(5.0)
    series = {
        "epochs": [epoch - 300, epoch, epoch + 300],
        "precips": [1.0, 1.0 + limit * 2, 3.0 + limit * 2],
    }
    warnings = _quarantine_suspect_precipitation(
        "IEM", "AK_ASOS|PAKF", series, tz_name="America/Nome",
    )

    assert [w["code"] for w in warnings] == [observation_warnings.SUSPECT_PRECIPITATION]
    assert series["precips"] == pytest.approx([1.0, 1.0, 3.0])
    assert suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-05", "rain")
    assert not suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-06", "rain")


def test_router_quarantines_on_the_contradiction_alone() -> None:
    """La segunda capa marca sin recortar nada: no sabemos qué parte del día
    es falso, lo es entero."""
    from server.routers.observations import _quarantine_suspect_precipitation

    epoch = 1788703800  # 2026-09-06 14:10Z → 06:10 en America/Nome
    series = {
        "epochs": [epoch - 300, epoch],
        "precips": [250.0, 255.3],
        "precip_contradiction": {"amount_mm": 255.3, "reports": 79},
    }
    warnings = _quarantine_suspect_precipitation(
        "IEM", "AK_ASOS|PAKF", series, tz_name="America/Nome",
    )

    assert [w["code"] for w in warnings] == [observation_warnings.UNREPORTED_PRECIPITATION]
    assert warnings[0]["params"] == {"amount": 255.3, "reports": 79}
    assert series["precips"] == pytest.approx([250.0, 255.3])
    assert suspect_data.is_flagged("IEM", "AK_ASOS|PAKF", "2026-09-06", "rain")


def test_router_leaves_a_healthy_gauge_untouched() -> None:
    from server.routers.observations import _quarantine_suspect_precipitation

    series = {"epochs": [0, 300, 600], "precips": [0.0, 12.0, 20.0]}
    assert _quarantine_suspect_precipitation("WU", "IBARCE1", series, tz_name="") == []
    assert series["precips"] == pytest.approx([0.0, 12.0, 20.0])
    assert suspect_data.flags_for("WU", "IBARCE1", "1970-01-01") == {}

    # Sin serie de lluvia no hay nada que juzgar.
    assert _quarantine_suspect_precipitation("WU", "IBARCE1", {}, tz_name="") == []

"""Estaciones apartadas del ranking a mano.

Buttigliera d'Asti llevaba días encabezando las máximas de Italia con 43,1 °C
frente a los 29,6 de la segunda, y 55 °C el día anterior. No la caza ningún
control automático: ``is_climatologically_impossible`` solo mira el suelo de
frío y la amplitud diurna se queda por debajo del umbral de 40 °C.
"""
from server.services.ranking import (
    RANKING_MANUAL_EXCLUSIONS,
    StationDaily,
    _drop_manually_excluded,
    _is_manually_excluded,
)

APARTADA = ("METEOHUB_IT", "dpcn-piemonte|45.02083|7.93389|buttigliera d'asti")


def _registro(day: str, **campos) -> StationDaily:
    base = dict(
        provider=APARTADA[0], station_id=APARTADA[1], name="Buttigliera d'Asti",
        local_date=day, tmax=43.1, tmin=15.0, rain=2.0, gust=30.0,
    )
    base.update(campos)
    return StationDaily(**base)


def test_the_excluded_station_stops_ranking():
    rec = _registro("2026-09-07")
    assert _is_manually_excluded(rec)
    _drop_manually_excluded(rec)
    assert rec.tmax is None and rec.tmin is None
    # Se vacía entera: si el aparato miente en una variable, no hay razón para
    # fiarse del resto.
    assert rec.rain is None and rec.gust is None


def test_the_exclusion_expires_on_its_own():
    """Sin caducidad la lista se vuelve permanente y nadie la revisa."""
    hasta = RANKING_MANUAL_EXCLUSIONS[APARTADA]
    assert _is_manually_excluded(_registro("2026-09-14"))
    assert not _is_manually_excluded(_registro(hasta))
    assert not _is_manually_excluded(_registro("2026-10-01"))


def test_nobody_else_is_touched():
    vecina = _registro("2026-09-07", station_id="dpcn-piemonte|45.0|7.9|otra")
    assert not _is_manually_excluded(vecina)
    _drop_manually_excluded(vecina)
    assert vecina.tmax == 43.1


def test_a_record_without_a_local_day_stays_excluded():
    """Sin fecha no se puede comprobar la caducidad, y se aparta igual.

    En el flujo real nunca pasa —el store asigna el día antes de filtrar—,
    pero si pasara, dejar entrar a una estación que sabemos rota es peor que
    apartarla de más."""
    assert _is_manually_excluded(_registro(""))


def test_every_exclusion_carries_an_expiry_date():
    for clave, hasta in RANKING_MANUAL_EXCLUSIONS.items():
        assert isinstance(hasta, str) and len(hasta) == 10, clave

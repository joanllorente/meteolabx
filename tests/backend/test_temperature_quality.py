"""Controles de plausibilidad del termómetro y cuarentena de la temperatura.

Los cuatro casos salieron de estaciones reales de IEM el mismo día, y cada uno
rompe el criterio que valía para el anterior.
"""

from __future__ import annotations

import pytest

from domain import observation_warnings
from domain.temperature_quality import (
    ANTARCTIC_FLOOR_C,
    MAX_DIURNAL_RANGE_C,
    cold_floor_c,
    flatlined_fields,
    is_climatologically_impossible,
    is_diurnal_range_impossible,
)
from server.services import ranking, suspect_data


@pytest.fixture(autouse=True)
def _limpio():
    suspect_data.clear()
    yield
    suspect_data.clear()


SEPTIEMBRE = 9
ENERO = 1


def test_the_floor_follows_latitude_and_season() -> None:
    """Un suelo global borraría a Concordia, que baja de −84 °C de verdad. Es
    el error que este proyecto ya cometió una vez."""
    # En la misma latitud, el invierno admite mucho más frío que el verano.
    assert cold_floor_c(45.0, ENERO) < cold_floor_c(45.0, SEPTIEMBRE)
    # Cuanto más al norte, más frío se admite.
    assert cold_floor_c(67.0, SEPTIEMBRE) < cold_floor_c(42.0, SEPTIEMBRE)
    # La Antártida juega aparte, en cualquier mes.
    assert cold_floor_c(-75.1, SEPTIEMBRE) == ANTARCTIC_FLOOR_C
    assert cold_floor_c(-75.1, ENERO) == ANTARCTIC_FLOOR_C
    # Sin latitud o sin mes no se juzga.
    assert cold_floor_c(None, SEPTIEMBRE) == ANTARCTIC_FLOOR_C
    assert cold_floor_c(45.0, None) == ANTARCTIC_FLOOR_C


def test_impossible_cold_is_caught_and_real_cold_survives() -> None:
    # Squaw Valley (California, 39 °N) publicando −73,3 °C en septiembre.
    assert is_climatologically_impossible(-73.3, 39.2, SEPTIEMBRE)
    # Ilirnej (Siberia, 67 °N) con −6,1 °C en septiembre: normal.
    assert not is_climatologically_impossible(-6.1, 67.25, SEPTIEMBRE)
    # Amundsen-Scott con −56 °C y Concordia con −84: récords reales.
    assert not is_climatologically_impossible(-56.0, -90.0, SEPTIEMBRE)
    assert not is_climatologically_impossible(-84.0, -75.1, SEPTIEMBRE)
    # Oymyakon en pleno invierno siberiano.
    assert not is_climatologically_impossible(-67.0, 63.5, ENERO)


def test_the_diurnal_range_matches_the_ranking() -> None:
    """Mismo umbral que ``ranking._MAX_DIURNAL_RANGE_C``: que la ficha y el
    ranking discrepen sobre si un dato vale ya causó bastante confusión."""
    assert MAX_DIURNAL_RANGE_C == ranking._MAX_DIURNAL_RANGE_C
    # Gettysburg: 23,0 de máxima y −22,0 de mínima el mismo septiembre.
    assert is_diurnal_range_impossible(23.0, -22.0)
    # Un día continental muy amplio sigue valiendo.
    assert not is_diurnal_range_impossible(40.0, 5.0)
    # Sin uno de los dos no hay nada que comparar.
    assert not is_diurnal_range_impossible(23.0, None)


def test_a_frozen_series_is_detected_even_without_being_identical() -> None:
    """Skriveri (Letonia) llevaba 24 h entre 0,0 y 0,2 °C mientras su presión
    subía 14 hPa de forma coherente. El control que existía exigía valores
    EXACTAMENTE iguales, así que no lo veía."""
    horas = [i * 3600 for i in range(24)]
    skriveri = [0.0, 0.1, 0.1, 0.1, 0.2, 0.1] * 4
    assert flatlined_fields(horas, {"temps": skriveri}) == ["temps"]

    # Un día normal no se toca.
    normal = [7.0 + i * 0.4 for i in range(24)]
    assert flatlined_fields(horas, {"temps": normal}) == []

    # Con pocas muestras o poco arco temporal no se juzga.
    assert flatlined_fields(horas[:6], {"temps": skriveri[:6]}) == []
    cortas = [i * 60 for i in range(24)]  # 24 minutos
    assert flatlined_fields(cortas, {"temps": skriveri}) == []

    # La tolerancia es por campo. Con la de temperatura (0,5 °C) una humedad
    # que se mueve tres décimas cuenta como congelada; con una tolerancia
    # estricta, no. Por eso el detector se aplica hoy solo a `temps`: una
    # humedad clavada en 100 % durante horas es niebla, no una avería, y
    # marcarla exigiría una tolerancia propia pensada para ella.
    casi_quieta = [90.0, 90.1, 90.2, 90.1] * 6
    assert flatlined_fields(horas, {"humidities": casi_quieta}) == ["humidities"]
    assert flatlined_fields(
        horas, {"humidities": casi_quieta}, span_by_field={"humidities": 0.001},
    ) == []


def test_the_warning_says_which_fault_it_is() -> None:
    for razon in ("frozen", "impossible", "range"):
        aviso = observation_warnings.suspect_temperature(razon)
        assert aviso["code"] == observation_warnings.SUSPECT_TEMPERATURE
        assert aviso["params"] == {"reason": razon}


def test_quarantine_drops_the_three_temperature_fields() -> None:
    """Un termómetro roto no lo está solo para la máxima: los tres campos
    salen del mismo sensor. El viento y la lluvia siguen clasificando."""
    rec = ranking.StationDaily(
        provider="IEM", station_id="SD_ASOS|0D8", name="Gettysburg", locality="SD",
        lat=44.9866, lon=-99.9528, tmax=23.0, tmin=-22.0, tcur=-21.0,
        tcur_at=1788000000, gust=37.0, rain=0.3, local_date="2026-09-06",
    )
    suspect_data.flag("IEM", "SD_ASOS|0D8", "2026-09-06", suspect_data.TEMPERATURE)
    ranking._drop_quarantined_variables(rec)

    assert rec.tmax is None and rec.tmin is None and rec.tcur is None
    assert rec.tcur_at is None
    assert rec.gust == pytest.approx(37.0)
    assert rec.rain == pytest.approx(0.3)


def test_temperature_and_rain_quarantines_are_independent() -> None:
    rec = ranking.StationDaily(
        provider="IEM", station_id="X|Y", name="X", locality="",
        lat=40.0, lon=0.0, tmax=20.0, tmin=10.0, tcur=15.0, rain=5.0,
        local_date="2026-09-06",
    )
    suspect_data.flag("IEM", "X|Y", "2026-09-06", suspect_data.PRECIPITATION)
    ranking._drop_quarantined_variables(rec)
    assert rec.rain is None
    assert rec.tmax == pytest.approx(20.0)  # la temperatura no se toca


# =====================================================================
# El ranking juzga por su cuenta, y la cuarentena lleva historial
# =====================================================================

def test_the_ranking_flags_without_anyone_opening_the_card() -> None:
    """Antes la cuarentena solo se levantaba al abrir la ficha: una estación
    que nadie visita no se marcaba nunca, y cada medianoche local volvía a
    estar limpia. El bulk ve todas las estaciones en cada ciclo."""
    gettysburg = ranking.StationDaily(
        provider="IEM", station_id="SD_ASOS|0D8", name="Gettysburg", locality="SD",
        lat=44.9866, lon=-99.9528, tmax=23.0, tmin=-22.0, tcur=-21.0,
        gust=37.0, local_date="2026-09-06",
    )
    squaw = ranking.StationDaily(
        provider="IEM", station_id="CA_DCP|SQBC1", name="Squaw Valley", locality="CA",
        lat=39.2, lon=-120.23, tmax=-73.3, tmin=-73.3, tcur=-73.3,
        local_date="2026-09-06",
    )
    ilirnej = ranking.StationDaily(
        provider="IEM", station_id="WMO_BUFR_SRF|0-643-0-248", name="Ilirnej",
        locality="", lat=67.25, lon=167.97, tmax=-6.1, tmin=-7.8, tcur=-6.1,
        local_date="2026-09-06",
    )
    assert ranking._flag_suspect_temperature(gettysburg) == "range"
    assert ranking._flag_suspect_temperature(squaw) == "impossible"
    assert ranking._flag_suspect_temperature(ilirnej) is None

    # La racha de Gettysburg sobrevive: solo cae la variable acusada.
    ranking._drop_quarantined_variables(gettysburg)
    assert gettysburg.tmax is None and gettysburg.tmin is None
    assert gettysburg.gust == pytest.approx(37.0)


def test_the_history_tells_a_bad_afternoon_from_a_broken_sensor() -> None:
    """``days_total`` es lo que distingue el fallo puntual del crónico."""
    dia = 24 * 3600
    for jornada in range(5):
        suspect_data.flag(
            "IEM", "CA_DCP|SQBC1", f"2026-09-0{jornada + 1}",
            suspect_data.TEMPERATURE, params={"reason": "impossible"},
            now=1_000_000_000 + jornada * dia,
        )
    suspect_data.flag(
        "IEM", "SD_ASOS|0D8", "2026-09-05", suspect_data.TEMPERATURE,
        params={"reason": "range"}, now=1_000_000_000 + 4 * dia,
    )

    historial = {fila["station_id"]: fila for fila in suspect_data.history()}
    assert historial["CA_DCP|SQBC1"]["days_total"] == 5   # crónica
    assert historial["SD_ASOS|0D8"]["days_total"] == 1    # una tarde
    # Ordenado de más crónica a menos, que es como se lee el panel.
    assert suspect_data.history()[0]["station_id"] == "CA_DCP|SQBC1"

    activas = suspect_data.active("2026-09-05")
    assert {fila["station_id"] for fila in activas} == {"CA_DCP|SQBC1", "SD_ASOS|0D8"}
    assert activas[0]["days_total"] == 5


def test_the_quarantine_survives_a_restart() -> None:
    """Sin persistencia, un redespliegue diario dejaba el historial a cero y
    nunca se veía una estación crónica."""
    import json

    suspect_data.flag(
        "IEM", "CA_DCP|SQBC1", "2026-09-06", suspect_data.TEMPERATURE,
        params={"reason": "impossible"}, now=1_000_000_000,
    )
    exportado = json.loads(json.dumps(suspect_data.export_state()))
    suspect_data.clear()
    assert suspect_data.history() == []

    suspect_data.import_state(exportado)
    assert suspect_data.is_flagged("IEM", "CA_DCP|SQBC1", "2026-09-06", "temperature")
    assert suspect_data.history()[0]["days_total"] == 1
    # Un estado ilegible no revienta el arranque.
    assert suspect_data.import_state("basura") == 0

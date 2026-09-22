"""Pluviómetros manuales de MeteoSwiss: la ficha enseña su última lluvia.

Lenzerheide (LEH) es una de las 188 estaciones manuales de MeteoSwiss: un
observador mide la lluvia una vez al día y se publica dos días después. Su
ficha pedía una lectura actual que no existe, salía en blanco y el panel lo
contaba como error. Ahora la ficha de la estación dice que no es de tiempo
real, y un endpoint da la última lluvia diaria publicada.
"""

from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.services import meteoswiss_climo, stations

PRECIP = meteoswiss_climo.FIELDS.index("precip_total")


def _bloque(filas):
    """(días ordinales, valores) con solo la lluvia rellena."""
    dias = np.asarray([dia.toordinal() for dia, _ in filas], dtype=np.int32)
    valores = np.full((len(filas), len(meteoswiss_climo.FIELDS)), np.nan, dtype=np.float32)
    for indice, (_, lluvia) in enumerate(filas):
        valores[indice, PRECIP] = np.nan if lluvia is None else lluvia
    return dias, valores


@pytest.fixture
def ficheros(monkeypatch):
    """Sustituye la descarga de ``d_recent``/``d_historical``."""
    contenido = {"recent": _bloque([]), "historical": _bloque([])}

    async def _falso(_client, _collection, _code, span, *, today):
        return contenido[span]

    monkeypatch.setattr(meteoswiss_climo, "_block", _falso)
    return contenido


def test_the_station_card_says_it_has_no_current_reading() -> None:
    assert stations.get_station("METEOSWISS", "LEH")["realtime"] is False
    assert stations.get_station("METEOSWISS", "SMA")["realtime"] is True


def test_the_latest_published_day_wins_and_gaps_are_skipped(ficheros) -> None:
    ficheros["recent"] = _bloque([
        (date(2026, 9, 16), 9.7),
        (date(2026, 9, 19), 0.0),
        (date(2026, 9, 20), None),  # fila sin lluvia: aún no medida
    ])
    ultima = asyncio.run(meteoswiss_climo.latest_daily_precip(None, "LEH"))
    # Un cero publicado es un dato, no un hueco.
    assert ultima == (date(2026, 9, 19), 0.0)


def test_early_january_falls_back_to_the_historical_file(ficheros) -> None:
    ficheros["historical"] = _bloque([(date(2025, 12, 31), 3.4)])
    assert asyncio.run(meteoswiss_climo.latest_daily_precip(None, "LEH")) == (
        date(2025, 12, 31), pytest.approx(3.4),
    )


def test_the_endpoint_gives_the_six_to_six_utc_window(ficheros) -> None:
    ficheros["recent"] = _bloque([(date(2026, 9, 19), 0.0)])
    with TestClient(app) as cliente:
        respuesta = cliente.get("/v1/observations/daily/latest?provider=METEOSWISS&station_id=leh")
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "provider": "METEOSWISS",
        "station_id": "LEH",
        "day": "2026-09-19",
        "window_start_utc": "2026-09-19T06:00:00+00:00",
        "window_end_utc": "2026-09-20T06:00:00+00:00",
        "precip_mm": 0.0,
    }


def test_without_published_rain_it_is_a_404(ficheros) -> None:
    with TestClient(app) as cliente:
        vacia = cliente.get("/v1/observations/daily/latest?provider=METEOSWISS&station_id=LEH")
        otra_red = cliente.get("/v1/observations/daily/latest?provider=AEMET&station_id=0016A")
    assert vacia.status_code == 404
    assert otra_red.status_code == 404

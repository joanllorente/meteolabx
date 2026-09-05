"""La serie local de AEMET no trae viento; OpenData sí, y se completa.

Tres estaciones (aeropuerto de Barcelona, Lleida y el Observatori de l'Ebre)
se sirven de una serie histórica local que llega a 1950 pero solo publica
precipitación, máxima, mínima e insolación. Sin completar, esas estaciones
salían sin viento aunque AEMET lo publique.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from domain.parsing.aemet_climo import complete_daily_frame
from server.services.aemet_climo import _legacy_periods_to_complete


def _day(day: str, **values):
    row = {
        "date": day,
        "epoch": float("nan"),
        "temp_mean": float("nan"),
        "temp_max": float("nan"),
        "temp_min": float("nan"),
        "wind_mean": float("nan"),
        "wind_dir_mean": float("nan"),
        "gust_max": float("nan"),
        "gust_dir_max": float("nan"),
        "precip_total": float("nan"),
    }
    row.update(values)
    return row


def test_el_viento_de_opendata_rellena_la_serie_local():
    local = pd.DataFrame([_day("2025-08-01", temp_max=29.0, precip_total=0.0)])
    opendata = pd.DataFrame([_day("2025-08-01", temp_max=29.1, wind_mean=19.1, gust_max=41.0, gust_dir_max=210.0)])

    merged = complete_daily_frame(opendata, local)
    row = merged.iloc[0]
    assert row["wind_mean"] == 19.1
    assert row["gust_dir_max"] == 210.0
    # Donde las dos fuentes hablan manda OpenData, que es la que mide hoy.
    assert row["temp_max"] == 29.1
    # Y lo que solo tiene la serie local no se pierde.
    assert row["precip_total"] == 0.0


def test_los_dias_que_opendata_no_publica_los_sostiene_la_serie_local():
    # 1950 está muy por debajo de donde llega OpenData.
    local = pd.DataFrame([_day("1950-01-01", temp_max=10.6, temp_min=8.7)])
    merged = complete_daily_frame(pd.DataFrame(), local)
    assert len(merged) == 1
    assert merged.iloc[0]["temp_max"] == 10.6


def test_no_se_le_piden_a_opendata_anos_a_los_que_no_llega():
    # Pedir 1950 a OpenData es una ronda de peticiones para nada.
    assert _legacy_periods_to_complete("0076", [(date(1950, 1, 1), date(1950, 12, 31))]) == []


def test_si_se_le_piden_los_anos_que_si_cubre():
    tramos = _legacy_periods_to_complete("0076", [(date(2025, 8, 1), date(2025, 8, 31))])
    assert tramos == [(date(2025, 8, 1), date(2025, 8, 31))]


def test_una_estacion_sin_serie_local_no_tiene_nada_que_completar():
    assert _legacy_periods_to_complete("0201X", [(date(2025, 8, 1), date(2025, 8, 31))]) == []

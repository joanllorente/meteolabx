"""La serie de viento del histórico sale de lo que publica cada red.

Unas dan media y racha con su veleta, otras solo el rumbo de la racha y otras
no miden viento. Aquí se comprueba que la tabla se adapta a lo que llega en
vez de dar por hecho lo que debería llegar.
"""

from __future__ import annotations

import math

import pandas as pd

from domain.climograms import build_wind_chart_table


def _frame(**columns):
    base = {"date": pd.date_range("2026-08-01", periods=4)}
    base.update(columns)
    return pd.DataFrame(base)


def test_el_rumbo_es_el_predominante_y_no_la_media_de_los_grados():
    # Días del norte: 350°, 10°, 5° y 355°. Promediar los grados daría 180°
    # —justo el rumbo contrario—; el predominante es el norte.
    frame = _frame(
        wind_mean=[6.0, 7.0, 5.0, 8.0],
        wind_dir_mean=[350.0, 10.0, 5.0, 355.0],
        gust_max=[30.0, 41.0, 22.0, 44.0],
    )
    row = build_wind_chart_table(frame, "monthly").iloc[0]
    assert row["dir_kind"] == "mean"
    assert min(row["dir_deg"], 360 - row["dir_deg"]) < 10


def test_sin_veleta_de_viento_medio_se_usa_la_de_la_racha():
    # AEMET, Meteo-France o ECCC publican de dónde vino el golpe, no el rumbo
    # medio del día. Es menos, pero es dato.
    frame = _frame(
        wind_mean=[6.0, 7.0, 5.0, 8.0],
        gust_max=[30.0, 41.0, 22.0, 44.0],
        gust_dir_max=[200.0, 210.0, 205.0, 195.0],
    )
    row = build_wind_chart_table(frame, "monthly").iloc[0]
    assert row["dir_kind"] == "gust"
    assert 190 < row["dir_deg"] < 215


def test_una_red_que_solo_publica_racha_no_inventa_la_media():
    frame = _frame(gust_max=[30.0, 41.0, 22.0, 44.0])
    row = build_wind_chart_table(frame, "monthly").iloc[0]
    assert math.isnan(row["wind_mean"])
    assert row["gust_max"] == 44.0
    assert row["dir_kind"] == ""
    assert math.isnan(row["dir_deg"])


def test_sin_viento_no_hay_serie_que_dibujar():
    frame = build_wind_chart_table(_frame(), "monthly")
    assert math.isnan(frame.iloc[0]["wind_mean"])
    assert math.isnan(frame.iloc[0]["gust_max"])


def test_la_calma_no_vota_el_rumbo():
    # Con viento flojo la veleta gira sola: tres días de calma apuntando al
    # este no pueden ganarle al único día con viento de verdad.
    frame = _frame(
        wind_mean=[0.5, 0.4, 0.6, 25.0],
        wind_dir_mean=[90.0, 95.0, 85.0, 270.0],
        gust_max=[3.0, 3.0, 3.0, 60.0],
    )
    row = build_wind_chart_table(frame, "monthly").iloc[0]
    assert 260 < row["dir_deg"] < 280


def test_cada_ano_es_un_punto_en_la_vista_anual():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-03-01", "2024-08-01", "2025-03-01", "2025-08-01"]),
            "wind_mean": [5.0, 7.0, 9.0, 11.0],
            "gust_max": [30.0, 40.0, 50.0, 60.0],
            "wind_dir_mean": [10.0, 20.0, 200.0, 210.0],
        }
    )
    table = build_wind_chart_table(frame, "yearly")
    assert list(table["label"]) == ["2024", "2025"]
    assert list(table["gust_max"]) == [40.0, 60.0]
    assert table.iloc[0]["wind_mean"] == 6.0

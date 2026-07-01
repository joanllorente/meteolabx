import math

from domain.parsing.aemet_climo import _aemet_daily_record_to_row
from domain.parsing.wu_climo import normalize_wu_daily_payload


def test_wu_daily_history_keeps_mean_wind_direction():
    frame = normalize_wu_daily_payload(
        {
            "observations": [
                {
                    "obsTimeLocal": "2026-05-20 23:59:59",
                    "epoch": 1779314399,
                    "metric": {
                        "tempAvg": 20.0,
                        "tempHigh": 24.0,
                        "tempLow": 16.0,
                        "windspeedAvg": 12.0,
                        "winddirAvg": "SW",
                        "windgustHigh": 30.0,
                        "precipTotal": 1.2,
                    },
                }
            ]
        }
    )

    assert "wind_dir_mean" in frame.columns
    assert frame.loc[0, "wind_dir_mean"] == 225.0


def test_aemet_daily_history_keeps_wind_direction_if_present():
    row = _aemet_daily_record_to_row(
        {
            "fecha": "2026-05-20",
            "tmed": "20,0",
            "tmax": "24,0",
            "tmin": "16,0",
            "velmedia": "3,0",
            "dir": "SO",
            "racha": "9,0",
            "prec": "0,0",
        }
    )

    assert row is not None
    assert math.isclose(row["wind_mean"], 10.8)
    assert row["wind_dir_mean"] == 225.0

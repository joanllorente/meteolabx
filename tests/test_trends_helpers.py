from datetime import datetime
import math

from models.thermodynamics import e_s
from tabs import trends


def test_prepare_today_dataset_from_series_groups_and_sets_intervals():
    now_local = datetime(2026, 4, 21, 12, 0, 0)
    payload = {
        "epochs": [1713686400, 1713687000, 1713687600],
        "temps": [10.0, 11.0, 12.0],
        "humidities": [60.0, 61.0, 62.0],
        "pressures_abs": [1000.0, 1001.0, 1002.0],
    }

    prepared = trends._prepare_today_dataset_from_series(
        payload,
        pressure_key="pressures_abs",
        now_local=now_local,
        infer_series_step_minutes=lambda dt: 10,
        min_source_step=5,
    )

    assert prepared is not None
    assert prepared["trend_grid_step_min"] == 20
    assert prepared["interval_theta_e"] == 20
    assert prepared["interval_e"] == 20
    assert prepared["interval_p"] == 180


def test_load_synoptic_trends_source_recovers_humidity_from_dewpoint(fake_logger):
    hourly7d = {
        "has_data": True,
        "epochs": [1713686400, 1713697200, 1713708000],
        "temps": [20.0, 21.0, 22.0],
        "humidities": [float("nan"), float("nan"), float("nan")],
        "dewpts": [10.0, 11.0, 12.0],
        "pressures": [1015.0, 1016.0, 1017.0],
    }

    prepared = trends._load_synoptic_trends_source(
        provider_id="AEMET",
        hourly7d=hourly7d,
        infer_series_step_minutes=lambda dt: 180,
        render_neutral_info_note=lambda *args, **kwargs: None,
        t=lambda key, **kwargs: key,
        logger=fake_logger,
        is_nan=math.isnan,
        e_s=e_s,
        station_elevation=0,
    )

    assert prepared is not None
    assert prepared["interval_theta_e"] == 180
    assert prepared["interval_e"] == 180
    assert prepared["interval_p"] == 180
    assert prepared["df_trends"]["rh"].notna().all()

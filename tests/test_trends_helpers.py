from datetime import datetime, timedelta, timezone
import inspect
import json
import math
from pathlib import Path

from models.thermodynamics import e_s
from tabs import trends
from utils.trends_pipeline import extend_today_pressure_trend


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


def test_wind_uv_frame_aligns_to_today_trend_grid():
    times = [
        datetime(2026, 6, 8, 12, 53, tzinfo=timezone.utc),
        datetime(2026, 6, 8, 13, 13, tzinfo=timezone.utc),
        datetime(2026, 6, 8, 13, 33, tzinfo=timezone.utc),
    ]
    frame = trends._prepare_wind_uv_frame(
        [int(dt.timestamp()) for dt in times],
        [10.0, 12.0, 14.0],
        [180.0, 190.0, 200.0],
        grid_step_minutes=20,
        tz_name="",
        is_nan=math.isnan,
    )

    assert frame["dt"].dt.minute.tolist() == [40, 0, 20]
    assert frame["u"].notna().all()
    assert frame["v"].notna().all()


def test_wind_uv_frame_uses_station_timezone():
    epoch = int(datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc).timestamp())

    frame = trends._prepare_wind_uv_frame(
        [epoch, epoch + 1200, epoch + 2400],
        [10.0, 10.0, 10.0],
        [180.0, 180.0, 180.0],
        grid_step_minutes=20,
        tz_name="America/Denver",
        is_nan=math.isnan,
    )

    assert frame["dt"].iloc[0].hour == 14


def test_symmetric_y_range_uses_minimum_until_data_exceeds_it():
    assert trends._symmetric_y_range_with_min([1.0, -4.0, float("nan")], 20.0) == [-20.0, 20.0]
    assert trends._symmetric_y_range_with_min([30.0, -12.0], 20.0) == [-33.0, 33.0]


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


def test_load_synoptic_trends_source_flags_short_provider_coverage_and_reads_abs_pressure(fake_logger):
    notes = []

    def render_note(message, **kwargs):
        notes.append((message, kwargs))

    hourly7d = {
        "has_data": True,
        "epochs": [1713686400, 1713697200, 1713708000],
        "temps": [14.0, 15.0, 16.0],
        "humidities": [70.0, 71.0, 72.0],
        "pressures_abs": [1012.0, 1012.5, 1013.0],
    }

    prepared = trends._load_synoptic_trends_source(
        provider_id="METOFFICE",
        hourly7d=hourly7d,
        infer_series_step_minutes=lambda dt: 180,
        render_neutral_info_note=render_note,
        t=lambda key, **kwargs: key,
        logger=fake_logger,
        is_nan=math.isnan,
        e_s=e_s,
        station_elevation=0,
    )

    assert prepared is not None
    assert prepared["coverage_limited"] is True
    assert notes == [
        (
            "trends.notes.synoptic_insufficient_coverage",
            {"title": "trends.notes.provider_coverage_title"},
        )
    ]
    assert prepared["df_trends"]["p"].notna().all()


def test_load_synoptic_trends_source_limits_synoptic_window_to_latest_week(fake_logger):
    start = datetime(2026, 4, 12, 0, 0, 0)
    times = [start + timedelta(hours=3 * idx) for idx in range(14 * 8)]
    epochs = [int(dt.timestamp()) for dt in times]
    values = [float(idx) for idx in range(len(epochs))]

    prepared = trends._load_synoptic_trends_source(
        provider_id="POEM",
        hourly7d={
            "has_data": True,
            "epochs": epochs,
            "temps": values,
            "humidities": [70.0] * len(epochs),
            "pressures_abs": [1012.0] * len(epochs),
        },
        infer_series_step_minutes=lambda dt: 180,
        render_neutral_info_note=lambda *args, **kwargs: None,
        t=lambda key, **kwargs: key,
        logger=fake_logger,
        is_nan=math.isnan,
        e_s=e_s,
        station_elevation=0,
    )

    assert prepared is not None
    span_hours = (prepared["day_end"] - prepared["day_start"]).total_seconds() / 3600.0
    assert span_hours <= 7 * 24
    assert prepared["day_start"] >= prepared["day_end"] - timedelta(days=7)


def test_extend_today_pressure_trend_uses_only_wu_hourly_lookback():
    day_start = datetime(2026, 5, 29, 0, 0, 0)
    hourly_times = [day_start - timedelta(hours=3) + timedelta(hours=idx) for idx in range(8)]
    hourly_epochs = [int(dt.timestamp()) for dt in hourly_times]
    hourly_pressures = [1000.0 + idx for idx in range(len(hourly_times))]
    base_times = [day_start + timedelta(minutes=20 * idx) for idx in range(10)]
    base_pressures = [1003.0 + (idx / 3.0) for idx in range(len(base_times))]

    trend_times, trend_values = extend_today_pressure_trend(
        provider_id="WU",
        pressure_trend_times=[],
        pressure_trend_values=[],
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        get_provider_station_id=lambda provider_id: "",
        get_meteofrance_service=lambda: None,
        infer_series_step_minutes=lambda dt: 60,
        wu_hourly7d={
            "has_data": True,
            "epochs": hourly_epochs,
            "pressures": hourly_pressures,
        },
        base_pressure_times=base_times,
        base_pressure_values=base_pressures,
        station_elevation=0,
        is_nan=math.isnan,
    )

    assert trend_times.iloc[0].to_pydatetime().hour == 0
    assert trend_times.iloc[1].to_pydatetime().minute == 20
    assert not math.isnan(float(trend_values[0]))
    assert float(trend_values[0]) == 1.0


def test_trend_chart_heading_tooltip_escapes_html():
    html = trends._trend_chart_heading_html("<Theta>", "A < B & C")

    assert "&lt;Theta&gt;" in html
    assert "A &lt; B &amp; C" in html
    assert "<Theta>" not in html
    assert '<div class="trend-chart-help-tooltip">' in html


def test_trend_chart_tooltip_typography_matches_observation_cards():
    css_source = inspect.getsource(trends._inject_trend_chart_heading_css)

    assert "font-size: 0.74rem" in css_source
    assert "font-weight: 400" in css_source
    assert "line-height: 1.34" in css_source
    assert "font-weight: 600" not in css_source


def test_trend_chart_tooltip_locale_keys_exist_in_supported_languages():
    locales_dir = Path(__file__).resolve().parent.parent / "locales"
    for lang in ("es", "en", "fr"):
        payload = json.loads((locales_dir / f"{lang}.json").read_text(encoding="utf-8"))
        tooltips = payload["trends"]["tooltips"]
        assert tooltips["theta_e"].strip()
        assert tooltips["mixing_ratio"].strip()
        assert tooltips["uv"].strip()

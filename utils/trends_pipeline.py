"""
Pipeline común para preparar datasets de tendencias.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def _local_naive_datetime_from_epoch(epoch, tz_name: str = ""):
    tz_text = str(tz_name or "").strip()
    if tz_text:
        try:
            from zoneinfo import ZoneInfo

            return datetime.fromtimestamp(float(epoch), tz=ZoneInfo(tz_text)).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.fromtimestamp(float(epoch))


def build_trends_frame(epochs, temps, humidities, pressures, *, tz_name: str = ""):
    import pandas as pd

    epochs = list(epochs or [])
    row_count = len(epochs)
    if row_count == 0:
        return pd.DataFrame(columns=["dt", "temp", "rh", "p"])

    def _coerce_series(values):
        return pd.Series(list(values or [])[:row_count], dtype="float64").reindex(range(row_count))

    def _local_datetimes(values):
        rows = []
        for value in values[:row_count]:
            try:
                rows.append(_local_naive_datetime_from_epoch(float(value), tz_name))
            except (TypeError, ValueError, OSError, OverflowError):
                rows.append(pd.NaT)
        return rows

    frame = pd.DataFrame(
        {
            "dt": pd.to_datetime(_local_datetimes(epochs), errors="coerce"),
            "temp": _coerce_series(temps),
            "rh": _coerce_series(humidities),
            "p": _coerce_series(pressures),
        }
    )
    return frame.sort_values("dt")


def derive_theta_e_series(df_trends, equivalent_potential_temperature):
    import numpy as np
    import pandas as pd

    temps = pd.to_numeric(df_trends.get("temp"), errors="coerce").to_numpy(dtype=np.float64)
    humidities = pd.to_numeric(df_trends.get("rh"), errors="coerce").to_numpy(dtype=np.float64)
    pressures = pd.to_numeric(df_trends.get("theta_e_pressure", df_trends.get("p")), errors="coerce").to_numpy(dtype=np.float64)
    return np.asarray(
        [
            equivalent_potential_temperature(temp, humidity, pressure)
            if not (pd.isna(temp) or pd.isna(humidity) or pd.isna(pressure))
            else np.nan
            for temp, humidity, pressure in zip(temps, humidities, pressures)
        ],
        dtype=np.float64,
    )


def derive_mixing_ratio_series(df_trends, vapor_pressure, mixing_ratio):
    import numpy as np
    import pandas as pd

    temps = pd.to_numeric(df_trends.get("temp"), errors="coerce").to_numpy(dtype=np.float64)
    humidities = pd.to_numeric(df_trends.get("rh"), errors="coerce").to_numpy(dtype=np.float64)
    pressures = pd.to_numeric(df_trends.get("mixing_ratio_pressure", df_trends.get("p")), errors="coerce").to_numpy(dtype=np.float64)
    return np.asarray(
        [
            mixing_ratio(vapor_pressure(temp, humidity), pressure) * 1000.0
            if not (pd.isna(temp) or pd.isna(humidity) or pd.isna(pressure))
            else np.nan
            for temp, humidity, pressure in zip(temps, humidities, pressures)
        ],
        dtype=np.float64,
    )


def prepare_today_trends_dataset(df_trends, now_local, infer_series_step_minutes, *, min_source_step: int):
    import pandas as pd

    if df_trends.empty:
        return None

    raw_series_step_min = max(int(min_source_step), infer_series_step_minutes(pd.to_datetime(df_trends["dt"])))
    trend_grid_step_min = max(20, raw_series_step_min)
    df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor(f"{trend_grid_step_min}min")
    df_trends = df_trends.groupby("dt", as_index=False).last()

    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    grid = pd.date_range(
        start=day_start,
        end=day_end,
        freq=f"{trend_grid_step_min}min",
        inclusive="left",
    )
    series_step_min = max(trend_grid_step_min, infer_series_step_minutes(df_trends["dt"]))
    return {
        "df_trends": df_trends,
        "grid": grid,
        "day_start": day_start,
        "day_end": day_end,
        "series_step_min": series_step_min,
        "trend_grid_step_min": trend_grid_step_min,
    }


def prepare_today_dataset_from_series(
    series_payload,
    *,
    pressure_key,
    now_local,
    infer_series_step_minutes,
    min_source_step: int,
    tz_name: str = "",
):
    df_trends = build_trends_frame(
        series_payload.get("epochs", []),
        series_payload.get("temps", []),
        series_payload.get("humidities", []),
        series_payload.get(pressure_key, []),
        tz_name=tz_name,
    )
    if df_trends.empty:
        return None

    prepared = prepare_today_trends_dataset(
        df_trends,
        now_local,
        infer_series_step_minutes,
        min_source_step=min_source_step,
    )
    if not prepared:
        return None

    prepared["interval_theta_e"] = max(20, prepared["trend_grid_step_min"])
    prepared["interval_e"] = max(20, prepared["trend_grid_step_min"])
    prepared["interval_p"] = 180
    return prepared


def load_synoptic_trends_source(
    *,
    provider_id,
    hourly7d,
    infer_series_step_minutes,
    render_neutral_info_note,
    coverage_note_key,
    coverage_title,
    min_coverage_hours=6.0 * 24.0,
    logger,
    is_nan,
    e_s,
    station_elevation,
    tz_name: str = "",
):
    provider_id = str(provider_id or "").strip().upper()
    if not hourly7d.get("has_data", False):
        return {"warning_only": True}

    coverage_limited = False
    synoptic_span_h = None

    epochs_7d = hourly7d.get("epochs", [])
    temps_7d = hourly7d.get("temps", [])
    humidities_7d = hourly7d.get("humidities", [])
    dewpts_7d = hourly7d.get("dewpts", [])
    pressures_7d = hourly7d.get("pressures", [])
    if pressures_7d is None or len(pressures_7d) == 0:
        pressures_7d = hourly7d.get("pressures_abs", [])

    if provider_id == "WU":
        pressure_factor = math.exp(-float(station_elevation or 0) / 8000.0)
        pressures_7d = [
            p * pressure_factor if not is_nan(p) else float("nan")
            for p in pressures_7d
        ]

    if (len(humidities_7d) == 0 or all(is_nan(h) for h in humidities_7d)) and len(dewpts_7d) == len(temps_7d):
        logger.warning("⚠️  Serie sinóptica sin HR - usando fallback desde T y Td")
        humidities_7d = []
        for temp, td in zip(temps_7d, dewpts_7d):
            if is_nan(temp) or is_nan(td):
                humidities_7d.append(float("nan"))
            else:
                e_td = e_s(td)
                e_s_t = e_s(temp)
                humidities_7d.append(100.0 * e_td / e_s_t if e_s_t > 0 else float("nan"))

    df_trends = build_trends_frame(
        epochs_7d,
        temps_7d,
        humidities_7d,
        pressures_7d,
        tz_name=tz_name,
    )
    if df_trends.empty:
        logger.warning(f"Tendencia sinóptica {provider_id}: DataFrame vacío")
        return None

    import pandas as pd

    df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("3h")
    df_trends = df_trends.groupby("dt", as_index=False).last()
    max_dt = df_trends["dt"].max()
    min_dt = max_dt - pd.Timedelta(days=7)
    df_trends = df_trends[df_trends["dt"] >= min_dt]
    if df_trends.empty:
        logger.warning(f"Tendencia sinóptica {provider_id}: DataFrame vacío tras limitar a 7 días")
        return None

    synoptic_span_h = (df_trends["dt"].max() - df_trends["dt"].min()).total_seconds() / 3600.0
    if coverage_note_key and synoptic_span_h < float(min_coverage_hours):
        coverage_limited = True
        render_neutral_info_note(
            coverage_note_key,
            title=coverage_title,
        )

    series_step_min = max(180, infer_series_step_minutes(df_trends["dt"]))
    return {
        "df_trends": df_trends,
        "grid": pd.to_datetime(df_trends["dt"].values),
        "day_start": df_trends["dt"].min(),
        "day_end": df_trends["dt"].max(),
        "series_step_min": series_step_min,
        "interval_theta_e": max(180, series_step_min),
        "interval_e": max(180, series_step_min),
        "interval_p": 180,
        "coverage_limited": coverage_limited,
        "coverage_span_hours": synoptic_span_h,
        "min_coverage_hours": float(min_coverage_hours),
    }


def extend_today_pressure_trend(
    *,
    provider_id,
    pressure_trend_times,
    pressure_trend_values,
    day_start,
    day_end,
    get_provider_station_id,
    get_meteofrance_service,
    infer_series_step_minutes,
    wu_hourly7d=None,
    base_pressure_times=None,
    base_pressure_values=None,
    station_elevation=0,
    is_nan=None,
):
    import numpy as np
    import pandas as pd
    from models.trends import calculate_trend

    def _value_is_nan(value) -> bool:
        if callable(is_nan):
            try:
                return bool(is_nan(value))
            except Exception:
                pass
        try:
            return math.isnan(float(value))
        except (TypeError, ValueError):
            return True

    def _pressure_frame_from_epochs(ext_epochs, ext_pressures, *, convert_msl_to_abs=False):
        if len(ext_epochs) != len(ext_pressures) or not ext_epochs:
            return None
        pressure_factor = 1.0
        if convert_msl_to_abs:
            try:
                pressure_factor = math.exp(-float(station_elevation or 0) / 8000.0)
            except (TypeError, ValueError):
                pressure_factor = 1.0

        frame = pd.DataFrame({
            "dt": [datetime.fromtimestamp(ep) for ep in ext_epochs],
            "p": [
                float("nan") if _value_is_nan(value) else float(value) * pressure_factor
                for value in ext_pressures
            ],
        }).sort_values("dt")
        frame["dt"] = pd.to_datetime(frame["dt"], errors="coerce")
        return frame.dropna(subset=["dt"])

    def _pressure_frame_from_times(times, pressures):
        times = [] if times is None else list(times)
        pressures = [] if pressures is None else list(pressures)
        row_count = min(len(times), len(pressures))
        if row_count <= 0:
            return None
        frame = pd.DataFrame({
            "dt": pd.to_datetime(times[:row_count], errors="coerce"),
            "p": [
                float("nan") if _value_is_nan(value) else float(value)
                for value in pressures[:row_count]
            ],
        }).sort_values("dt")
        return frame.dropna(subset=["dt"])

    def _trend_from_pressure_frame(df_pressure_ext, *, interval_minutes=None):
        if df_pressure_ext is None or df_pressure_ext.empty:
            return None
        df_pressure_ext = df_pressure_ext.sort_values("dt").groupby("dt", as_index=False).last()
        ext_times = pd.to_datetime(df_pressure_ext["dt"])
        ext_step_min = max(60, infer_series_step_minutes(ext_times))
        ext_interval_p = int(interval_minutes) if interval_minutes is not None else max(180, ext_step_min)
        ext_trend_p = calculate_trend(
            np.asarray(df_pressure_ext["p"].values, dtype=np.float64),
            ext_times,
            interval_minutes=ext_interval_p,
        )
        today_mask = (
            (ext_times >= pd.Timestamp(day_start))
            & (ext_times < pd.Timestamp(day_end))
        )
        if not bool(today_mask.any()):
            return None
        return (
            ext_times[today_mask].reset_index(drop=True),
            np.asarray(ext_trend_p[today_mask.to_numpy()], dtype=np.float64),
        )

    provider_id = str(provider_id or "").strip().upper()
    if provider_id == "WU":
        wu_hourly7d = wu_hourly7d if isinstance(wu_hourly7d, dict) else {}
        ext_epochs = list(wu_hourly7d.get("epochs", []) or [])
        ext_pressures = list(wu_hourly7d.get("pressures_abs", []) or [])
        convert_msl_to_abs = False
        if not ext_pressures:
            ext_pressures = list(wu_hourly7d.get("pressures", []) or [])
            convert_msl_to_abs = True
        base_frame = _pressure_frame_from_times(base_pressure_times, base_pressure_values)
        if wu_hourly7d.get("has_data") and ext_epochs and ext_pressures and base_frame is not None:
            hourly_frame = _pressure_frame_from_epochs(
                ext_epochs,
                ext_pressures,
                convert_msl_to_abs=convert_msl_to_abs,
            )
            if hourly_frame is None:
                return pressure_trend_times, pressure_trend_values
            lookback_start = pd.Timestamp(day_start) - pd.Timedelta(hours=3)
            lookback_mask = (
                (hourly_frame["dt"] >= lookback_start)
                & (hourly_frame["dt"] < pd.Timestamp(day_start))
            )
            today_mask = (
                (base_frame["dt"] >= pd.Timestamp(day_start))
                & (base_frame["dt"] < pd.Timestamp(day_end))
            )
            merged_pressure = pd.concat(
                [hourly_frame.loc[lookback_mask], base_frame.loc[today_mask]],
                ignore_index=True,
            )
            extended = _trend_from_pressure_frame(merged_pressure, interval_minutes=180)
            if extended is not None:
                return extended
        return pressure_trend_times, pressure_trend_values

    if provider_id != "METEOFRANCE":
        return pressure_trend_times, pressure_trend_values

    station_id = get_provider_station_id(provider_id)
    if not station_id:
        return pressure_trend_times, pressure_trend_values

    meteofrance_service = get_meteofrance_service()
    pressure_payload = meteofrance_service.fetch_meteofrance_today_pressure_series_with_lookback(
        station_id,
        meteofrance_service.METEOFRANCE_API_KEY,
        hours_before_start=3,
    )
    ext_epochs = pressure_payload.get("epochs", [])
    ext_pressures = pressure_payload.get("pressures_abs", [])
    if not pressure_payload.get("has_data") or len(ext_epochs) != len(ext_pressures):
        return pressure_trend_times, pressure_trend_values

    pressure_frame = _pressure_frame_from_epochs(ext_epochs, ext_pressures)
    extended = _trend_from_pressure_frame(pressure_frame)
    if extended is None:
        return pressure_trend_times, pressure_trend_values
    return extended

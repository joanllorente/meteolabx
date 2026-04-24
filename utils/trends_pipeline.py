"""
Pipeline común para preparar datasets de tendencias.
"""

from __future__ import annotations


def build_trends_frame(epochs, temps, humidities, pressures):
    import pandas as pd

    epochs = list(epochs or [])
    row_count = len(epochs)
    if row_count == 0:
        return pd.DataFrame(columns=["dt", "temp", "rh", "p"])

    def _coerce_series(values):
        return pd.Series(list(values or [])[:row_count], dtype="float64").reindex(range(row_count))

    frame = pd.DataFrame(
        {
            "dt": pd.to_datetime(pd.Series(epochs[:row_count]), unit="s", errors="coerce"),
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
    from datetime import timedelta

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
):
    df_trends = build_trends_frame(
        series_payload.get("epochs", []),
        series_payload.get("temps", []),
        series_payload.get("humidities", []),
        series_payload.get(pressure_key, []),
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
    logger,
    is_nan,
    e_s,
    station_elevation,
):
    provider_id = str(provider_id or "").strip().upper()
    if not hourly7d.get("has_data", False):
        return {"warning_only": True}

    if coverage_note_key:
        epochs = hourly7d.get("epochs", [])
        if epochs:
            synoptic_span_h = (max(epochs) - min(epochs)) / 3600.0
            if synoptic_span_h < 6.0 * 24.0:
                render_neutral_info_note(
                    coverage_note_key,
                    title=coverage_title,
                )

    epochs_7d = hourly7d.get("epochs", [])
    temps_7d = hourly7d.get("temps", [])
    humidities_7d = hourly7d.get("humidities", [])
    dewpts_7d = hourly7d.get("dewpts", [])
    pressures_7d = hourly7d.get("pressures", [])

    if provider_id == "WU":
        import math

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
    )
    if df_trends.empty:
        logger.warning(f"Tendencia sinóptica {provider_id}: DataFrame vacío")
        return None

    import pandas as pd

    df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("3h")
    df_trends = df_trends.groupby("dt", as_index=False).last()
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
):
    import numpy as np
    import pandas as pd
    from datetime import datetime
    from models.trends import calculate_trend

    provider_id = str(provider_id or "").strip().upper()
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

    df_pressure_ext = pd.DataFrame({
        "dt": [datetime.fromtimestamp(ep) for ep in ext_epochs],
        "p": ext_pressures,
    }).sort_values("dt")
    if df_pressure_ext.empty:
        return pressure_trend_times, pressure_trend_values

    ext_times = pd.to_datetime(df_pressure_ext["dt"])
    ext_step_min = max(60, infer_series_step_minutes(ext_times))
    ext_interval_p = max(180, ext_step_min)
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
        return pressure_trend_times, pressure_trend_values
    return (
        ext_times[today_mask].reset_index(drop=True),
        np.asarray(ext_trend_p[today_mask.to_numpy()], dtype=np.float64),
    )


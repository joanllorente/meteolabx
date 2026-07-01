"""
Pipeline común para preparar datasets de tendencias.
"""

from __future__ import annotations

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


def build_trends_frame(
    epochs, temps, humidities, pressures, *, tz_name: str = "", derived: dict | None = None,
):
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

    columns = {
            "dt": pd.to_datetime(_local_datetimes(epochs), errors="coerce"),
            "temp": _coerce_series(temps),
            "rh": _coerce_series(humidities),
            "p": _coerce_series(pressures),
    }
    derived = derived if isinstance(derived, dict) else {}
    for source, target in (
        ("theta_e", "theta_e"),
        ("mixing_ratios", "mixing_ratio"),
        ("theta_e_trends", "trend_theta_e"),
        ("mixing_ratio_trends", "trend_mixing_ratio"),
        ("pressure_trends", "trend_pressure"),
        ("wind_u", "wind_u"),
        ("wind_v", "wind_v"),
    ):
        columns[target] = _coerce_series(derived.get(source, []))
    frame = pd.DataFrame(columns)
    return frame.sort_values("dt")


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
        derived=series_payload,
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

    # θe y razón de mezcla dependen de la HUMEDAD; su tendencia (derivada
    # discreta) debe calcularse sobre el intervalo de muestreo REAL de la
    # humedad, no el del grid (que refleja la temperatura, más densa). Si la
    # humedad llega cada 60 min (p. ej. FROST) pero usáramos el paso del grid
    # (~20 min), ``calculate_trend`` no encontraría pares válidos y la curva
    # saldría vacía.
    import pandas as pd

    grid_step = int(prepared["trend_grid_step_min"])
    df_prepared = prepared["df_trends"]
    rh_valid_dt = df_prepared.loc[
        pd.to_numeric(df_prepared.get("rh"), errors="coerce").notna(), "dt"
    ]
    if len(rh_valid_dt) >= 2:
        humidity_step = max(grid_step, int(infer_series_step_minutes(rh_valid_dt)))
    else:
        humidity_step = grid_step

    prepared["interval_theta_e"] = max(20, humidity_step)
    prepared["interval_e"] = max(20, humidity_step)
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
    pressures_7d = hourly7d.get("pressures_abs", [])
    if pressures_7d is None or len(pressures_7d) == 0:
        pressures_7d = hourly7d.get("pressures", [])

    df_trends = build_trends_frame(
        epochs_7d,
        temps_7d,
        humidities_7d,
        pressures_7d,
        tz_name=tz_name,
        derived=hourly7d,
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

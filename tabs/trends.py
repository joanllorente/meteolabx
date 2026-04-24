import streamlit as st
from utils.provider_features import get_provider_feature
from utils.series_state import series_from_state
from utils.trends_pipeline import (
    derive_mixing_ratio_series,
    derive_theta_e_series,
    extend_today_pressure_trend,
    load_synoptic_trends_source,
    prepare_today_dataset_from_series,
)


def _prepare_today_dataset_from_series(*args, **kwargs):
    return prepare_today_dataset_from_series(*args, **kwargs)


def _load_synoptic_trends_source(*, provider_id, hourly7d, infer_series_step_minutes, render_neutral_info_note, t, logger, is_nan, e_s, station_elevation):
    provider_feature = get_provider_feature(provider_id)
    coverage_note_key = str(provider_feature.get("synoptic_coverage_note_key", "")).strip()
    translated_note = t(coverage_note_key) if coverage_note_key else ""
    return load_synoptic_trends_source(
        provider_id=provider_id,
        hourly7d=hourly7d,
        infer_series_step_minutes=infer_series_step_minutes,
        render_neutral_info_note=render_neutral_info_note,
        coverage_note_key=translated_note,
        coverage_title=t("trends.notes.provider_coverage_title"),
        logger=logger,
        is_nan=is_nan,
        e_s=e_s,
        station_elevation=station_elevation,
    )


def _load_today_trends_source(
    *,
    provider_id,
    now_local,
    infer_series_step_minutes,
    get_provider_station_id,
    get_aemet_service,
    get_meteocat_service,
    t,
    logger,
    session_state,
):
    local_chart_series = series_from_state(session_state, "chart", pressure_key="pressures_abs")
    local_prepared = None
    local_epochs = list(local_chart_series.get("epochs", []) or [])
    if local_epochs:
        local_prepared = _prepare_today_dataset_from_series(
            {
                "epochs": local_chart_series.get("epochs", []),
                "temps": local_chart_series.get("temps", []),
                "humidities": local_chart_series.get("humidities", []),
                "chart_pressures": local_chart_series.get("pressures_abs", []),
            },
            pressure_key="chart_pressures",
            now_local=now_local,
            infer_series_step_minutes=infer_series_step_minutes,
            min_source_step=5,
        )
        if local_prepared is not None:
            try:
                latest_epoch = max(int(ep) for ep in local_epochs if ep is not None)
            except Exception:
                latest_epoch = 0
            latest_age_minutes = ((now_local.timestamp() - latest_epoch) / 60.0) if latest_epoch > 0 else 1e9
            if latest_age_minutes <= 25.0:
                local_prepared["uv_series_override"] = None
                local_prepared["data_source_label"] = t(
                    "trends.sources.local_today",
                    minutes=local_prepared["interval_theta_e"],
                )
                return local_prepared

    provider_id = str(provider_id or "").strip().upper()
    provider_feature = get_provider_feature(provider_id)
    provider_today_sources = {
        "AEMET": {
            "station_missing_log": "Tendencias AEMET Hoy: falta idema",
            "fetcher": lambda station_id: get_aemet_service().fetch_aemet_today_series_with_lookback(
                station_id,
                hours_before_start=3,
            ),
            "pressure_key": "pressures",
            "min_source_step": 10,
            "empty_log": "Tendencias AEMET Hoy: sin serie con lookback",
            "frame_empty_log": "Tendencias AEMET Hoy: DataFrame con lookback vacío",
        },
        "METEOCAT": {
            "station_missing_log": "Tendencias METEOCAT Hoy: falta station_code",
            "fetcher": lambda station_id: get_meteocat_service().fetch_meteocat_today_series_with_lookback(
                station_id,
                hours_before_start=3,
            ),
            "pressure_key": "pressures_abs",
            "min_source_step": 5,
            "empty_log": "Tendencias METEOCAT Hoy: sin serie con lookback",
            "frame_empty_log": "Tendencias METEOCAT Hoy: DataFrame vacío",
        },
    }
    source_config = provider_today_sources.get(provider_id)
    if source_config is not None:
        station_id = get_provider_station_id(provider_id)
        if not station_id:
            logger.warning(source_config["station_missing_log"])
            return None
        try:
            series_payload = source_config["fetcher"](station_id)
        except Exception as err:
            logger.warning(f"Tendencias {provider_id} Hoy: error obteniendo serie con lookback: {err}")
            return None
        if not series_payload.get("has_data", False):
            logger.warning(source_config["empty_log"])
            return None
        prepared = _prepare_today_dataset_from_series(
            series_payload,
            pressure_key=source_config["pressure_key"],
            now_local=now_local,
            infer_series_step_minutes=infer_series_step_minutes,
            min_source_step=source_config["min_source_step"],
        )
        if not prepared:
            logger.warning(source_config["frame_empty_log"])
            return None
        prepared["uv_series_override"] = series_payload
        prepared["data_source_label"] = t(
            str(provider_feature.get("today_trends_source_key", "")),
            minutes=prepared["trend_grid_step_min"],
        )
        return prepared

    if local_prepared is None:
        logger.warning("Tendencias 20 min: sin chart_epochs")
        return None

    local_prepared["uv_series_override"] = None
    local_prepared["data_source_label"] = t(
        "trends.sources.local_today",
        minutes=local_prepared["interval_theta_e"],
    )
    return local_prepared


def _render_synoptic_unavailable_notice(provider_id, *, t, render_neutral_info_note, logger):
    provider_id = str(provider_id or "").strip().upper()
    provider_feature = get_provider_feature(provider_id)
    warning_key = str(provider_feature.get("synoptic_unavailable_warning_key", "")).strip()
    caption_key = str(provider_feature.get("synoptic_unavailable_caption_key", "")).strip()
    note_key = str(provider_feature.get("synoptic_unavailable_note_key", "")).strip()
    if warning_key:
        st.warning(t(warning_key))
        if caption_key:
            st.caption(t(caption_key))
    elif note_key:
        render_neutral_info_note(
            t(note_key),
            title=t("trends.notes.provider_coverage_title"),
        )
    else:
        st.warning(t("trends.warnings.series_unavailable"))
    logger.warning(f"Sin datos horarios para tendencia sinóptica ({provider_id})")


def _apply_trend_figure_layout(
    fig,
    *,
    title_text,
    x_title,
    y_title,
    y_range,
    day_start,
    day_end,
    tickformat,
    dtick_ms,
    text_color,
    grid_color,
    margin_top: int = 60,
):
    fig.update_layout(
        title=dict(text=title_text, x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
        xaxis=dict(
            title=dict(text=x_title, font=dict(color=text_color)),
            type="date",
            range=[day_start, day_end],
            tickformat=tickformat,
            dtick=dtick_ms,
            gridcolor=grid_color,
            showgrid=True,
            tickfont=dict(color=text_color),
        ),
        yaxis=dict(
            title=dict(text=y_title, font=dict(color=text_color)),
            range=y_range,
            gridcolor=grid_color,
            showgrid=True,
            tickfont=dict(color=text_color),
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        height=400,
        margin=dict(l=60, r=40, t=margin_top, b=60),
        font=dict(family='system-ui, -apple-system, "Segoe UI", Roboto, Arial', color=text_color),
        annotations=[dict(
            text="meteolabx.com",
            xref="paper", yref="paper",
            x=0.98, y=0.02,
            xanchor="right", yanchor="bottom",
            showarrow=False,
            font=dict(size=10, color="rgba(128,128,128,0.5)")
        )],
    )


def render_trends_tab(ctx):
    t = ctx["t"]
    dark = ctx["dark"]
    connected = ctx["connected"]
    logger = ctx["logger"]
    theme_mode = ctx["theme_mode"]
    p_abs = ctx["p_abs"]
    p_msl = ctx["p_msl"]
    temp_unit_pref = ctx["temp_unit_pref"]
    temp_unit_txt = ctx["temp_unit_txt"]
    pressure_unit_pref = ctx["pressure_unit_pref"]
    pressure_unit_txt = ctx["pressure_unit_txt"]
    wind_unit_pref = ctx["wind_unit_pref"]
    wind_unit_txt = ctx["wind_unit_txt"]
    _get_aemet_service = ctx["_get_aemet_service"]
    _get_meteocat_service = ctx["_get_meteocat_service"]
    _get_meteofrance_service = ctx["_get_meteofrance_service"]
    _render_neutral_info_note = ctx["_render_neutral_info_note"]
    _infer_series_step_minutes = ctx["_infer_series_step_minutes"]
    _fetch_trends_synoptic_series = ctx["_fetch_trends_synoptic_series"]
    _get_provider_station_id = ctx["_get_provider_station_id"]
    _plotly_chart_stretch = ctx["_plotly_chart_stretch"]
    convert_temperature_delta = ctx["convert_temperature_delta"]
    convert_pressure = ctx["convert_pressure"]
    convert_wind = ctx["convert_wind"]
    is_nan = ctx["is_nan"]
    e_s = ctx["e_s"]
    components = ctx["components"]
    # Definir colores según tema
    if dark:
        text_color = "rgba(255, 255, 255, 0.92)"
        grid_color = "rgba(255, 255, 255, 0.15)"
        zero_line_color = "rgba(230, 230, 230, 0.65)"
        now_line_color = "rgba(230, 236, 245, 0.7)"
    else:
        text_color = "rgba(15, 18, 25, 0.92)"
        grid_color = "rgba(18, 18, 18, 0.12)"
        zero_line_color = "rgba(55, 55, 55, 0.65)"
        now_line_color = "rgba(35, 42, 56, 0.55)"

    if not connected:
        st.info(t("trends.connect_prompt"))
    else:
        from datetime import datetime, timedelta
        import pandas as pd
        import plotly.graph_objects as go
        import math
        import numpy as np
        from models.trends import (
            specific_humidity, equivalent_potential_temperature,
            calculate_trend
        )

        now_local = datetime.now()
        provider_id = st.session_state.get("connection_type", "")

        st.markdown(f"### {t('trends.section_title')}")

        periodo = st.selectbox(
            t("trends.period_label"),
            ["today", "synoptic"],
            format_func=lambda value: t(f"trends.periods.{value}"),
            key="periodo_tendencias"
        )

        dataset_ready = False
        has_barometer_series = True
        uv_series_override = None

        if periodo == "today":
            st.markdown(t("trends.derivatives_today"))
            today_source = _load_today_trends_source(
                provider_id=provider_id,
                now_local=now_local,
                infer_series_step_minutes=_infer_series_step_minutes,
                get_provider_station_id=_get_provider_station_id,
                get_aemet_service=_get_aemet_service,
                get_meteocat_service=_get_meteocat_service,
                t=t,
                logger=logger,
                session_state=st.session_state,
            )
            if today_source is None:
                st.warning(t("trends.warnings.series_unavailable"))
            else:
                uv_series_override = today_source.get("uv_series_override")
                df_trends = today_source["df_trends"]
                day_start = today_source["day_start"]
                day_end = today_source["day_end"]
                grid = today_source["grid"]
                series_step_min = today_source["series_step_min"]
                interval_theta_e = today_source["interval_theta_e"]
                interval_e = today_source["interval_e"]
                interval_p = today_source["interval_p"]
                dataset_ready = True
        else:
            st.markdown(t("trends.derivatives_synoptic"))
            hourly7d, _data_source_label = _fetch_trends_synoptic_series(provider_id)

            if not hourly7d.get("has_data", False):
                _render_synoptic_unavailable_notice(
                    provider_id,
                    t=t,
                    render_neutral_info_note=_render_neutral_info_note,
                    logger=logger,
                )
            else:
                synoptic_source = load_synoptic_trends_source(
                    provider_id=provider_id,
                    hourly7d=hourly7d,
                    infer_series_step_minutes=_infer_series_step_minutes,
                    render_neutral_info_note=_render_neutral_info_note,
                    coverage_note_key=t(str(get_provider_feature(provider_id).get("synoptic_coverage_note_key", "")).strip()) if str(get_provider_feature(provider_id).get("synoptic_coverage_note_key", "")).strip() else "",
                    coverage_title=t("trends.notes.provider_coverage_title"),
                    logger=logger,
                    is_nan=is_nan,
                    e_s=e_s,
                    station_elevation=st.session_state.get("station_elevation", 0),
                )
                if synoptic_source is None:
                    st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                else:
                    df_trends = synoptic_source["df_trends"]
                    grid = synoptic_source["grid"]
                    day_start = synoptic_source["day_start"]
                    day_end = synoptic_source["day_end"]
                    series_step_min = synoptic_source["series_step_min"]
                    interval_theta_e = synoptic_source["interval_theta_e"]
                    interval_e = synoptic_source["interval_e"]
                    interval_p = synoptic_source["interval_p"]
                    dataset_ready = True

        if dataset_ready:
            barometer_values = pd.to_numeric(df_trends.get("p"), errors="coerce")
            barometer_valid_count = int(barometer_values.notna().sum())
            has_barometer_series = barometer_valid_count >= 2
            if not has_barometer_series:
                logger.warning(f"Tendencias: estación sin barómetro ({provider_id})")

            theta_e_pressure_fallback = float("nan")
            try:
                theta_e_pressure_fallback = float(p_abs)
            except Exception:
                theta_e_pressure_fallback = float("nan")
            if is_nan(theta_e_pressure_fallback):
                try:
                    theta_e_pressure_fallback = float(p_msl)
                except Exception:
                    theta_e_pressure_fallback = float("nan")
            has_pressure_for_theta_e = has_barometer_series or not is_nan(theta_e_pressure_fallback)

            humidity_values = pd.to_numeric(df_trends.get("rh"), errors="coerce")
            humidity_valid_count = int(humidity_values.notna().sum())
            has_humidity_series = humidity_valid_count >= 2
            if not has_humidity_series:
                logger.warning(f"Tendencias: estación sin histórico de humedad ({provider_id})")

            missing_trend_calculations = []
            has_pressure_for_mixing_ratio = has_barometer_series or not is_nan(theta_e_pressure_fallback)

            if not has_pressure_for_theta_e or not has_humidity_series or not has_barometer_series:
                if not has_pressure_for_theta_e and not has_humidity_series:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                        t("trends.calculations.mixing_ratio"),
                    ]
                elif not has_pressure_for_theta_e:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                    ]
                elif not has_humidity_series:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                        t("trends.calculations.mixing_ratio"),
                    ]

                if not has_barometer_series:
                    missing_trend_calculations.append(t("trends.calculations.pressure"))

                if missing_trend_calculations:
                    unique_missing = []
                    for item in missing_trend_calculations:
                        if item not in unique_missing:
                            unique_missing.append(item)
                    missing_trend_calculations = unique_missing

                    sensores_faltantes = []
                    if not has_pressure_for_theta_e:
                        sensores_faltantes.append(t("trends.sensors.barometer"))
                    elif not has_barometer_series:
                        sensores_faltantes.append(t("trends.sensors.barometer_series"))
                    if not has_humidity_series:
                        sensores_faltantes.append(t("trends.sensors.hygrometer"))
                    sensores_txt = f" {t('common.and')} ".join(sensores_faltantes)
                    calculos_txt = ", ".join(missing_trend_calculations[:-1])
                    if len(missing_trend_calculations) > 1:
                        tail = missing_trend_calculations[-1]
                        calculos_txt = f"{calculos_txt} {t('common.and')} {tail}" if calculos_txt else tail
                    else:
                        calculos_txt = missing_trend_calculations[0]
                    _render_neutral_info_note(
                        t("trends.notes.missing_sensors", sensors=sensores_txt, calculations=calculos_txt),
                        title=t("trends.sensor_titles.unavailable"),
                    )

            if has_humidity_series and has_pressure_for_theta_e:
                theta_e_pressure_series = pd.to_numeric(df_trends.get("p"), errors="coerce").copy()
                if not is_nan(theta_e_pressure_fallback):
                    theta_e_pressure_series = theta_e_pressure_series.fillna(theta_e_pressure_fallback)
                df_trends["theta_e_pressure"] = theta_e_pressure_series
                df_trends["mixing_ratio_pressure"] = theta_e_pressure_series.copy()

            if periodo == "today":
                dtick_ms = 60 * 60 * 1000
                tickformat = "%H:%M"
            else:
                dtick_ms = 12 * 60 * 60 * 1000
                tickformat = "%d/%m %H:%M"

            trend_times = pd.to_datetime(df_trends["dt"])

            # --- GRÁFICO 1: Tendencia de θe ---
            if has_humidity_series and has_pressure_for_theta_e:
                try:
                    df_trends["theta_e"] = derive_theta_e_series(df_trends, equivalent_potential_temperature)
                    trend_theta_e = calculate_trend(
                        np.asarray(df_trends["theta_e"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_theta_e,
                    )
                    trend_theta_e_display = np.asarray(
                        [
                            convert_temperature_delta(value, temp_unit_pref) if not np.isnan(value) else np.nan
                            for value in trend_theta_e
                        ],
                        dtype=np.float64,
                    )

                    valid_trends = trend_theta_e_display[~np.isnan(trend_theta_e_display)]
                    if len(valid_trends) == 0:
                        st.warning(t("trends.warnings.no_theta_e"))
                    else:
                        max_abs = max(abs(valid_trends.min()), abs(valid_trends.max()))
                        y_range_theta_e = [-max_abs * 1.1, max_abs * 1.1]

                        fig_theta_e = go.Figure()
                        fig_theta_e.add_trace(go.Scatter(
                            x=trend_times, y=trend_theta_e_display, mode="lines+markers", name="dθe/dt",
                            line=dict(color="rgb(255, 107, 107)", width=2.5),
                            marker=dict(size=5, color="rgb(255, 107, 107)"), connectgaps=True
                        ))
                        fig_theta_e.add_vline(
                            x=now_local,
                            line_width=1.2,
                            line_dash="dot",
                            line_color=now_line_color,
                            opacity=0.85,
                        )
                        fig_theta_e.add_hline(y=0, line_width=1.2, line_dash="dash", opacity=0.75, line_color=zero_line_color)

                        _apply_trend_figure_layout(
                            fig_theta_e,
                            title_text=t("trends.charts.theta_e_title"),
                            x_title=t("common.hour"),
                            y_title=f"dθe/dt ({temp_unit_txt}/h)",
                            y_range=y_range_theta_e,
                            day_start=day_start,
                            day_end=day_end,
                            tickformat=tickformat,
                            dtick_ms=dtick_ms,
                            text_color=text_color,
                            grid_color=grid_color,
                        )

                        _plotly_chart_stretch(fig_theta_e, key=f"theta_e_graph_{theme_mode}_{periodo}")
                except Exception as err:
                    st.error(t("trends.errors.theta_e", error=str(err)))
                    logger.error(f"Error tendencia θe: {repr(err)}")

            # --- GRÁFICO 2: Tendencia de razón de mezcla ---
            if has_humidity_series and has_pressure_for_mixing_ratio:
                try:
                    from models.trends import vapor_pressure
                    from models.thermodynamics import mixing_ratio

                    df_trends["mixing_ratio"] = derive_mixing_ratio_series(df_trends, vapor_pressure, mixing_ratio)
                    trend_mixing_ratio = calculate_trend(
                        np.asarray(df_trends["mixing_ratio"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_e,
                    )
                    trend_mixing_ratio_display = np.asarray(trend_mixing_ratio, dtype=np.float64)

                    valid_trends_mixing_ratio = trend_mixing_ratio_display[~np.isnan(trend_mixing_ratio_display)]
                    if len(valid_trends_mixing_ratio) == 0:
                        st.warning(t("trends.warnings.no_mixing_ratio"))
                    else:
                        max_abs_mixing_ratio = max(
                            abs(valid_trends_mixing_ratio.min()),
                            abs(valid_trends_mixing_ratio.max()),
                        )
                        y_range_mixing_ratio = [-max_abs_mixing_ratio * 1.1, max_abs_mixing_ratio * 1.1]

                        fig_mixing_ratio = go.Figure()
                        fig_mixing_ratio.add_trace(go.Scatter(
                            x=trend_times, y=trend_mixing_ratio_display, mode="lines+markers", name="dr/dt",
                            line=dict(color="rgb(107, 170, 255)", width=2.5),
                            marker=dict(size=5, color="rgb(107, 170, 255)"), connectgaps=True
                        ))
                        fig_mixing_ratio.add_vline(
                            x=now_local,
                            line_width=1.2,
                            line_dash="dot",
                            line_color=now_line_color,
                            opacity=0.85,
                        )
                        fig_mixing_ratio.add_hline(y=0, line_width=1.2, line_dash="dash", opacity=0.75, line_color=zero_line_color)

                        _apply_trend_figure_layout(
                            fig_mixing_ratio,
                            title_text=t("trends.charts.mixing_ratio_title"),
                            x_title=t("common.hour"),
                            y_title=t("trends.charts.mixing_ratio_axis"),
                            y_range=y_range_mixing_ratio,
                            day_start=day_start,
                            day_end=day_end,
                            tickformat=tickformat,
                            dtick_ms=dtick_ms,
                            text_color=text_color,
                            grid_color=grid_color,
                        )

                        _plotly_chart_stretch(fig_mixing_ratio, key=f"mixing_ratio_graph_{theme_mode}_{periodo}")
                except Exception as err:
                    st.error(t("trends.errors.mixing_ratio", error=str(err)))
                    logger.error(f"Error tendencia razón de mezcla: {repr(err)}")

            # --- GRÁFICO 2.5 (solo Hoy): componentes zonal/meridional del viento ---
            if periodo == "today":
                try:
                    if isinstance(uv_series_override, dict) and uv_series_override.get("has_data"):
                        chart_epochs_uv = uv_series_override.get("epochs", [])
                        chart_winds_uv = uv_series_override.get("winds", [])
                        chart_dirs_uv = uv_series_override.get("wind_dirs", [])
                    else:
                        chart_epochs_uv = st.session_state.get("chart_epochs", [])
                        chart_winds_uv = st.session_state.get("chart_winds", [])
                        chart_dirs_uv = st.session_state.get("chart_wind_dirs", [])

                    uv_times = []
                    u_vals = []
                    v_vals = []

                    for i, epoch in enumerate(chart_epochs_uv):
                        if i >= len(chart_winds_uv) or i >= len(chart_dirs_uv):
                            continue
                        speed = chart_winds_uv[i]
                        direction_deg = chart_dirs_uv[i]
                        if is_nan(speed) or is_nan(direction_deg):
                            continue

                        theta = math.radians(float(direction_deg))
                        speed = float(speed)
                        # Convención meteorológica:
                        # u = -V sin(theta), v = -V cos(theta)
                        u_comp = -speed * math.sin(theta)
                        v_comp = -speed * math.cos(theta)

                        uv_times.append(datetime.fromtimestamp(epoch))
                        u_vals.append(u_comp)
                        v_vals.append(v_comp)

                    if len(uv_times) >= 3:
                        df_uv = pd.DataFrame({"dt": uv_times, "u": u_vals, "v": v_vals}).sort_values("dt")
                        df_uv["dt"] = pd.to_datetime(df_uv["dt"]).dt.floor("5min")
                        df_uv = df_uv.groupby("dt", as_index=False).last()

                        s_u = pd.Series(df_uv["u"].values, index=pd.to_datetime(df_uv["dt"]))
                        s_v = pd.Series(df_uv["v"].values, index=pd.to_datetime(df_uv["dt"]))
                        y_u = s_u.reindex(grid)
                        y_v = s_v.reindex(grid)
                        y_u_display = y_u.apply(lambda value: convert_wind(value, wind_unit_pref) if not is_nan(value) else float("nan"))
                        y_v_display = y_v.apply(lambda value: convert_wind(value, wind_unit_pref) if not is_nan(value) else float("nan"))

                        uv_valid = np.concatenate([
                            np.asarray(y_u_display.values, dtype=np.float64),
                            np.asarray(y_v_display.values, dtype=np.float64),
                        ])
                        uv_valid = uv_valid[~np.isnan(uv_valid)]
                        if len(uv_valid) > 0:
                            max_abs_uv = float(max(abs(uv_valid.min()), abs(uv_valid.max())))
                            if max_abs_uv < 0.5:
                                max_abs_uv = 0.5
                            y_range_uv = [-max_abs_uv * 1.1, max_abs_uv * 1.1]

                            fig_uv = go.Figure()
                            fig_uv.add_trace(go.Scatter(
                                x=grid, y=y_u_display.values, mode="lines+markers", name=t("trends.charts.uv_u"),
                                line=dict(color="rgb(255, 148, 82)", width=2.2),
                                marker=dict(size=4, color="rgb(255, 148, 82)"), connectgaps=(provider_id != "METEOCAT")
                            ))
                            fig_uv.add_trace(go.Scatter(
                                x=grid, y=y_v_display.values, mode="lines+markers", name=t("trends.charts.uv_v"),
                                line=dict(color="rgb(96, 196, 129)", width=2.2),
                                marker=dict(size=4, color="rgb(96, 196, 129)"), connectgaps=(provider_id != "METEOCAT")
                            ))
                            fig_uv.add_vline(
                                x=now_local,
                                line_width=1.2,
                                line_dash="dot",
                                line_color=now_line_color,
                                opacity=0.85,
                            )
                            fig_uv.add_hline(y=0, line_width=1.2, line_dash="dash", opacity=0.75, line_color=zero_line_color)

                            _apply_trend_figure_layout(
                                fig_uv,
                                title_text=t("trends.charts.uv_title"),
                                x_title=t("common.hour"),
                                y_title=wind_unit_txt,
                                y_range=y_range_uv,
                                day_start=day_start,
                                day_end=day_end,
                                tickformat=tickformat,
                                dtick_ms=dtick_ms,
                                text_color=text_color,
                                grid_color=grid_color,
                                margin_top=90,
                            )
                            fig_uv.update_layout(
                                showlegend=True,
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom",
                                    y=1.02,
                                    xanchor="center",
                                    x=0.5,
                                ),
                            )
                            _plotly_chart_stretch(fig_uv, key=f"wind_uv_graph_{theme_mode}_{periodo}")
                        else:
                            _render_neutral_info_note(
                                t("trends.notes.uv_insufficient"),
                                title=t("common.information"),
                            )
                    else:
                        _render_neutral_info_note(
                            t("trends.notes.uv_insufficient"),
                            title=t("common.information"),
                        )
                except Exception as err:
                    st.error(t("trends.errors.uv", error=str(err)))
                    logger.error(f"Error componentes viento u/v: {repr(err)}")

            # --- GRÁFICO 3: Tendencia de presión ---
            if has_barometer_series:
                try:
                    pressure_trend_times = trend_times
                    pressure_trend_values = calculate_trend(
                        np.asarray(df_trends["p"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_p,
                    )

                    if periodo == "today":
                        pressure_trend_times, pressure_trend_values = extend_today_pressure_trend(
                            provider_id=provider_id,
                            pressure_trend_times=pressure_trend_times,
                            pressure_trend_values=pressure_trend_values,
                            day_start=day_start,
                            day_end=day_end,
                            get_provider_station_id=_get_provider_station_id,
                            get_meteofrance_service=_get_meteofrance_service,
                            infer_series_step_minutes=_infer_series_step_minutes,
                        )
                    pressure_trend_display = np.asarray(
                        [
                            convert_pressure(value, pressure_unit_pref) if not np.isnan(value) else np.nan
                            for value in pressure_trend_values
                        ],
                        dtype=np.float64,
                    )

                    valid_trends_p = pressure_trend_display[~np.isnan(pressure_trend_display)]
                    if len(valid_trends_p) == 0:
                        st.warning(t("trends.warnings.no_pressure"))
                    else:
                        max_abs_p = max(abs(valid_trends_p.min()), abs(valid_trends_p.max()))
                        y_range_p = [-max_abs_p * 1.1, max_abs_p * 1.1]

                        fig_p = go.Figure()
                        fig_p.add_trace(go.Scatter(
                            x=pressure_trend_times, y=pressure_trend_display, mode="lines+markers", name="dp/dt",
                            line=dict(color="rgb(150, 107, 255)", width=2.5),
                            marker=dict(size=5, color="rgb(150, 107, 255)"), connectgaps=True
                        ))
                        fig_p.add_vline(
                            x=now_local,
                            line_width=1.2,
                            line_dash="dot",
                            line_color=now_line_color,
                            opacity=0.85,
                        )
                        fig_p.add_hline(y=0, line_width=1.2, line_dash="dash", opacity=0.75, line_color=zero_line_color)

                        _apply_trend_figure_layout(
                            fig_p,
                            title_text=t("trends.charts.pressure_title"),
                            x_title=t("common.hour"),
                            y_title=f"dp/dt ({pressure_unit_txt}/h)",
                            y_range=y_range_p,
                            day_start=day_start,
                            day_end=day_end,
                            tickformat=tickformat,
                            dtick_ms=dtick_ms,
                            text_color=text_color,
                            grid_color=grid_color,
                        )

                        _plotly_chart_stretch(fig_p, key=f"p_graph_{theme_mode}_{periodo}")
                except Exception as err:
                    st.error(t("trends.errors.pressure", error=str(err)))
                    logger.error(f"Error tendencia presión: {repr(err)}")
            elif not has_barometer_series:
                pass

            # Sincronizar línea vertical de hover entre gráficos separados (solo Hoy).
            if periodo == "today":
                components.html(
                    f"""
                    <script>
                    (function() {{
                        const host = window.parent;
                        const doc = host.document;
                        const LINE_NAME = "mlbx-hover-sync-line";
                        const LINE_COLOR = "{now_line_color}";

                        function getTrendPlots() {{
                            const all = Array.from(doc.querySelectorAll('[data-testid="stPlotlyChart"] .js-plotly-plot'));
                            const visible = all.filter((p) => p.offsetParent !== null);
                            if (visible.length <= 4) return visible;
                            return visible.slice(-4);
                        }}

                        function getShapesWithoutSync(plot) {{
                            const current = (plot && plot.layout && Array.isArray(plot.layout.shapes))
                                ? plot.layout.shapes
                                : [];
                            return current.filter((s) => s && s.name !== LINE_NAME);
                        }}

                        function drawSyncLine(plot, xValue) {{
                            if (!host.Plotly || !plot) return;
                            const base = getShapesWithoutSync(plot);
                            const hoverLine = {{
                                type: "line",
                                xref: "x",
                                yref: "paper",
                                x0: xValue,
                                x1: xValue,
                                y0: 0,
                                y1: 1,
                                line: {{ color: LINE_COLOR, width: 1.1, dash: "dot" }},
                                name: LINE_NAME
                            }};
                            host.Plotly.relayout(plot, {{ shapes: [...base, hoverLine] }});
                        }}

                        function clearSyncLine(plot) {{
                            if (!host.Plotly || !plot) return;
                            const base = getShapesWithoutSync(plot);
                            host.Plotly.relayout(plot, {{ shapes: base }});
                        }}

                        function bindHoverSync() {{
                            if (!host.Plotly) return;
                            const plots = getTrendPlots();
                            if (plots.length < 2) return;

                            plots.forEach((plot) => {{
                                if (plot.dataset.mlbxHoverSyncBound === "1") return;
                                plot.dataset.mlbxHoverSyncBound = "1";

                                plot.on("plotly_hover", (ev) => {{
                                    const xVal = ev && ev.points && ev.points[0] ? ev.points[0].x : null;
                                    if (xVal === null || xVal === undefined) return;
                                    plots.forEach((p) => {{
                                        if (p !== plot) drawSyncLine(p, xVal);
                                    }});
                                }});

                                plot.on("plotly_unhover", () => {{
                                    plots.forEach((p) => {{
                                        if (p !== plot) clearSyncLine(p);
                                    }});
                                }});
                            }});
                        }}

                        function scheduleHoverSyncBind() {{
                            if (host.__mlbxHoverSyncRaf) return;
                            host.__mlbxHoverSyncRaf = host.requestAnimationFrame(() => {{
                                host.__mlbxHoverSyncRaf = null;
                                bindHoverSync();
                            }});
                        }}

                        function bootstrapHoverSync(attempts) {{
                            scheduleHoverSyncBind();
                            if (attempts <= 0) return;
                            host.setTimeout(() => bootstrapHoverSync(attempts - 1), 350);
                        }}

                        bootstrapHoverSync(10);

                        if (!host.__mlbxHoverSyncEventsBound) {{
                            host.__mlbxHoverSyncEventsBound = true;
                            host.addEventListener("resize", scheduleHoverSyncBind, {{ passive: true }});
                            host.addEventListener("pageshow", scheduleHoverSyncBind, {{ passive: true }});
                        }}
                    }})();
                    </script>
                    """,
                    height=0,
                    width=0,
                )

# ============================================================

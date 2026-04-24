from datetime import datetime

import streamlit as st
from utils.provider_features import SUPPORTED_HISTORICAL_PROVIDERS, get_provider_feature
LEGACY_SUMMARY_MODE_ALIASES = {"Mensual": "monthly", "Anual": "annual"}
SUMMARY_MODE_OPTIONS = ["monthly", "annual"]


def _historical_provider_is_supported(provider_id, render_neutral_info_note, t) -> bool:
    provider_id = str(provider_id or "").strip().upper()
    provider_config = get_provider_feature(provider_id)
    if provider_config and not provider_config.get("historical_supported", False):
        note_key = str(provider_config.get("historical_note_key", "")).strip()
        if note_key:
            render_neutral_info_note(t(note_key))
        return False
    if provider_id not in SUPPORTED_HISTORICAL_PROVIDERS:
        st.info(t("historical.notes.implemented_providers"))
        return False
    return True


def _normalize_historical_summary_mode(session_state) -> str:
    current_summary_mode = LEGACY_SUMMARY_MODE_ALIASES.get(
        str(session_state.get("climo_summary_mode", "")).strip(),
        str(session_state.get("climo_summary_mode", "")).strip(),
    )
    if current_summary_mode not in SUMMARY_MODE_OPTIONS:
        current_summary_mode = SUMMARY_MODE_OPTIONS[0]
    session_state["climo_summary_mode"] = current_summary_mode
    return current_summary_mode


def _year_options(now_local: datetime, *, min_year: int = 1990, lookback_years: int = 35):
    year_floor = max(min_year, now_local.year - lookback_years)
    return list(range(now_local.year, year_floor - 1, -1))


def _load_frost_period_options(provider_id, station_id, get_frost_service):
    if str(provider_id or "").strip().upper() != "FROST":
        return {"monthly": [], "annual": []}
    frost_service = get_frost_service()
    return frost_service.get_frost_climo_period_options(
        station_id=station_id,
        client_id=frost_service.FROST_CLIENT_ID,
        client_secret=frost_service.FROST_CLIENT_SECRET,
    )


def _render_historical_inputs(
    *,
    provider_id,
    summary_mode,
    now_local,
    year_options,
    month_name,
    frost_period_options,
    t,
):
    selection = {
        "selected_months": [],
        "selected_years": [],
        "frost_selected_period": "",
        "frost_selected_periods": [],
    }

    if provider_id == "FROST":
        if summary_mode == "monthly":
            monthly_periods = frost_period_options.get("monthly", [])
            default_period = monthly_periods[-1] if monthly_periods else None
            period_col, month_col = st.columns(2)
            with period_col:
                selection["frost_selected_period"] = st.selectbox(
                    t("historical.inputs.climate_period"),
                    options=monthly_periods,
                    index=(len(monthly_periods) - 1) if monthly_periods else None,
                    key="frost_climo_period_monthly_select",
                ) if monthly_periods else ""
            with month_col:
                selection["selected_months"] = st.multiselect(
                    t("historical.inputs.months"),
                    options=list(range(1, 13)),
                    default=list(range(1, 13)),
                    format_func=lambda m: month_name(int(m)),
                    key="frost_climo_months_select",
                )
            if default_period:
                st.caption(
                    t(
                        "historical.caption.frost_period_summary",
                        period=selection["frost_selected_period"] or default_period,
                        months=len(selection["selected_months"]),
                    )
                )
        else:
            annual_periods = frost_period_options.get("annual", [])
            selection["frost_selected_periods"] = st.multiselect(
                t("historical.inputs.climate_periods"),
                options=annual_periods,
                default=annual_periods[-1:] if annual_periods else [],
                key="frost_climo_periods_annual_select",
            )
            if selection["frost_selected_periods"]:
                st.caption(
                    t(
                        "historical.caption.frost_periods_summary",
                        periods=", ".join(selection["frost_selected_periods"]),
                    )
                )
        return selection

    if summary_mode == "monthly":
        month_col, year_col = st.columns(2)
        with month_col:
            selection["selected_months"] = st.multiselect(
                t("historical.inputs.months"),
                options=list(range(1, 13)),
                default=[now_local.month],
                format_func=lambda m: month_name(int(m)),
                key="climo_months_select",
            )
        with year_col:
            selection["selected_years"] = st.multiselect(
                t("historical.inputs.years"),
                options=year_options,
                default=[now_local.year],
                key="climo_years_monthly_select",
            )
    else:
        selection["selected_years"] = st.multiselect(
            t("historical.inputs.years"),
            options=year_options,
            default=[now_local.year],
            key="climo_years_annual_select",
        )
    return selection


def _prepare_historical_selection(
    *,
    provider_id,
    summary_mode,
    selected_months,
    selected_years,
    frost_selected_period,
    frost_selected_periods,
    frost_period_options,
    get_climograms_service,
    render_neutral_info_note,
    t,
):
    climograms_service = get_climograms_service()
    max_monthly_blocks = 12
    periods = []

    if provider_id == "FROST":
        if not frost_period_options.get("monthly") and not frost_period_options.get("annual"):
            render_neutral_info_note(t("historical.notes.frost_unavailable"))
            return False, periods, climograms_service
        if summary_mode == "monthly" and (not frost_selected_period or not selected_months):
            st.info(t("historical.info.select_frost_period_and_month"))
            return False, periods, climograms_service
        if summary_mode == "annual" and not frost_selected_periods:
            st.info(t("historical.info.select_frost_period"))
            return False, periods, climograms_service
        return True, periods, climograms_service

    if not selected_years or (summary_mode == "monthly" and not selected_months):
        if summary_mode == "monthly":
            st.info(t("historical.info.select_month_and_year"))
        else:
            st.info(t("historical.info.select_year"))
        return False, periods, climograms_service

    if summary_mode == "monthly":
        monthly_blocks = len(selected_months) * len(selected_years)
        if monthly_blocks > max_monthly_blocks:
            st.warning(
                t(
                    "historical.warnings.max_monthly_blocks",
                    max_blocks=max_monthly_blocks,
                    selected_blocks=monthly_blocks,
                )
            )
            return False, periods, climograms_service

    periods = climograms_service.build_period_specs(summary_mode, selected_years, selected_months)
    if not periods:
        st.warning(t("historical.warnings.invalid_period"))
        return False, periods, climograms_service

    total_days_requested = sum((period.end - period.start).days + 1 for period in periods)
    st.caption(
        t(
            "historical.caption.period_summary",
            period_range=climograms_service.describe_period_range(periods),
            blocks=len(periods),
            days=total_days_requested,
        )
    )
    return True, periods, climograms_service


def _historical_chart_scope(provider_id, chart_granularity, summary_mode, t):
    if chart_granularity == "daily":
        return (
            t("historical.chart.x.day"),
            t("historical.chart.scope.daily"),
            t("historical.table.scope.day"),
            t("historical.table.period_col.day"),
        )
    if chart_granularity == "monthly":
        return (
            t("historical.chart.x.month"),
            t("historical.chart.scope.monthly") if provider_id != "FROST" else t("historical.chart.scope.monthly_normals"),
            t("historical.table.scope.month"),
            t("historical.table.period_col.month"),
        )
    return (
        t("historical.chart.x.year") if provider_id != "FROST" else t("historical.chart.x.climate_period"),
        t("historical.chart.scope.yearly") if provider_id != "FROST" else t("historical.chart.scope.climate_periods"),
        t("historical.table.scope.year") if provider_id != "FROST" else t("historical.table.scope.climate_period"),
        t("historical.table.period_col.year") if provider_id != "FROST" else t("historical.table.period_col.climate_period"),
    )


def _table_column_label(base_label: str, unit_txt: str) -> str:
    label = str(base_label or "").strip()
    return label if "(" in label and ")" in label else f"{label} ({unit_txt})"


def render_historical_tab(ctx):
    section_title = ctx["section_title"]
    t = ctx["t"]
    connected = ctx["connected"]
    dark = ctx["dark"]
    theme_mode = ctx["theme_mode"]
    unit_preferences = ctx["unit_preferences"]
    temp_unit_txt = ctx["temp_unit_txt"]
    precip_unit_txt = ctx["precip_unit_txt"]
    month_name = ctx["month_name"]
    WuError = ctx["WuError"]
    _render_neutral_info_note = ctx["_render_neutral_info_note"]
    _get_provider_station_id = ctx["_get_provider_station_id"]
    _get_provider_api_key = ctx["_get_provider_api_key"]
    _render_historical_provider_series_start = ctx["_render_historical_provider_series_start"]
    _get_historical_missing_message = ctx["_get_historical_missing_message"]
    _get_climograms_service = ctx["_get_climograms_service"]
    _get_frost_service = ctx["_get_frost_service"]
    _get_provider_label = ctx["_get_provider_label"]
    _fetch_historical_dataset = ctx["_fetch_historical_dataset"]
    _render_theme_table = ctx["_render_theme_table"]
    _plotly_chart_stretch = ctx["_plotly_chart_stretch"]
    section_title(t("historical.section_title"))

    if not connected:
        st.info(t("historical.connect_prompt"))
    else:
        provider_id = str(st.session_state.get("connection_type", "WU")).strip().upper() or "WU"
        if _historical_provider_is_supported(provider_id, _render_neutral_info_note, t):
            station_id = _get_provider_station_id(provider_id)
            api_key = _get_provider_api_key(provider_id)
            _render_historical_provider_series_start(provider_id, station_id)

            missing_msg = _get_historical_missing_message(provider_id, station_id, api_key)
            if missing_msg:
                st.warning(missing_msg)
            else:
                import pandas as pd
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots

                now_local = datetime.now()
                year_options = _year_options(now_local)
                _normalize_historical_summary_mode(st.session_state)

                summary_mode = st.radio(
                    t("historical.summary.label"),
                    SUMMARY_MODE_OPTIONS,
                    horizontal=True,
                    format_func=lambda mode: t(f"historical.summary.options.{mode}"),
                    key="climo_summary_mode",
                )

                frost_period_options = _load_frost_period_options(provider_id, station_id, _get_frost_service)
                selection = _render_historical_inputs(
                    provider_id=provider_id,
                    summary_mode=summary_mode,
                    now_local=now_local,
                    year_options=year_options,
                    month_name=month_name,
                    frost_period_options=frost_period_options,
                    t=t,
                )
                selected_months = selection["selected_months"]
                selected_years = selection["selected_years"]
                frost_selected_period = selection["frost_selected_period"]
                frost_selected_periods = selection["frost_selected_periods"]

                historical_ready, periods, climograms_service = _prepare_historical_selection(
                    provider_id=provider_id,
                    summary_mode=summary_mode,
                    selected_months=selected_months,
                    selected_years=selected_years,
                    frost_selected_period=frost_selected_period,
                    frost_selected_periods=frost_selected_periods,
                    frost_period_options=frost_period_options,
                    get_climograms_service=_get_climograms_service,
                    render_neutral_info_note=_render_neutral_info_note,
                    t=t,
                )

                if historical_ready:
                        daily_df = None
                        extremes_overrides = None
                        provider_label = _get_provider_label(provider_id)
                        with st.spinner(t("historical.spinner.loading", provider=provider_label)):
                            try:
                                daily_df, extremes_overrides = _fetch_historical_dataset(
                                    provider_id=provider_id,
                                    climograms_service=climograms_service,
                                    station_id=station_id,
                                    api_key=api_key,
                                    summary_mode=summary_mode,
                                    periods=periods,
                                    selected_years=selected_years,
                                    selected_months=selected_months,
                                    frost_selected_period=frost_selected_period,
                                    frost_selected_periods=frost_selected_periods,
                                )
                            except WuError as e:
                                if provider_id == "WU":
                                    if e.kind == "unauthorized":
                                        st.error(t("historical.errors.wu_unauthorized"))
                                    elif e.kind == "notfound":
                                        st.error(t("historical.errors.wu_notfound"))
                                    elif e.kind == "ratelimit":
                                        st.error(t("historical.errors.wu_ratelimit"))
                                    elif e.kind == "timeout":
                                        st.error(t("historical.errors.wu_timeout"))
                                    elif e.kind == "network":
                                        st.error(t("historical.errors.wu_network"))
                                    else:
                                        status_msg = f" (HTTP {e.status_code})" if e.status_code else ""
                                        st.error(t("historical.errors.wu_http", status_msg=status_msg))
                                else:
                                    st.error(t("historical.errors.meteocat_generic"))
                            except Exception as exc:
                                st.error(
                                    t(
                                        "historical.errors.provider_generic",
                                        provider=provider_label,
                                        error_type=type(exc).__name__,
                                        error=exc,
                                    )
                                )

                        if daily_df is not None:
                            if daily_df.empty:
                                st.warning(t("historical.warnings.no_data_selected_period"))
                            else:
                                data_start = pd.to_datetime(daily_df["date"]).min()
                                data_end = pd.to_datetime(daily_df["date"]).max()
                                st.caption(
                                    t(
                                        "historical.caption.records_received",
                                        count=len(daily_df),
                                        start=data_start.strftime('%d/%m/%Y'),
                                        end=data_end.strftime('%d/%m/%Y'),
                                    )
                                )

                                st.markdown(f"### {t('historical.sections.extremes')}")
                                extremes_table_df = climograms_service.build_extremes_table(
                                    daily_df,
                                    overrides=extremes_overrides,
                                    unit_preferences=unit_preferences,
                                )
                                _render_theme_table(extremes_table_df)

                                st.markdown(f"### {t('historical.sections.summary')}")
                                general_table_df = climograms_service.build_general_metrics_table(
                                    daily_df,
                                    unit_preferences=unit_preferences,
                                )
                                _render_theme_table(general_table_df)

                                if provider_id == "FROST":
                                    chart_granularity = "monthly" if summary_mode == "monthly" else "yearly"
                                else:
                                    chart_granularity = climograms_service.resolve_chart_granularity(summary_mode, len(periods))
                                chart_df = climograms_service.build_chart_table(
                                    daily_df,
                                    chart_granularity,
                                    unit_preferences=unit_preferences,
                                )

                                if not chart_df.empty:
                                    x_title, title_scope, table_scope, table_period_col = _historical_chart_scope(
                                        provider_id,
                                        chart_granularity,
                                        summary_mode,
                                        t,
                                    )

                                    if dark:
                                        text_color = "rgba(255, 255, 255, 0.92)"
                                        grid_color = "rgba(255, 255, 255, 0.14)"
                                        precip_color = "rgba(96, 165, 250, 0.45)"
                                    else:
                                        text_color = "rgba(15, 18, 25, 0.92)"
                                        grid_color = "rgba(18, 18, 18, 0.12)"
                                        precip_color = "rgba(59, 130, 246, 0.35)"

                                    fig_climo = make_subplots(specs=[[{"secondary_y": True}]])
                                    fig_climo.add_trace(
                                        go.Bar(
                                            x=chart_df["label"],
                                            y=chart_df["precip_total"],
                                            name=t("historical.chart.legend.precip"),
                                            marker_color=precip_color,
                                        ),
                                        secondary_y=True,
                                    )
                                    fig_climo.add_trace(
                                        go.Scatter(
                                            x=chart_df["label"],
                                            y=chart_df["temp_mean"],
                                            mode="lines+markers",
                                            name=t("historical.chart.legend.temp_mean"),
                                            line=dict(color="#22c55e", width=2.5),
                                        ),
                                        secondary_y=False,
                                    )
                                    fig_climo.add_trace(
                                        go.Scatter(
                                            x=chart_df["label"],
                                            y=chart_df["temp_max"],
                                            mode="lines+markers",
                                            name=t("historical.chart.legend.temp_max"),
                                            line=dict(color="#ef4444", width=2.0),
                                        ),
                                        secondary_y=False,
                                    )
                                    fig_climo.add_trace(
                                        go.Scatter(
                                            x=chart_df["label"],
                                            y=chart_df["temp_min"],
                                            mode="lines+markers",
                                            name=t("historical.chart.legend.temp_min"),
                                            line=dict(color="#3b82f6", width=2.0),
                                        ),
                                        secondary_y=False,
                                    )

                                    fig_climo.update_layout(
                                        template="meteolabx_dark" if dark else "meteolabx_light",
                                        title=dict(
                                            text=t("historical.chart.title", scope=title_scope),
                                            x=0.5,
                                            xanchor="center",
                                            y=0.98,
                                            yanchor="top",
                                            font=dict(color=text_color, size=18),
                                            pad=dict(t=0, b=18),
                                        ),
                                        height=500,
                                        margin=dict(l=40, r=40, t=92, b=40),
                                        hovermode="x unified",
                                        legend=dict(
                                            orientation="h",
                                            y=1.02,
                                            x=0.0,
                                            yanchor="bottom",
                                            font=dict(color=text_color),
                                        ),
                                        font=dict(color=text_color),
                                        plot_bgcolor="rgba(0,0,0,0)",
                                        paper_bgcolor="rgba(0,0,0,0)",
                                        annotations=[
                                            dict(
                                                text="MeteoLabX",
                                                x=0.5,
                                                y=0.5,
                                                xref="paper",
                                                yref="paper",
                                                showarrow=False,
                                                font=dict(
                                                    size=52,
                                                    color=(
                                                        "rgba(255, 255, 255, 0.08)"
                                                        if dark
                                                        else "rgba(15, 18, 25, 0.08)"
                                                    ),
                                                ),
                                                xanchor="center",
                                                yanchor="middle",
                                                textangle=-18,
                                            )
                                        ],
                                    )
                                    fig_climo.update_xaxes(
                                        title_text=x_title,
                                        showgrid=False,
                                        title_font=dict(color=text_color),
                                        tickfont=dict(color=text_color),
                                    )
                                    fig_climo.update_yaxes(
                                        title_text=temp_unit_txt,
                                        secondary_y=False,
                                        showgrid=True,
                                        gridcolor=grid_color,
                                        zeroline=False,
                                        title_font=dict(color=text_color),
                                        tickfont=dict(color=text_color),
                                    )
                                    fig_climo.update_yaxes(
                                        title_text=precip_unit_txt,
                                        secondary_y=True,
                                        showgrid=False,
                                        zeroline=False,
                                        title_font=dict(color=text_color),
                                        tickfont=dict(color=text_color),
                                    )

                                    _plotly_chart_stretch(
                                        fig_climo,
                                        key=f"climogram_chart_{theme_mode}_{summary_mode}_{chart_granularity}_{len(chart_df)}",
                                    )

                                    units_df = climograms_service.build_units_table(
                                        daily_df,
                                        chart_granularity,
                                        unit_preferences=unit_preferences,
                                    )
                                    table_df = units_df[
                                        ["label", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"]
                                    ].copy()
                                    temp_abs_max_label = _table_column_label(t("historical.table.columns.temp_abs_max"), temp_unit_txt)
                                    temp_abs_min_label = _table_column_label(t("historical.table.columns.temp_abs_min"), temp_unit_txt)
                                    temp_mean_label = _table_column_label(t("historical.table.columns.temp_mean"), temp_unit_txt)
                                    precip_label = _table_column_label(t("historical.table.columns.precip"), precip_unit_txt)
                                    table_df = table_df.rename(
                                        columns={
                                            "label": table_period_col,
                                            "temp_abs_max": temp_abs_max_label,
                                            "temp_abs_min": temp_abs_min_label,
                                            "temp_mean": temp_mean_label,
                                            "precip_total": precip_label,
                                        }
                                    )
                                    for col_name in [
                                        temp_abs_max_label,
                                        temp_abs_min_label,
                                        temp_mean_label,
                                        precip_label,
                                    ]:
                                        table_df[col_name] = pd.to_numeric(table_df[col_name], errors="coerce")
                                        table_df[col_name] = table_df[col_name].apply(
                                            lambda value: "—" if pd.isna(value) else f"{float(value):.1f}"
                                        )

                                    st.markdown(f"### {t('historical.sections.data_by', scope=table_scope)}")
                                    _render_theme_table(table_df)


# ============================================================

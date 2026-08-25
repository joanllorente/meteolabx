from datetime import datetime
import html
import math
from typing import Optional

import streamlit as st
from components.cards import card, dual_value_card, metric_group_card, render_grid, wind_extremes_card
from utils.helpers import coerce_str
from utils.i18n import month_name
from utils.provider_features import SUPPORTED_HISTORICAL_PROVIDERS, get_provider_feature

LEGACY_SUMMARY_MODE_ALIASES = {"Mensual": "monthly", "Anual": "annual"}
SUMMARY_MODE_OPTIONS = ["monthly", "annual"]
WEATHERLINK_SUMMARY_MODE_OPTIONS = ["monthly"]
HISTORICAL_RESULT_STATE_KEY = "historical_query_result_v1"
WIND_ROSE_CALM_THRESHOLD_KMH = 2.0
WIND_ROSE_SECTORS16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def _historical_provider_is_supported(provider_id, render_neutral_info_note, t) -> bool:
    provider_id = coerce_str(provider_id, upper=True)
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


def _summary_mode_options(provider_id: str) -> list[str]:
    if coerce_str(provider_id, upper=True) == "WEATHERLINK":
        return list(WEATHERLINK_SUMMARY_MODE_OPTIONS)
    return list(SUMMARY_MODE_OPTIONS)


def _normalize_historical_summary_mode(session_state, provider_id: str = "") -> str:
    options = _summary_mode_options(provider_id)
    current_summary_mode = LEGACY_SUMMARY_MODE_ALIASES.get(
        str(session_state.get("climo_summary_mode", "")).strip(),
        str(session_state.get("climo_summary_mode", "")).strip(),
    )
    if current_summary_mode not in options:
        current_summary_mode = options[0]
    session_state["climo_summary_mode"] = current_summary_mode
    return current_summary_mode


def _year_options(now_local: datetime, *, min_year: int = 1990, lookback_years: Optional[int] = 35):
    if lookback_years is None:
        year_floor = min_year
    else:
        year_floor = max(min_year, now_local.year - lookback_years)
    return list(range(now_local.year, year_floor - 1, -1))


def _provider_year_options(provider_id: str, now_local: datetime):
    provider_config = get_provider_feature(provider_id)
    min_year = int(provider_config.get("historical_min_year") or 1990)
    lookback_years = provider_config.get("historical_lookback_years", 35)
    if lookback_years is not None:
        lookback_years = int(lookback_years)
    return _year_options(now_local, min_year=min_year, lookback_years=lookback_years)


def _load_frost_period_options(provider_id, station_id):
    if coerce_str(provider_id, upper=True) != "FROST":
        return {"monthly": [], "annual": []}
    # Backend-only: los periodos de normales disponibles los resuelve el
    # backend (POST /v1/climo/frost/period-options), no una llamada
    # directa a frost.met.no desde Streamlit.
    from utils.api_client import fetch_frost_period_options_via_api

    return fetch_frost_period_options_via_api(station_id)


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


def _render_historical_selector(
    *,
    provider_id,
    summary_mode_options,
    now_local,
    year_options,
    month_name,
    frost_period_options,
    t,
):
    """Selector compacto en una fila; no ejecuta ninguna consulta por sí mismo."""
    current_mode = _normalize_historical_summary_mode(st.session_state, provider_id)
    selection = {
        "selected_months": [],
        "selected_years": [],
        "frost_selected_period": "",
        "frost_selected_periods": [],
    }

    st.markdown(
        '<span class="historical-selector-anchor" aria-hidden="true"></span>',
        unsafe_allow_html=True,
    )

    def render_mode_control():
        selected = st.segmented_control(
            t("historical.summary.label"),
            summary_mode_options,
            selection_mode="single",
            format_func=lambda mode: t(f"historical.summary.options.{mode}"),
            key="climo_summary_mode",
            disabled=len(summary_mode_options) == 1,
            width="stretch",
        )
        return selected or current_mode

    if provider_id == "FROST" and current_mode == "monthly":
        mode_col, period_col, month_col, action_col = st.columns(
            [1.0, 1.35, 1.45, 0.95], gap="medium", vertical_alignment="bottom"
        )
        with mode_col:
            summary_mode = render_mode_control()
        monthly_periods = frost_period_options.get("monthly", [])
        with period_col:
            selection["frost_selected_period"] = (
                st.selectbox(
                    t("historical.inputs.climate_period"),
                    options=monthly_periods,
                    index=(len(monthly_periods) - 1) if monthly_periods else None,
                    key="frost_climo_period_monthly_select",
                )
                if monthly_periods
                else ""
            )
        with month_col:
            selection["selected_months"] = st.multiselect(
                t("historical.inputs.months"),
                options=list(range(1, 13)),
                default=list(range(1, 13)),
                format_func=lambda month: month_name(int(month)),
                key="frost_climo_months_select",
            )
        with action_col:
            query_requested = st.button(
                t("historical.actions.query"),
                key="historical_query_button",
                type="primary",
                use_container_width=True,
            )
        return summary_mode, selection, query_requested

    if provider_id == "FROST":
        mode_col, period_col, action_col = st.columns(
            [1.0, 2.8, 0.95], gap="medium", vertical_alignment="bottom"
        )
        with mode_col:
            summary_mode = render_mode_control()
        annual_periods = frost_period_options.get("annual", [])
        with period_col:
            selection["frost_selected_periods"] = st.multiselect(
                t("historical.inputs.climate_periods"),
                options=annual_periods,
                default=annual_periods[-1:] if annual_periods else [],
                key="frost_climo_periods_annual_select",
            )
        with action_col:
            query_requested = st.button(
                t("historical.actions.query"),
                key="historical_query_button",
                type="primary",
                use_container_width=True,
            )
        return summary_mode, selection, query_requested

    if current_mode == "monthly":
        mode_col, month_col, year_col, action_col = st.columns(
            [1.0, 1.45, 1.2, 0.95], gap="medium", vertical_alignment="bottom"
        )
        with mode_col:
            summary_mode = render_mode_control()
        with month_col:
            selection["selected_months"] = st.multiselect(
                t("historical.inputs.months"),
                options=list(range(1, 13)),
                default=[now_local.month],
                format_func=lambda month: month_name(int(month)),
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
        mode_col, year_col, action_col = st.columns(
            [1.0, 2.8, 0.95], gap="medium", vertical_alignment="bottom"
        )
        with mode_col:
            summary_mode = render_mode_control()
        with year_col:
            selection["selected_years"] = st.multiselect(
                t("historical.inputs.years"),
                options=year_options,
                default=[now_local.year],
                key="climo_years_annual_select",
            )

    with action_col:
        query_requested = st.button(
            t("historical.actions.query"),
            key="historical_query_button",
            type="primary",
            use_container_width=True,
        )
    return summary_mode, selection, query_requested


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
    # Recortar el mes/año en curso a "hoy" para TODOS los proveedores: no
    # tiene sentido pedir días futuros, y así el mes actual devuelve los
    # días ya publicados en vez de salir vacío (antes solo se hacía en WU).
    if hasattr(climograms_service, "clip_periods_to_today"):
        periods = climograms_service.clip_periods_to_today(periods)
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


def _as_float(value) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


HISTORICAL_METRIC_KEYS = (
    "absolute_max",
    "absolute_min",
    "warmest_year",
    "coldest_year",
    "windiest_year",
    "max_gust",
    "wettest_year",
    "driest_year",
    "most_rain_days_year",
    "max_precip_24h",
    "sunniest_year",
    "least_sunny_year",
    "lowest_maximum",
    "highest_minimum",
    "windiest_month",
    "max_precip_24h_short",
    "windiest_day",
    "rainiest_day",
)

HISTORICAL_SUMMARY_METRIC_KEYS = (
    "mean_temperature",
    "mean_maximums",
    "mean_minimums",
    "temperature_stddev",
    "mean_wind",
    "accumulated_precipitation",
    "mean_precipitation",
    "rain_days",
    "mean_solar_irradiance",
    "mean_daily_global_solar_irradiation",
    "mean_sunshine_hours",
    "tropical_nights",
    "torrid_nights",
    "frost_nights",
)

_TEMPERATURE_METRIC_KEYS = {
    "absolute_max",
    "absolute_min",
    "warmest_year",
    "coldest_year",
    "lowest_maximum",
    "highest_minimum",
}
_WIND_METRIC_KEYS = {"windiest_year", "windiest_month", "windiest_day", "max_gust"}
_PRECIP_METRIC_KEYS = {
    "wettest_year",
    "driest_year",
    "most_rain_days_year",
    "max_precip_24h",
    "max_precip_24h_short",
    "rainiest_day",
}
_SOLAR_METRIC_KEYS = {"sunniest_year", "least_sunny_year"}


def _historical_query_signature(
    *,
    provider_id,
    station_id,
    summary_mode,
    selected_months,
    selected_years,
    frost_selected_period,
    frost_selected_periods,
):
    """Identifica la selección visible para no mezclarla con resultados previos."""
    return (
        coerce_str(provider_id, upper=True),
        coerce_str(station_id),
        str(summary_mode or ""),
        tuple(int(value) for value in selected_months),
        tuple(int(value) for value in selected_years),
        str(frost_selected_period or ""),
        tuple(str(value) for value in frost_selected_periods),
    )


def _historical_result_matches_connection(historical_result, *, provider_id, station_id) -> bool:
    """Permite editar el selector sin ocultar la ultima consulta aplicada."""
    if not isinstance(historical_result, dict):
        return False
    signature = historical_result.get("signature")
    if not isinstance(signature, (list, tuple)) or len(signature) < 2:
        return False
    return (
        signature[0] == coerce_str(provider_id, upper=True)
        and signature[1] == coerce_str(station_id)
    )


def _historical_result_summary_mode(historical_result, fallback: str) -> str:
    """Recupera el modo con el que se obtuvieron los resultados visibles."""
    mode = str((historical_result or {}).get("summary_mode") or "").strip()
    signature = (historical_result or {}).get("signature")
    if mode not in SUMMARY_MODE_OPTIONS and isinstance(signature, (list, tuple)) and len(signature) > 2:
        mode = str(signature[2] or "").strip()
    return mode if mode in SUMMARY_MODE_OPTIONS else fallback


def _split_historical_display_value(value) -> tuple[str, str]:
    text = str(value or "—").strip() or "—"
    if text == "—" or " " not in text:
        return text, ""
    number, unit = text.rsplit(" ", 1)
    try:
        float(number.replace(",", "."))
    except (TypeError, ValueError):
        return text, ""
    return number, unit


def _historical_row_has_value(row) -> bool:
    value = str((row or {}).get("value", "")).strip()
    first_token = value.split(" ", 1)[0] if value else ""
    return first_token not in {"", "-", "—", "nan", "None"}


def _historical_direction_text(value) -> str:
    """Dirección meteorológica en 16 rumbos; ausencia explícita como ``-``."""
    direction = _as_float(value)
    if direction != direction:
        return "-"
    sector_idx = int((((direction % 360.0) + 11.25) // 22.5)) % 16
    return WIND_ROSE_SECTORS16[sector_idx]


def _historical_direction_parts(value) -> tuple[str, str]:
    """Rumbo cardinal y grados legibles, conservando el valor numérico disponible."""
    direction = _as_float(value)
    if direction != direction:
        return "-", ""
    normalized = direction % 360.0
    rounded = round(normalized, 1)
    degree_text = f"{rounded:.0f}°" if math.isclose(rounded, round(rounded)) else f"{rounded:.1f}°"
    return _historical_direction_text(normalized), degree_text


def _historical_unit_with_direction(
    value: str,
    unit: str,
    direction: str,
    direction_degrees: str = "",
) -> str:
    """Añade el rumbo junto a una velocidad existente, sin ensuciar valores ausentes."""
    if str(value).strip() in {"", "-", "—", "nan", "None"}:
        return ""
    parts = [str(unit).strip(), str(direction).strip() or "-"]
    if direction_degrees:
        parts.append(str(direction_degrees).strip())
    return " · ".join(part for part in parts if part)


def _historical_gust_direction_parts(daily_df) -> tuple[str, str]:
    """Dirección asociada a la mayor racha del dataset, si el origen la conserva."""
    if (
        daily_df is None
        or getattr(daily_df, "empty", True)
        or "gust_max" not in daily_df.columns
        or "gust_dir_max" not in daily_df.columns
    ):
        return "-", ""
    import pandas as pd

    gusts = pd.to_numeric(daily_df["gust_max"], errors="coerce")
    directions = pd.to_numeric(daily_df["gust_dir_max"], errors="coerce")
    valid_gusts = gusts.dropna()
    if valid_gusts.empty:
        return "-", ""
    index = valid_gusts.idxmax()
    if pd.isna(directions.loc[index]):
        return "-", ""
    return _historical_direction_parts(directions.loc[index])


def _historical_gust_direction(daily_df) -> str:
    return _historical_gust_direction_parts(daily_df)[0]


def _historical_predominant_direction_parts(daily_df) -> tuple[str, str]:
    """Rumbo más frecuente entre las direcciones representativas disponibles."""
    stats = _wind_rose_stats_from_daily(daily_df)
    dominant = str(stats.get("dominant_dir") or "-")
    if dominant == "-" or daily_df is None or "wind_dir_mean" not in daily_df.columns:
        return "-", ""

    import pandas as pd

    directions = pd.to_numeric(daily_df["wind_dir_mean"], errors="coerce")
    speeds = (
        pd.to_numeric(daily_df["wind_mean"], errors="coerce")
        if "wind_mean" in daily_df.columns
        else pd.Series(float("nan"), index=daily_df.index, dtype=float)
    )
    sector_index = WIND_ROSE_SECTORS16.index(dominant)
    samples = []
    for direction, speed in zip(directions, speeds):
        if pd.isna(direction) or (not pd.isna(speed) and float(speed) < WIND_ROSE_CALM_THRESHOLD_KMH):
            continue
        sample_sector = int((((float(direction) % 360.0) + 11.25) // 22.5)) % 16
        if sample_sector == sector_index:
            samples.append(float(direction) % 360.0)
    if not samples:
        return dominant, ""
    radians = [math.radians(value) for value in samples]
    mean_direction = math.degrees(
        math.atan2(
            sum(math.sin(value) for value in radians),
            sum(math.cos(value) for value in radians),
        )
    ) % 360.0
    _, degree_text = _historical_direction_parts(mean_direction)
    return dominant, degree_text


def _historical_predominant_direction(daily_df) -> str:
    return _historical_predominant_direction_parts(daily_df)[0]


def _historical_windiest_day_direction_parts(daily_df, date_txt: str = "") -> tuple[str, str]:
    """Dirección del propio día más ventoso, no la predominante del periodo."""
    if (
        daily_df is None
        or getattr(daily_df, "empty", True)
        or "wind_mean" not in daily_df.columns
        or "wind_dir_mean" not in daily_df.columns
    ):
        return "-", ""
    import pandas as pd

    frame = daily_df.copy()
    has_dates = "date" in frame.columns
    if has_dates:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["wind_mean"] = pd.to_numeric(frame["wind_mean"], errors="coerce")
    frame["wind_dir_mean"] = pd.to_numeric(frame["wind_dir_mean"], errors="coerce")
    target = pd.to_datetime(str(date_txt), dayfirst=True, errors="coerce")
    if has_dates and not pd.isna(target):
        matching = frame.loc[frame["date"].dt.normalize() == target.normalize()]
        if not matching.empty:
            valid = matching.dropna(subset=["wind_mean"])
            row = (valid if not valid.empty else matching).iloc[0]
            return _historical_direction_parts(row["wind_dir_mean"])

    # Si son totales mensuales no es lícito atribuir su dirección a un día.
    looks_monthly = has_dates and len(frame) > 1 and bool((frame["date"].dt.day == 1).all())
    if looks_monthly:
        return "-", ""
    valid = frame.dropna(subset=["wind_mean"])
    if valid.empty:
        return "-", ""
    row = valid.loc[valid["wind_mean"].idxmax()]
    return _historical_direction_parts(row["wind_dir_mean"])


def _historical_windiest_month_direction_parts(daily_df) -> tuple[str, str]:
    """Dirección predominante dentro del mes con mayor viento medio."""
    if (
        daily_df is None
        or getattr(daily_df, "empty", True)
        or "date" not in daily_df.columns
        or "wind_mean" not in daily_df.columns
        or "wind_dir_mean" not in daily_df.columns
    ):
        return "-", ""
    import pandas as pd

    frame = daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["wind_mean"] = pd.to_numeric(frame["wind_mean"], errors="coerce")
    frame["wind_dir_mean"] = pd.to_numeric(frame["wind_dir_mean"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return "-", ""
    periods = frame["date"].dt.to_period("M")
    monthly_means = frame.groupby(periods)["wind_mean"].mean().dropna()
    if monthly_means.empty:
        return "-", ""
    winning_period = monthly_means.idxmax()
    return _historical_predominant_direction_parts(frame.loc[periods == winning_period])


def _historical_month_year_label(date_txt: str) -> str:
    """Convierte la fecha técnica del agregado mensual en «Mes Año»."""
    import pandas as pd

    value = str(date_txt or "").strip()
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return value
    return f"{month_name(int(parsed.month))} {int(parsed.year)}"


def _historical_override_direction_parts(extremes_overrides, metric_name: str) -> tuple[str, str]:
    override = (extremes_overrides or {}).get(metric_name) if isinstance(extremes_overrides, dict) else None
    if not isinstance(override, dict):
        return "-", ""
    return _historical_direction_parts(override.get("Dirección"))


def _historical_summary_cards(
    general_table_df,
    *,
    t,
    dark: bool,
    daily_df=None,
    solar_metric_kind: str = "sunshine_hours",
) -> list[str]:
    """Organiza el resumen en cinco familias meteorológicas estables."""
    metric_col = general_table_df.columns[0] if general_table_df is not None and len(general_table_df.columns) else None
    value_col = general_table_df.columns[1] if general_table_df is not None and len(general_table_df.columns) > 1 else None
    localized_keys = {
        str(t(f"historical.metrics.{metric_key}")): metric_key
        for metric_key in HISTORICAL_SUMMARY_METRIC_KEYS
    }
    values = {}
    if metric_col is not None and value_col is not None and not general_table_df.empty:
        for _, row in general_table_df.iterrows():
            metric_key = localized_keys.get(str(row.get(metric_col, "")).strip())
            if metric_key:
                values[metric_key] = str(row.get(value_col, "-") or "-").strip()

    def metric(metric_key: str) -> tuple[str, str, str]:
        display_value = values.get(metric_key, "-")
        first_token = display_value.split(" ", 1)[0] if display_value else ""
        if first_token in {"", "-", "—", "nan", "None"}:
            return t(f"historical.metrics.{metric_key}"), "-", ""
        value, unit = _split_historical_display_value(display_value)
        return t(f"historical.metrics.{metric_key}"), value, unit

    solar_key = next(
        (
            key
            for key in (
                "mean_daily_global_solar_irradiation",
                "mean_sunshine_hours",
                "mean_solar_irradiance",
            )
            if key in values
        ),
        (
            "mean_daily_global_solar_irradiation"
            if solar_metric_kind == "irradiation"
            else "mean_solar_irradiance"
            if solar_metric_kind == "irradiance"
            else "mean_sunshine_hours"
        ),
    )

    predominant_direction, predominant_degrees = _historical_predominant_direction_parts(daily_df)

    return [
        metric_group_card(
            t("historical.cards.average_temperatures"),
            [
                (t("historical.cards.summary_labels.mean"), *metric("mean_temperature")[1:]),
                (t("historical.cards.summary_labels.maximums"), *metric("mean_maximums")[1:]),
                (t("historical.cards.summary_labels.minimums"), *metric("mean_minimums")[1:]),
                (t("historical.cards.summary_labels.stddev"), *metric("temperature_stddev")[1:]),
            ],
            icon_kind="temp",
            uid="historical-summary-temperature",
            dark=dark,
            tooltip_key="temperatura",
        ),
        metric_group_card(
            t("historical.cards.wind_summary"),
            [
                (t("historical.cards.summary_labels.mean"), *metric("mean_wind")[1:]),
                (
                    t("historical.cards.summary_labels.predominant_direction"),
                    predominant_direction,
                    predominant_degrees,
                ),
            ],
            icon_kind="wind",
            uid="historical-summary-wind",
            dark=dark,
            tooltip_key="viento",
            equal_columns=True,
            stack_last_unit=True,
        ),
        metric_group_card(
            t("historical.cards.rain_summary"),
            [
                (t("historical.cards.summary_labels.accumulated"), *metric("accumulated_precipitation")[1:]),
                (t("historical.cards.summary_labels.mean"), *metric("mean_precipitation")[1:]),
                (t("historical.cards.summary_labels.rain_days"), *metric("rain_days")[1:]),
            ],
            icon_kind="rain",
            uid="historical-summary-rain",
            dark=dark,
            tooltip_key="precipitación hoy",
        ),
        metric_group_card(
            t("historical.cards.solar_summary"),
            [
                (
                    t(
                        "historical.cards.summary_labels.irradiance_mean"
                        if solar_key == "mean_solar_irradiance"
                        else "historical.cards.summary_labels.irradiation_mean"
                        if solar_key == "mean_daily_global_solar_irradiation"
                        else "historical.cards.summary_labels.sunshine_mean"
                    ),
                    *metric(solar_key)[1:],
                )
            ],
            icon_kind="solar",
            uid="historical-summary-solar",
            dark=dark,
            tooltip_key="radiación solar",
        ),
        metric_group_card(
            t("historical.cards.characteristic_days"),
            [
                (t("historical.cards.summary_labels.tropical"), *metric("tropical_nights")[1:]),
                (t("historical.cards.summary_labels.torrid"), *metric("torrid_nights")[1:]),
                (t("historical.cards.summary_labels.frost"), *metric("frost_nights")[1:]),
            ],
            icon_kind="temp_night",
            uid="historical-summary-characteristic-days",
            dark=dark,
            tooltip_key="temperatura",
        ),
    ]


def _historical_highlight_icon(metric_key: str) -> tuple[str, str]:
    if metric_key == "lowest_maximum":
        return "temp_cold", "temperatura"
    if metric_key == "highest_minimum":
        return "temp_night", "temperatura"
    if metric_key in _TEMPERATURE_METRIC_KEYS:
        return "temp", "temperatura"
    if metric_key in _WIND_METRIC_KEYS:
        return "wind", "viento"
    if metric_key in _PRECIP_METRIC_KEYS:
        return "rain", "precipitación hoy"
    if metric_key in _SOLAR_METRIC_KEYS:
        return "solar", "radiación solar"
    return "temp", ""


def _historical_max_precip_rate(daily_df, unit_preferences=None) -> tuple[str, str] | None:
    """Devuelve la mayor intensidad nativa del periodo y su fecha formateada."""
    if daily_df is None or getattr(daily_df, "empty", True) or "precip_rate_max" not in daily_df.columns:
        return None

    import pandas as pd
    from utils.units import format_precip, normalize_unit_preferences

    rates = pd.to_numeric(daily_df["precip_rate_max"], errors="coerce")
    if not rates.notna().any():
        return None
    index = rates.idxmax()
    rate_mm_h = float(rates.loc[index])
    prefs = normalize_unit_preferences(unit_preferences)
    precip_unit = prefs["precip"]
    decimals = 2 if precip_unit == "in" else 1
    value = f"{format_precip(rate_mm_h, precip_unit, decimals=decimals)} {precip_unit}/h"

    date_txt = ""
    date_column = "precip_rate_max_date" if "precip_rate_max_date" in daily_df.columns else "date"
    if date_column in daily_df.columns:
        timestamp = pd.to_datetime(daily_df.loc[index, date_column], errors="coerce")
        if not pd.isna(timestamp):
            date_txt = timestamp.strftime("%d/%m/%Y")
    return value, date_txt


def _historical_annual_cards(
    rows,
    *,
    t,
    dark: bool,
    daily_df=None,
    unit_preferences=None,
    solar_metric_kind: str = "sunshine_hours",
) -> list[str]:
    """Cuadrícula anual estable: seis tarjetas, con guiones si falta un sensor."""

    def row_for(metric_key: str):
        return next((row for row in rows if row["metric_key"] == metric_key), None)

    def stat(metric_key: str) -> tuple[str, str, str]:
        row = row_for(metric_key)
        if row is None or not _historical_row_has_value(row):
            return "-", "", ""
        value, unit = _split_historical_display_value(row["value"])
        date = row["date"] if row["date"] not in {"", "-", "—", "nan", "None"} else ""
        return value, unit, date

    def difference(
        primary_value: str,
        secondary_value: str,
        unit: str,
    ) -> str:
        try:
            result = abs(
                float(primary_value.replace(",", "."))
                - float(secondary_value.replace(",", "."))
            )
        except (TypeError, ValueError):
            return ""
        return f"{result:.1f} {unit}".strip()

    abs_max_value, abs_max_unit, abs_max_date = stat("absolute_max")
    abs_min_value, abs_min_unit, abs_min_date = stat("absolute_min")
    amplitude = difference(abs_max_value, abs_min_value, abs_max_unit or abs_min_unit)

    warm_value, warm_unit, warm_date = stat("warmest_year")
    cold_value, cold_unit, cold_date = stat("coldest_year")
    mean_difference = difference(warm_value, cold_value, warm_unit or cold_unit)

    high_min_value, high_min_unit, high_min_date = stat("highest_minimum")
    low_max_value, low_max_unit, low_max_date = stat("lowest_maximum")

    gust_value, gust_unit, gust_date = stat("max_gust")
    windy_value, windy_unit, windy_date = stat("windiest_year")
    gust_direction, gust_direction_degrees = _historical_gust_direction_parts(daily_df)
    predominant_direction, predominant_direction_degrees = _historical_predominant_direction_parts(daily_df)

    wet_value, wet_unit, wet_date = stat("wettest_year")
    dry_value, dry_unit, dry_date = stat("driest_year")
    max_precip_rate = _historical_max_precip_rate(daily_df, unit_preferences)
    intensity_value = ""
    if max_precip_rate is not None:
        rate_value, rate_date = max_precip_rate
        intensity_value = f"{rate_value} · {rate_date}" if rate_date else rate_value

    sunny_value, sunny_unit, sunny_date = stat("sunniest_year")
    low_sun_value, low_sun_unit, low_sun_date = stat("least_sunny_year")

    return [
        dual_value_card(
            t("historical.cards.thermal_extremes"),
            primary_value=abs_max_value,
            primary_unit=abs_max_unit,
            primary_date=abs_max_date,
            secondary_value=abs_min_value,
            secondary_unit=abs_min_unit,
            secondary_date=abs_min_date,
            footer_label=t("historical.cards.period_amplitude") if amplitude else "",
            footer_value=amplitude,
            icon_kind="temp",
            uid="historical-extremes",
            dark=dark,
            tooltip_key="temperatura",
        ),
        dual_value_card(
            t("historical.cards.mean_temp_extremes"),
            primary_value=warm_value,
            primary_unit=warm_unit,
            primary_date=warm_date,
            secondary_value=cold_value,
            secondary_unit=cold_unit,
            secondary_date=cold_date,
            footer_label=t("historical.cards.mean_temp_difference") if mean_difference else "",
            footer_value=mean_difference,
            icon_kind="temp",
            uid="historical-mean-temp-extremes",
            dark=dark,
            tooltip_key="temperatura",
        ),
        dual_value_card(
            t("historical.cards.daily_temperature_extremes"),
            primary_value=high_min_value,
            primary_unit=high_min_unit,
            primary_date=high_min_date,
            secondary_value=low_max_value,
            secondary_unit=low_max_unit,
            secondary_date=low_max_date,
            primary_label=t("historical.metrics.highest_minimum"),
            secondary_label=t("historical.metrics.lowest_maximum"),
            footer_label="",
            footer_value="",
            icon_kind="temp_night",
            uid="historical-daily-temperature-extremes",
            dark=dark,
            tooltip_key="temperatura",
        ),
        wind_extremes_card(
            t("historical.cards.wind_extremes"),
            day_label=t("historical.metrics.max_gust"),
            day_value=gust_value,
            day_unit=gust_unit,
            day_date=gust_date,
            day_direction=gust_direction,
            day_degrees=gust_direction_degrees,
            month_label=t("historical.metrics.windiest_year"),
            month_value=windy_value,
            month_unit=windy_unit,
            month_date=windy_date,
            month_direction=predominant_direction,
            month_degrees=predominant_direction_degrees,
            direction_label=t("historical.cards.summary_labels.predominant_direction"),
            uid="historical-wind-extremes",
            dark=dark,
        ),
        dual_value_card(
            t("historical.cards.precip_extremes"),
            primary_value=wet_value,
            primary_unit=wet_unit,
            primary_date=wet_date,
            secondary_value=dry_value,
            secondary_unit=dry_unit,
            secondary_date=dry_date,
            footer_label=t("historical.cards.max_intensity") if intensity_value else "",
            footer_value=intensity_value,
            icon_kind="rain",
            uid="historical-precip-extremes",
            dark=dark,
            tooltip_key="precipitación hoy",
        ),
        dual_value_card(
            t(
                "historical.cards.solar_irradiation_extremes"
                if solar_metric_kind in {"irradiation", "irradiance"}
                else "historical.cards.solar_extremes"
            ),
            primary_value=sunny_value,
            primary_unit=sunny_unit,
            primary_date=sunny_date,
            secondary_value=low_sun_value,
            secondary_unit=low_sun_unit,
            secondary_date=low_sun_date,
            footer_label="",
            footer_value="",
            icon_kind="solar",
            uid="historical-solar-extremes",
            dark=dark,
            tooltip_key="radiación solar",
        ),
    ]


def _historical_extreme_cards(
    extremes_table_df,
    *,
    t,
    dark: bool,
    daily_df=None,
    unit_preferences=None,
    summary_mode: str = "monthly",
    period_count: int = 1,
    solar_metric_kind: str = "sunshine_hours",
    extremes_overrides=None,
) -> list[str]:
    """Convierte la tabla canónica de hitos en tarjetas sin perder métricas."""
    if extremes_table_df is None or extremes_table_df.empty or len(extremes_table_df.columns) < 2:
        return []

    metric_col = extremes_table_df.columns[0]
    value_col = extremes_table_df.columns[1]
    date_col = extremes_table_df.columns[2] if len(extremes_table_df.columns) > 2 else None
    localized_keys = {
        str(t(f"historical.metrics.{metric_key}")): metric_key
        for metric_key in HISTORICAL_METRIC_KEYS
    }
    localized_keys.update(
        {
            str(t("historical.metrics.highest_solar_irradiation_year")): "sunniest_year",
            str(t("historical.metrics.lowest_solar_irradiation_year")): "least_sunny_year",
        }
    )
    rows = []
    for _, row in extremes_table_df.iterrows():
        title = str(row.get(metric_col, "")).strip()
        metric_key = localized_keys.get(title, "")
        if not metric_key:
            # La tabla puede incluir índices climáticos adicionales (p. ej.
            # noches tropicales/tórridas). No forman parte de los seis
            # hitos visuales y permanecen en el resumen tabular.
            continue
        rows.append(
            {
                "title": title,
                "metric_key": metric_key,
                "value": str(row.get(value_col, "—") or "—"),
                "date": str(row.get(date_col, "—") or "—") if date_col is not None else "—",
            }
        )

    annual_comparison = summary_mode == "annual" and int(period_count) > 1
    if annual_comparison:
        annual_keys = {
            "absolute_max",
            "absolute_min",
            "warmest_year",
            "coldest_year",
            "max_gust",
            "windiest_year",
            "wettest_year",
            "driest_year",
            "sunniest_year",
            "least_sunny_year",
            "lowest_maximum",
            "highest_minimum",
        }
        rows = [row for row in rows if row["metric_key"] in annual_keys]
        return _historical_annual_cards(
            rows,
            t=t,
            dark=dark,
            daily_df=daily_df,
            unit_preferences=unit_preferences,
            solar_metric_kind=solar_metric_kind,
        )

    absolute_max = next((row for row in rows if row["metric_key"] == "absolute_max"), None)
    absolute_min = next((row for row in rows if row["metric_key"] == "absolute_min"), None)
    lowest_maximum = next((row for row in rows if row["metric_key"] == "lowest_maximum"), None)
    highest_minimum = next((row for row in rows if row["metric_key"] == "highest_minimum"), None)
    cards = []

    if (
        absolute_max is not None
        and absolute_min is not None
        and _historical_row_has_value(absolute_max)
        and _historical_row_has_value(absolute_min)
    ):
        max_value, max_unit = _split_historical_display_value(absolute_max["value"])
        min_value, min_unit = _split_historical_display_value(absolute_min["value"])
        amplitude = "—"
        try:
            max_number = float(max_value.replace(",", "."))
            min_number = float(min_value.replace(",", "."))
            amplitude_unit = max_unit or min_unit
            amplitude = f"{abs(max_number - min_number):.1f}"
            if amplitude_unit:
                amplitude = f"{amplitude} {html.escape(amplitude_unit)}"
        except (TypeError, ValueError):
            pass

        thermal_footer_items = []
        if annual_comparison:
            for detail in (lowest_maximum, highest_minimum):
                if detail is None or not _historical_row_has_value(detail):
                    continue
                detail_value = detail["value"]
                if detail["date"] not in {"", "—", "nan", "None"}:
                    detail_value = f"{detail_value} · {detail['date']}"
                thermal_footer_items.append((detail["title"], detail_value))

        cards.append(
            dual_value_card(
                t("historical.cards.thermal_extremes"),
                primary_value=max_value,
                primary_unit=max_unit,
                primary_date=absolute_max["date"],
                secondary_value=min_value,
                secondary_unit=min_unit,
                secondary_date=absolute_min["date"],
                footer_label=t("historical.cards.period_amplitude"),
                footer_value=html.unescape(amplitude),
                footer_items=thermal_footer_items,
                icon_kind="temp",
                uid="historical-extremes",
                dark=dark,
                tooltip_key="temperatura",
            )
        )
        combined_temperature_keys = {"absolute_max", "absolute_min"}
        if annual_comparison:
            combined_temperature_keys.update({"lowest_maximum", "highest_minimum"})
        rows = [
            row for row in rows if row["metric_key"] not in combined_temperature_keys
        ]

    if annual_comparison:
        warmest_year = next((row for row in rows if row["metric_key"] == "warmest_year"), None)
        coldest_year = next((row for row in rows if row["metric_key"] == "coldest_year"), None)
        if (
            warmest_year is not None
            and coldest_year is not None
            and _historical_row_has_value(warmest_year)
            and _historical_row_has_value(coldest_year)
        ):
            warm_value, warm_unit = _split_historical_display_value(warmest_year["value"])
            cold_value, cold_unit = _split_historical_display_value(coldest_year["value"])
            mean_difference = ""
            try:
                difference = abs(float(warm_value.replace(",", ".")) - float(cold_value.replace(",", ".")))
                mean_difference = f"{difference:.1f} {warm_unit or cold_unit}".strip()
            except (TypeError, ValueError):
                pass
            cards.append(
                dual_value_card(
                    t("historical.cards.mean_temp_extremes"),
                    primary_value=warm_value,
                    primary_unit=warm_unit,
                    primary_date=warmest_year["date"],
                    secondary_value=cold_value,
                    secondary_unit=cold_unit,
                    secondary_date=coldest_year["date"],
                    footer_label=t("historical.cards.mean_temp_difference") if mean_difference else "",
                    footer_value=mean_difference,
                    icon_kind="temp",
                    uid="historical-mean-temp-extremes",
                    dark=dark,
                    tooltip_key="temperatura",
                )
            )
            rows = [
                row
                for row in rows
                if row["metric_key"] not in {"warmest_year", "coldest_year"}
            ]

    max_precip_rate = _historical_max_precip_rate(daily_df, unit_preferences)
    gust_direction, gust_direction_degrees = _historical_gust_direction_parts(daily_df)
    predominant_direction, predominant_direction_degrees = _historical_predominant_direction_parts(daily_df)
    windiest_day = next((row for row in rows if row["metric_key"] == "windiest_day"), None)
    windiest_month = next((row for row in rows if row["metric_key"] == "windiest_month"), None)
    combine_wind_periods = (
        summary_mode == "monthly"
        and int(period_count) > 1
        and windiest_day is not None
        and windiest_month is not None
        and _historical_row_has_value(windiest_day)
        and _historical_row_has_value(windiest_month)
    )
    wind_matrix_inserted = False
    override_windiest_day_direction = _historical_override_direction_parts(
        extremes_overrides, "Día más ventoso (viento medio)"
    )
    precip_dual_card = None
    if annual_comparison:
        wettest_year = next((row for row in rows if row["metric_key"] == "wettest_year"), None)
        driest_year = next((row for row in rows if row["metric_key"] == "driest_year"), None)
        if (
            wettest_year is not None
            and driest_year is not None
            and _historical_row_has_value(wettest_year)
            and _historical_row_has_value(driest_year)
        ):
            wet_value, wet_unit = _split_historical_display_value(wettest_year["value"])
            dry_value, dry_unit = _split_historical_display_value(driest_year["value"])
            intensity_value = ""
            if max_precip_rate is not None:
                rate_value, rate_date = max_precip_rate
                intensity_value = rate_value
                if rate_date:
                    intensity_value = f"{intensity_value} · {rate_date}"
            precip_dual_card = dual_value_card(
                t("historical.cards.precip_extremes"),
                primary_value=wet_value,
                primary_unit=wet_unit,
                primary_date=wettest_year["date"],
                secondary_value=dry_value,
                secondary_unit=dry_unit,
                secondary_date=driest_year["date"],
                footer_label=t("historical.cards.max_intensity") if intensity_value else "",
                footer_value=intensity_value,
                icon_kind="rain",
                uid="historical-precip-extremes",
                dark=dark,
                tooltip_key="precipitación hoy",
            )
            rows = [
                row
                for row in rows
                if row["metric_key"] not in {"wettest_year", "driest_year"}
            ]

    solar_dual_card = None
    if annual_comparison:
        sunniest_year = next((row for row in rows if row["metric_key"] == "sunniest_year"), None)
        least_sunny_year = next((row for row in rows if row["metric_key"] == "least_sunny_year"), None)
        if (
            sunniest_year is not None
            and least_sunny_year is not None
            and _historical_row_has_value(sunniest_year)
            and _historical_row_has_value(least_sunny_year)
        ):
            sunny_value, sunny_unit = _split_historical_display_value(sunniest_year["value"])
            low_sun_value, low_sun_unit = _split_historical_display_value(least_sunny_year["value"])
            solar_dual_card = dual_value_card(
                t(
                    "historical.cards.solar_irradiation_extremes"
                    if solar_metric_kind in {"irradiation", "irradiance"}
                    else "historical.cards.solar_extremes"
                ),
                primary_value=sunny_value,
                primary_unit=sunny_unit,
                primary_date=sunniest_year["date"],
                secondary_value=low_sun_value,
                secondary_unit=low_sun_unit,
                secondary_date=least_sunny_year["date"],
                footer_label="",
                footer_value="",
                icon_kind="solar",
                uid="historical-solar-extremes",
                dark=dark,
                tooltip_key="radiación solar",
            )
            rows = [
                row
                for row in rows
                if row["metric_key"] not in {"sunniest_year", "least_sunny_year"}
            ]

    card_order = {
        "lowest_maximum": 10,
        "highest_minimum": 20,
        "warmest_year": 10,
        "coldest_year": 20,
        "max_gust": 30,
        "windiest_day": 40,
        "windiest_month": 40,
        "windiest_year": 40,
        "rainiest_day": 50,
        "max_precip_24h_short": 50,
        "max_precip_24h": 50,
        "wettest_year": 50,
    }
    rows = sorted(
        enumerate(rows),
        key=lambda item: (card_order.get(item[1]["metric_key"], 100), item[0]),
    )

    metric_keys = {row["metric_key"] for _, row in rows}
    rate_target_key = next(
        (
            key
            for key in ("rainiest_day", "max_precip_24h_short", "max_precip_24h", "wettest_year")
            if key in metric_keys
        ),
        None,
    )

    for index, (_, row) in enumerate(rows, start=1):
        if not _historical_row_has_value(row):
            continue
        if combine_wind_periods and row["metric_key"] in {"windiest_day", "windiest_month"}:
            if not wind_matrix_inserted:
                day_value, day_unit = _split_historical_display_value(windiest_day["value"])
                month_value, month_unit = _split_historical_display_value(windiest_month["value"])
                day_direction, day_degrees = _historical_windiest_day_direction_parts(
                    daily_df, windiest_day["date"]
                )
                if override_windiest_day_direction[0] != "-":
                    day_direction, day_degrees = override_windiest_day_direction
                month_direction, month_degrees = _historical_windiest_month_direction_parts(daily_df)
                cards.append(
                    wind_extremes_card(
                        t("historical.cards.wind_extremes"),
                        day_label=t("historical.cards.summary_labels.windiest_day"),
                        day_value=day_value,
                        day_unit=day_unit,
                        day_date=windiest_day["date"],
                        day_direction=day_direction,
                        day_degrees=day_degrees,
                        month_label=t("historical.cards.summary_labels.windiest_month"),
                        month_value=month_value,
                        month_unit=month_unit,
                        month_date=_historical_month_year_label(windiest_month["date"]),
                        month_direction=month_direction,
                        month_degrees=month_degrees,
                        direction_label=t("historical.cards.summary_labels.predominant_direction"),
                        uid="historical-wind-period-extremes",
                        dark=dark,
                    )
                )
                wind_matrix_inserted = True
            continue
        value, unit = _split_historical_display_value(row["value"])
        direction = ""
        direction_degrees = ""
        if row["metric_key"] == "max_gust":
            direction = gust_direction
            direction_degrees = gust_direction_degrees
        elif row["metric_key"] == "windiest_day":
            direction, direction_degrees = _historical_windiest_day_direction_parts(
                daily_df, row["date"]
            )
            if override_windiest_day_direction[0] != "-":
                direction, direction_degrees = override_windiest_day_direction
        elif row["metric_key"] == "windiest_month":
            direction, direction_degrees = _historical_windiest_month_direction_parts(daily_df)
        elif row["metric_key"] == "windiest_year":
            direction = predominant_direction
            direction_degrees = predominant_direction_degrees
        icon_kind, tooltip_key = _historical_highlight_icon(row["metric_key"])
        date_html = "" if row["date"] in {"", "—", "nan", "None"} else html.escape(row["date"])
        subtitle_lines = [f"<div>{date_html}</div>"] if date_html else []
        if row["metric_key"] == rate_target_key and max_precip_rate is not None:
            rate_value, rate_date = max_precip_rate
            rate_date_html = ""
            if rate_date and rate_date != row["date"]:
                rate_date_html = f" · {html.escape(rate_date)}"
            subtitle_lines.append(
                f"<div>{html.escape(t('historical.cards.max_intensity'))}: "
                f"<b>{html.escape(rate_value)}</b>{rate_date_html}</div>"
            )
        if direction:
            cards.append(
                dual_value_card(
                    row["title"],
                    primary_value=value,
                    primary_unit=unit,
                    primary_date=row["date"] if date_html else "",
                    secondary_value=direction,
                    secondary_unit="",
                    secondary_date=direction_degrees,
                    show_arrows=False,
                    footer_label="",
                    footer_value="",
                    icon_kind=icon_kind,
                    uid=f"historical-{index}",
                    dark=dark,
                    tooltip_key=tooltip_key,
                )
            )
        else:
            cards.append(
                card(
                    html.escape(row["title"]),
                    html.escape(value),
                    html.escape(unit),
                    icon_kind=icon_kind,
                    subtitle_html="".join(subtitle_lines),
                    uid=f"historical-{index}",
                    dark=dark,
                    tooltip_key=tooltip_key,
                )
            )
    if precip_dual_card is not None:
        cards.append(precip_dual_card)
    if solar_dual_card is not None:
        cards.append(solar_dual_card)
    return cards


def _wind_rose_stats_from_daily(daily_df, *, calm_threshold: float = WIND_ROSE_CALM_THRESHOLD_KMH):
    counts = {sector: 0 for sector in WIND_ROSE_SECTORS16}
    calm = 0
    total_samples = 0
    valid_direction = 0

    if daily_df is None or "wind_dir_mean" not in daily_df.columns:
        return {
            "sectors16": list(WIND_ROSE_SECTORS16),
            "counts": counts,
            "calm": calm,
            "total_samples": total_samples,
            "valid_direction": valid_direction,
            "dir_total": 0,
            "dominant_dir": None,
            "dir_pcts": {sector: 0.0 for sector in WIND_ROSE_SECTORS16},
        }

    wind_values = daily_df["wind_mean"].tolist() if "wind_mean" in daily_df.columns else [float("nan")] * len(daily_df)
    direction_values = daily_df["wind_dir_mean"].tolist()

    for wind, direction in zip(wind_values, direction_values):
        speed = _as_float(wind)
        direction_deg = _as_float(direction)
        has_speed = speed == speed
        has_direction = direction_deg == direction_deg
        if not has_speed and not has_direction:
            continue

        total_samples += 1
        is_calm_sample = has_speed and speed < calm_threshold
        if is_calm_sample:
            calm += 1
        if not has_direction:
            continue

        valid_direction += 1
        if is_calm_sample:
            continue

        sector_idx = int((((direction_deg % 360.0) + 11.25) // 22.5)) % 16
        counts[WIND_ROSE_SECTORS16[sector_idx]] += 1

    dir_total = sum(counts.values())
    dominant_dir = max(WIND_ROSE_SECTORS16, key=lambda sector: counts[sector]) if dir_total > 0 else None
    dir_pcts = {
        sector: (100.0 * counts[sector] / dir_total) if dir_total > 0 else 0.0
        for sector in WIND_ROSE_SECTORS16
    }
    return {
        "sectors16": list(WIND_ROSE_SECTORS16),
        "counts": counts,
        "calm": calm,
        "total_samples": total_samples,
        "valid_direction": valid_direction,
        "dir_total": dir_total,
        "dominant_dir": dominant_dir,
        "dir_pcts": dir_pcts,
    }


def _render_historical_wu_wind_rose(
    daily_df,
    *,
    dark,
    theme_mode,
    station_id,
    summary_mode,
    chart_granularity,
    t,
    go,
    plotly_chart,
):
    stats = _wind_rose_stats_from_daily(daily_df)
    sectors16 = stats["sectors16"]
    counts = stats["counts"]
    calm = int(stats["calm"])
    total_samples = int(stats["total_samples"])
    valid_direction = int(stats["valid_direction"])
    dir_total = int(stats["dir_total"])
    dominant_dir = stats["dominant_dir"]

    st.markdown(f"### {t('historical.wind_rose.heading')}")
    if dir_total <= 0:
        st.info(
            t(
                "historical.wind_rose.unavailable",
                valid_direction=valid_direction,
                calm=calm,
            )
        )
        return

    if dark:
        text_color = "rgba(255, 255, 255, 0.92)"
        grid_color = "rgba(255, 255, 255, 0.14)"
    else:
        text_color = "rgba(15, 18, 25, 0.92)"
        grid_color = "rgba(18, 18, 18, 0.12)"

    dir_pcts = stats["dir_pcts"]
    theta_deg = [i * 22.5 for i in range(16)]
    r_pct = [dir_pcts[sector] for sector in sectors16]
    rose_colors = [
        "rgba(255, 170, 65, 0.90)" if sector == dominant_dir else "rgba(102, 188, 255, 0.75)"
        for sector in sectors16
    ]

    col_rose, col_stats = st.columns([0.62, 0.38], gap="large")

    with col_rose:
        fig_rose = go.Figure()
        fig_rose.add_trace(
            go.Barpolar(
                r=r_pct,
                theta=theta_deg,
                width=[20.0] * 16,
                marker_color=rose_colors,
                marker_line_color="rgba(102, 188, 255, 1)",
                marker_line_width=1,
                opacity=0.95,
                customdata=sectors16,
                hovertemplate="%{customdata}: %{r:.1f}%<extra></extra>",
                name=t("historical.wind_rose.frequency"),
            )
        )

        radial_max = max(10.0, math.ceil(max(r_pct) / 5.0) * 5.0)
        fig_rose.update_layout(
            template="meteolabx_dark" if dark else "meteolabx_light",
            title=dict(
                text=t("historical.wind_rose.title"),
                x=0.5,
                xanchor="center",
                font=dict(size=18, color=text_color),
            ),
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                angularaxis=dict(
                    direction="clockwise",
                    rotation=90,
                    tickmode="array",
                    tickvals=theta_deg,
                    ticktext=sectors16,
                    tickfont=dict(color=text_color),
                ),
                radialaxis=dict(
                    showgrid=True,
                    gridcolor=grid_color,
                    tickfont=dict(color=text_color),
                    angle=90,
                    ticksuffix="%",
                    range=[0, radial_max],
                ),
            ),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=30, r=30, t=60, b=20),
            height=460,
            font=dict(color=text_color),
            annotations=[
                dict(
                    text="meteolabx.com",
                    xref="paper",
                    yref="paper",
                    x=0.98,
                    y=0.02,
                    xanchor="right",
                    yanchor="bottom",
                    showarrow=False,
                    font=dict(size=10, color="rgba(128,128,128,0.5)"),
                )
            ],
        )
        station_token = "".join(ch if ch.isalnum() else "_" for ch in str(station_id or "wu"))
        plotly_chart(
            fig_rose,
            key=(
                "historical_wu_wind_rose_"
                f"{station_token}_{theme_mode}_{summary_mode}_{chart_granularity}_{len(daily_df)}"
            ),
        )

    with col_stats:
        calm_pct = (100.0 * calm / total_samples) if total_samples > 0 else 0.0
        dom_pct = (100.0 * counts[dominant_dir] / dir_total) if dominant_dir is not None else 0.0

        st.markdown(f"**{t('historical.wind_rose.samples')}:** {total_samples}")
        st.markdown(f"**{t('historical.wind_rose.calm')}:** {calm_pct:.1f}% ({calm})")
        if dominant_dir is not None:
            st.markdown(f"**{t('historical.wind_rose.dominant')}:** **{dominant_dir} ({dom_pct:.1f}%)**")
        else:
            st.markdown(f"**{t('historical.wind_rose.dominant')}:** -")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        rose_items = []
        for sector in sectors16:
            txt = f"{sector}: {dir_pcts[sector]:.1f}% ({counts[sector]})"
            item_class = "rose-stat-item is-dominant" if sector == dominant_dir else "rose-stat-item"
            rose_items.append(f"<div class='{item_class}'>{html.escape(txt)}</div>")
        st.markdown(
            "<div class='rose-stats-grid'>"
            + "".join(rose_items)
            + "</div>",
            unsafe_allow_html=True,
        )


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
    BackendApiError = ctx["BackendApiError"]
    _render_neutral_info_note = ctx["_render_neutral_info_note"]
    _get_provider_station_id = ctx["_get_provider_station_id"]
    _get_provider_api_key = ctx["_get_provider_api_key"]
    _get_provider_api_secret = ctx.get("_get_provider_api_secret", lambda _provider_id: "")
    _render_historical_provider_series_start = ctx["_render_historical_provider_series_start"]
    _get_historical_missing_message = ctx["_get_historical_missing_message"]
    _get_climograms_service = ctx["_get_climograms_service"]
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
            api_secret = _get_provider_api_secret(provider_id)
            _render_historical_provider_series_start(provider_id, station_id)

            missing_msg = _get_historical_missing_message(provider_id, station_id, api_key, api_secret)
            if missing_msg:
                st.warning(missing_msg)
            else:
                import pandas as pd
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots

                now_local = datetime.now()
                year_options = _provider_year_options(provider_id, now_local)
                summary_mode_options = _summary_mode_options(provider_id)
                _normalize_historical_summary_mode(st.session_state, provider_id)

                with st.container(border=True):
                    frost_period_options = _load_frost_period_options(provider_id, station_id)
                    summary_mode, selection, query_requested = _render_historical_selector(
                        provider_id=provider_id,
                        summary_mode_options=summary_mode_options,
                        now_local=now_local,
                        year_options=year_options,
                        month_name=month_name,
                        frost_period_options=frost_period_options,
                        t=t,
                    )
                    if provider_id == "WEATHERLINK":
                        st.caption(t("historical.caption.weatherlink_monthly_only"))

                selected_months = selection["selected_months"]
                selected_years = selection["selected_years"]
                frost_selected_period = selection["frost_selected_period"]
                frost_selected_periods = selection["frost_selected_periods"]
                query_signature = _historical_query_signature(
                    provider_id=provider_id,
                    station_id=station_id,
                    summary_mode=summary_mode,
                    selected_months=selected_months,
                    selected_years=selected_years,
                    frost_selected_period=frost_selected_period,
                    frost_selected_periods=frost_selected_periods,
                )

                query_period_caption_rendered = False
                if query_requested:
                    historical_ready, periods, _ = _prepare_historical_selection(
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
                    query_period_caption_rendered = historical_ready

                    if historical_ready:
                        st.session_state.pop(HISTORICAL_RESULT_STATE_KEY, None)
                        daily_df = None
                        extremes_overrides = None
                        provider_label = _get_provider_label(provider_id)
                        with st.spinner(t("historical.spinner.loading", provider=provider_label)):
                            try:
                                daily_df, extremes_overrides = _fetch_historical_dataset(
                                    provider_id=provider_id,
                                    station_id=station_id,
                                    api_key=api_key,
                                    api_secret=api_secret,
                                    summary_mode=summary_mode,
                                    periods=periods,
                                    selected_years=selected_years,
                                    selected_months=selected_months,
                                    frost_selected_period=frost_selected_period,
                                    frost_selected_periods=frost_selected_periods,
                                )
                            except BackendApiError as e:
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
                                    status_msg = f" (HTTP {e.status_code})" if e.status_code else ""
                                    st.error(
                                        t(
                                            "historical.errors.provider_generic",
                                            provider=provider_label,
                                            error_type=e.kind or "error",
                                            error=status_msg.strip() or e.kind or "error",
                                        )
                                    )
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
                            st.session_state[HISTORICAL_RESULT_STATE_KEY] = {
                                "signature": query_signature,
                                "summary_mode": summary_mode,
                                "daily_df": daily_df,
                                "extremes_overrides": extremes_overrides,
                                "periods": periods,
                            }

                historical_result = st.session_state.get(HISTORICAL_RESULT_STATE_KEY)
                historical_ready = _historical_result_matches_connection(
                    historical_result,
                    provider_id=provider_id,
                    station_id=station_id,
                )
                if historical_ready:
                        daily_df = historical_result["daily_df"]
                        extremes_overrides = historical_result.get("extremes_overrides")
                        periods = historical_result["periods"]
                        result_summary_mode = _historical_result_summary_mode(
                            historical_result,
                            summary_mode,
                        )
                        climograms_service = _get_climograms_service()
                        if periods and not query_period_caption_rendered:
                            total_days_requested = sum(
                                (period.end - period.start).days + 1 for period in periods
                            )
                            st.caption(
                                t(
                                    "historical.caption.period_summary",
                                    period_range=climograms_service.describe_period_range(periods),
                                    blocks=len(periods),
                                    days=total_days_requested,
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
                                solar_metric_kind = (
                                    "irradiation" if provider_id == "METEOCAT"
                                    else "irradiance" if provider_id == "WEATHERLINK"
                                    else "sunshine_hours"
                                )
                                extremes_table_df = climograms_service.build_extremes_table(
                                    daily_df,
                                    overrides=extremes_overrides,
                                    unit_preferences=unit_preferences,
                                    summary_mode=result_summary_mode,
                                    period_count=len(periods),
                                    include_daily_temperature_extremes=(
                                        result_summary_mode == "annual"
                                        and len(periods) > 1
                                        and provider_id in {"WU", "IEM"}
                                    ),
                                    solar_metric_kind=solar_metric_kind,
                                )
                                highlight_cards = _historical_extreme_cards(
                                    extremes_table_df,
                                    t=t,
                                    dark=dark,
                                    daily_df=daily_df,
                                    unit_preferences=unit_preferences,
                                    summary_mode=result_summary_mode,
                                    period_count=len(periods),
                                    solar_metric_kind=solar_metric_kind,
                                    extremes_overrides=extremes_overrides,
                                )
                                render_grid(
                                    highlight_cards,
                                    cols=3,
                                    extra_class="grid-basic historical-extremes-grid",
                                )

                                st.markdown(f"### {t('historical.sections.summary')}")
                                general_table_df = climograms_service.build_general_metrics_table(
                                    daily_df,
                                    unit_preferences=unit_preferences,
                                    solar_metric_kind=solar_metric_kind,
                                )
                                summary_cards = _historical_summary_cards(
                                    general_table_df,
                                    t=t,
                                    dark=dark,
                                    daily_df=daily_df,
                                    solar_metric_kind=solar_metric_kind,
                                )
                                render_grid(
                                    summary_cards,
                                    cols=3,
                                    extra_class="grid-basic historical-summary-grid",
                                )

                                if provider_id == "FROST":
                                    chart_granularity = "monthly" if result_summary_mode == "monthly" else "yearly"
                                else:
                                    chart_granularity = climograms_service.resolve_chart_granularity(
                                        result_summary_mode,
                                        len(periods),
                                    )
                                chart_df = climograms_service.build_chart_table(
                                    daily_df,
                                    chart_granularity,
                                    unit_preferences=unit_preferences,
                                )

                                if not chart_df.empty:
                                    x_title, title_scope, table_scope, table_period_col = _historical_chart_scope(
                                        provider_id,
                                        chart_granularity,
                                        result_summary_mode,
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
                                        key=f"climogram_chart_{theme_mode}_{result_summary_mode}_{chart_granularity}_{len(chart_df)}",
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

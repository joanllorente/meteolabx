import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence, Union

import streamlit as st


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


@st.cache_data(show_spinner=False)
def _solar_energy_today_wh_m2_cached(
    epochs: tuple[int, ...],
    solar_radiations: tuple[float, ...],
    day_start_ep: int,
    now_ep: int,
) -> float:
    points: list[tuple[int, float]] = []
    for ep, solar in zip(epochs, solar_radiations):
        ep_i = _safe_int(ep)
        solar_f = _safe_float(solar)
        if solar_f != solar_f or ep_i <= 0:
            continue
        if day_start_ep <= ep_i <= now_ep:
            points.append((ep_i, max(0.0, solar_f)))

    if len(points) < 2:
        return float("nan")

    points.sort(key=lambda item: item[0])
    energy_wh_m2 = 0.0
    prev_ep, prev_solar = points[0]
    for ep_i, solar_f in points[1:]:
        dt_s = ep_i - prev_ep
        if 0 < dt_s <= 2 * 3600:
            energy_wh_m2 += ((prev_solar + solar_f) * 0.5) * (dt_s / 3600.0)
        prev_ep, prev_solar = ep_i, solar_f

    return float(energy_wh_m2) if energy_wh_m2 > 0 else 0.0


@st.cache_data(show_spinner=False)
def _erythemal_dose_today_metrics_cached(
    epochs: tuple[int, ...],
    uv_indexes: tuple[float, ...],
    now_ep: int,
) -> tuple[float, float]:
    points: list[tuple[int, float]] = []
    for ep, uv_idx in zip(epochs, uv_indexes):
        ep_i = _safe_int(ep)
        uv_f = _safe_float(uv_idx)
        if uv_f != uv_f or ep_i <= 0 or ep_i > now_ep:
            continue
        points.append((ep_i, max(0.0, uv_f)))

    if not points:
        return float("nan"), float("nan")

    points.sort(key=lambda item: item[0])
    dose_j_m2 = 0.0
    for idx, (ep_i, uv_f) in enumerate(points):
        next_ep = points[idx + 1][0] if idx + 1 < len(points) else now_ep
        dt_s = next_ep - ep_i
        if dt_s <= 0 and idx > 0:
            dt_s = ep_i - points[idx - 1][0]
        if dt_s <= 0:
            dt_s = 300
        dt_s = max(60, min(1800, int(dt_s)))
        dose_j_m2 += 0.025 * uv_f * dt_s

    dose_j_m2 = max(0.0, float(dose_j_m2))
    return dose_j_m2 / 100.0, dose_j_m2


@st.cache_data(show_spinner=False)
def _wind_rose_stats_cached(
    winds: tuple[float, ...],
    gusts: tuple[float, ...],
    directions: tuple[float, ...],
) -> dict[str, Any]:
    sectors16 = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    counts = {sector: 0 for sector in sectors16}
    calm = 0
    total_samples = 0

    for wind, gust, direction in zip(winds, gusts, directions):
        w = _safe_float(wind)
        g = _safe_float(gust)
        d = _safe_float(direction)
        has_w = w == w
        has_g = g == g
        if not has_w and not has_g:
            continue
        total_samples += 1

        if has_w and has_g:
            speed_ref = max(w, g)
        elif has_w:
            speed_ref = w
        else:
            speed_ref = g

        is_calm_sample = speed_ref < 1.0
        if is_calm_sample:
            calm += 1
        if d != d or is_calm_sample:
            continue

        idx = int((d + 11.25) // 22.5) % 16
        counts[sectors16[idx]] += 1

    dir_total = sum(counts.values())
    dominant_dir = max(sectors16, key=lambda sector: counts[sector]) if dir_total > 0 else None
    dir_pcts = {
        sector: (100.0 * counts[sector] / dir_total) if dir_total > 0 else 0.0
        for sector in sectors16
    }
    return {
        "sectors16": list(sectors16),
        "counts": counts,
        "calm": calm,
        "total_samples": total_samples,
        "dir_total": dir_total,
        "dominant_dir": dominant_dir,
        "dir_pcts": dir_pcts,
    }


@dataclass
class ObservationContext:
    RD: Any
    Te: Any
    Tv: Any
    Tw: Any
    ensure_chart_data: Any
    _fmt_precip_display: Any
    _fmt_pressure_display: Any
    _fmt_radiation_display: Any
    _fmt_radiation_energy_display: Any
    _fmt_temp_display: Any
    _fmt_wind_display: Any
    _get_aemet_service: Any
    _infer_series_step_minutes: Any
    _plotly_chart_stretch: Any
    _translate_balance_label: Any
    _translate_clarity_label: Any
    _translate_pressure_trend_label: Any
    _translate_rain_intensity_label: Any
    _translate_sunrise_sunset_label: Any
    balance: Any
    base: Any
    card: Any
    clarity: Any
    connected: Any
    connection_type: Any
    convert_pressure: Any
    convert_radiation: Any
    convert_temperature: Any
    convert_wind: Any
    dark: Any
    dp3: Any
    e: Any
    et0: Any
    has_chart_data: Any
    has_radiation: Any
    html: Any
    inst_label: Any
    inst_mm_h: Any
    is_nan: Any
    lcl: Any
    logger: Any
    p_abs: Any
    p_arrow: Any
    p_label: Any
    p_msl: Any
    precip_unit_txt: Any
    pressure_unit_pref: Any
    pressure_unit_txt: Any
    q_gkg: Any
    r1_mm_h: Any
    r5_mm_h: Any
    radiation_energy_unit_txt: Any
    radiation_unit_pref: Any
    radiation_unit_txt: Any
    render_grid: Any
    rho: Any
    rho_v_gm3: Any
    section_title: Any
    sky_clarity_label: Any
    solar_rad: Any
    st: Any
    t: Any
    temp_unit_pref: Any
    temp_unit_txt: Any
    theme_mode: Any
    theta: Any
    time: Any
    uv: Any
    water_balance_label: Any
    wind_dir_text: Any
    wind_unit_pref: Any
    wind_unit_txt: Any
    z: Any


OBSERVATION_CONTEXT_FIELDS = tuple(ObservationContext.__annotations__.keys())


def build_observation_context(source: Mapping[str, Any]) -> ObservationContext:
    """Construye el contexto de Observación validando el contrato esperado."""
    missing = [key for key in OBSERVATION_CONTEXT_FIELDS if key not in source]
    if missing:
        raise KeyError(f"Missing observation context keys: {', '.join(missing)}")
    return ObservationContext(**{key: source[key] for key in OBSERVATION_CONTEXT_FIELDS})


def _coerce_observation_context(ctx: Union[ObservationContext, Mapping[str, Any]]) -> ObservationContext:
    if isinstance(ctx, ObservationContext):
        return ctx
    if isinstance(ctx, Mapping):
        return build_observation_context(ctx)
    raise TypeError("Observation context must be an ObservationContext or a mapping")


def render_observation_tab(ctx):
    ctx = _coerce_observation_context(ctx)
    RD = ctx.RD
    Te = ctx.Te
    Tv = ctx.Tv
    Tw = ctx.Tw
    ensure_chart_data = ctx.ensure_chart_data
    _fmt_precip_display = ctx._fmt_precip_display
    _fmt_pressure_display = ctx._fmt_pressure_display
    _fmt_radiation_display = ctx._fmt_radiation_display
    _fmt_radiation_energy_display = ctx._fmt_radiation_energy_display
    _fmt_temp_display = ctx._fmt_temp_display
    _fmt_wind_display = ctx._fmt_wind_display
    _get_aemet_service = ctx._get_aemet_service
    _infer_series_step_minutes = ctx._infer_series_step_minutes
    _plotly_chart_stretch = ctx._plotly_chart_stretch
    _translate_balance_label = ctx._translate_balance_label
    _translate_clarity_label = ctx._translate_clarity_label
    _translate_pressure_trend_label = ctx._translate_pressure_trend_label
    _translate_rain_intensity_label = ctx._translate_rain_intensity_label
    _translate_sunrise_sunset_label = ctx._translate_sunrise_sunset_label
    balance = ctx.balance
    base = ctx.base
    card = ctx.card
    clarity = ctx.clarity
    connected = ctx.connected
    connection_type = ctx.connection_type
    convert_pressure = ctx.convert_pressure
    convert_radiation = ctx.convert_radiation
    convert_temperature = ctx.convert_temperature
    convert_wind = ctx.convert_wind
    dark = ctx.dark
    dp3 = ctx.dp3
    e = ctx.e
    et0 = ctx.et0
    has_chart_data = ctx.has_chart_data
    has_radiation = ctx.has_radiation
    html = ctx.html
    inst_label = ctx.inst_label
    inst_mm_h = ctx.inst_mm_h
    is_nan = ctx.is_nan
    lcl = ctx.lcl
    logger = ctx.logger
    p_abs = ctx.p_abs
    p_arrow = ctx.p_arrow
    p_label = ctx.p_label
    p_msl = ctx.p_msl
    precip_unit_txt = ctx.precip_unit_txt
    pressure_unit_pref = ctx.pressure_unit_pref
    pressure_unit_txt = ctx.pressure_unit_txt
    q_gkg = ctx.q_gkg
    r1_mm_h = ctx.r1_mm_h
    r5_mm_h = ctx.r5_mm_h
    radiation_energy_unit_txt = ctx.radiation_energy_unit_txt
    radiation_unit_pref = ctx.radiation_unit_pref
    radiation_unit_txt = ctx.radiation_unit_txt
    render_grid = ctx.render_grid
    rho = ctx.rho
    rho_v_gm3 = ctx.rho_v_gm3
    section_title = ctx.section_title
    sky_clarity_label = ctx.sky_clarity_label
    solar_rad = ctx.solar_rad
    st = ctx.st
    t = ctx.t
    temp_unit_pref = ctx.temp_unit_pref
    temp_unit_txt = ctx.temp_unit_txt
    theme_mode = ctx.theme_mode
    theta = ctx.theta
    time = ctx.time
    uv = ctx.uv
    water_balance_label = ctx.water_balance_label
    wind_dir_text = ctx.wind_dir_text
    wind_unit_pref = ctx.wind_unit_pref
    wind_unit_txt = ctx.wind_unit_txt
    z = ctx.z

    def _mobile_observation_plot() -> bool:
        viewport = st.session_state.get("browser_viewport_width", 0)
        try:
            viewport_i = max(0, int(float(viewport)))
        except (TypeError, ValueError):
            viewport_i = 0
        if viewport_i == 0:
            raw = st.query_params.get("_vw", "")
            if isinstance(raw, list):
                raw = raw[0] if raw else ""
            try:
                viewport_i = max(0, int(float(str(raw).strip())))
            except (TypeError, ValueError):
                viewport_i = 0
        return 0 < viewport_i <= 900

    compact_mobile_plot = _mobile_observation_plot()
    x_tick_size = 11 if compact_mobile_plot else None
    y_tick_size = 11 if compact_mobile_plot else None
    right_y_tick_size = 11 if compact_mobile_plot else None
    section_title(t("observation.sections.observed"))

    def invalid_num(x):
        return x is None or is_nan(x)

    # Preparar valores
    temp_val = _fmt_temp_display(base.get("Tc"), decimals=1)
    rh_val = "—" if invalid_num(base.get("RH")) else f"{base['RH']:.0f}"
    td_val = _fmt_temp_display(base.get("Td"), decimals=1)
    wind_val = _fmt_wind_display(base.get("wind"), decimals=1)
    precip_total_str = _fmt_precip_display(base.get("precip_total"), decimals=1)
    p_abs_str = _fmt_pressure_display(p_abs, decimals=1)

    # Viento
    deg = base["wind_dir_deg"]
    wind = base["wind"]
    if invalid_num(wind) or wind == 0.0 or invalid_num(deg):
        wind_dir_str = "—"
    else:
        short = wind_dir_text(deg)
        wind_dir_str = f"{short} ({deg:.0f}°)"

    gust_str = _fmt_wind_display(base.get("gust"), decimals=1)

    # Lluvia
    def fmt_rate(x):
        from utils import is_nan as check_nan
        return "—" if check_nan(x) else f"{_fmt_precip_display(x, decimals=1)} {precip_unit_txt}/h"

    # Temperatura
    fl_str = "—" if is_nan(base["feels_like"]) else f"{_fmt_temp_display(base['feels_like'], decimals=1)} {temp_unit_txt}"
    _tc_hi = base.get("Tc", float("nan"))
    hi_str = (
        f"{_fmt_temp_display(base['heat_index'], decimals=1)} {temp_unit_txt}"
        if not is_nan(base["heat_index"]) and not is_nan(_tc_hi) and _tc_hi > 25
        else "—"
    )

    # Rocío
    try:
        e_vapor_val = float(e)
    except Exception:
        e_vapor_val = float("nan")
    e_vapor_str = _fmt_pressure_display(e_vapor_val, decimals=1)
    tw_sub_str = "—" if is_nan(Tw) else f"{_fmt_temp_display(Tw, decimals=1)} {temp_unit_txt}"
    p_label_card = _translate_pressure_trend_label(p_label)
    inst_label_card = _translate_rain_intensity_label(inst_label)
    is_wu_connection = str(connection_type).strip().upper() == "WU"
    rain_rate_label = t(
        "observation.cards.basic.precipitation_today.instantaneous"
        if is_wu_connection
        else "observation.cards.basic.precipitation_today.intensity"
    )
    rain_windows_html = ""
    if is_wu_connection:
        rain_windows_html = (
            f"<div style='margin-top:6px; font-size:0.8rem; opacity:0.6;'>"
            f"{t('observation.cards.basic.precipitation_today.minute_1')}: {fmt_rate(r1_mm_h)} · "
            f"{t('observation.cards.basic.precipitation_today.minute_5')}: {fmt_rate(r5_mm_h)}</div>"
        )

    # Extremos
    temp_side = ""
    tmax = base.get("temp_max")
    tmin = base.get("temp_min")
    if tmax is not None and tmin is not None and not is_nan(tmax) and not is_nan(tmin):
        temp_side = (
            f"<div class='max'>▲ {_fmt_temp_display(tmax, decimals=1)}</div>"
            f"<div class='min'>▼ {_fmt_temp_display(tmin, decimals=1)}</div>"
        )

    rh_side = ""
    rhmax = base.get("rh_max")
    rhmin = base.get("rh_min")
    if rhmax is not None and rhmin is not None and not is_nan(rhmax) and not is_nan(rhmin):
        rh_side = f"<div class='max'>▲ {rhmax:.0f}</div><div class='min'>▼ {rhmin:.0f}</div>"

    wind_side = ""
    gmax = base.get("gust_max")
    if gmax is not None and not is_nan(gmax):
        wind_side = f"<div class='max'>▲ {_fmt_wind_display(gmax, decimals=1)}</div>"

    # Usar la función card() pero asegurarnos de que se renderice correctamente
    from components.icons import icon_img

    cards_basic = [
        card(t("observation.cards.basic.temperature.title"), temp_val, temp_unit_txt, 
             icon_kind="temp", 
             subtitle_html=(
                 f"<div>{t('observation.cards.basic.temperature.feels_like')}: <b>{fl_str}</b></div>"
                 f"<div>{t('observation.cards.basic.temperature.heat_index')}: <b>{hi_str}</b></div>"
             ),
             side_html=temp_side, 
             uid="b1", dark=dark, tooltip_key="temperatura"),
        card(t("observation.cards.basic.relative_humidity.title"), rh_val, "%", 
             icon_kind="rh", 
             subtitle_html=f"<div>{t('observation.cards.basic.relative_humidity.vapor_pressure')}: <b>{e_vapor_str} {pressure_unit_txt}</b></div>",
             side_html=rh_side, 
             uid="b2", dark=dark, tooltip_key="humedad relativa"),
        card(t("observation.cards.basic.dew_point.title"), td_val, temp_unit_txt, 
             icon_kind="dew", 
             subtitle_html=f"<div>{t('observation.cards.basic.dew_point.wet_bulb')}: <b>{tw_sub_str}</b></div>", 
             uid="b3", dark=dark, tooltip_key="punto de rocío"),
        card(t("observation.cards.basic.pressure.title"), p_abs_str, pressure_unit_txt, 
             icon_kind="press", 
             subtitle_html=(
                 f"<div>{t('observation.cards.basic.pressure.trend')}: <b>{p_arrow} {p_label_card}</b></div>"
                 f"<div>{t('observation.cards.basic.pressure.delta_3h')}: <b>{_fmt_pressure_display(dp3, decimals=1)} {pressure_unit_txt}</b></div>"
                 f"<div>{t('observation.cards.basic.pressure.msl')}: <b>{_fmt_pressure_display(p_msl, decimals=1)} {pressure_unit_txt}</b></div>"
             ), 
             uid="b4", dark=dark, tooltip_key="presión"),
        card(t("observation.cards.basic.wind.title"), wind_val, wind_unit_txt, 
             icon_kind="wind", 
             subtitle_html=(
                 f"<div>{t('observation.cards.basic.wind.gust')}: <b>{gust_str}</b></div>"
                 f"<div>{t('observation.cards.basic.wind.direction')}: <b>{wind_dir_str}</b></div>"
             ), 
             side_html=wind_side, 
             uid="b5", dark=dark, tooltip_key="viento"),
        card(t("observation.cards.basic.precipitation_today.title"), precip_total_str, precip_unit_txt, 
             icon_kind="rain", 
             subtitle_html=(
                 f"<div>{rain_rate_label}: <b>{fmt_rate(inst_mm_h)}</b></div>"
                 f"<div style='font-size:0.9rem; opacity:0.85;'>{inst_label_card}</div>"
                 f"{rain_windows_html}"
             ), 
             uid="b6", dark=dark, tooltip_key="precipitación hoy"),
    ]
    render_grid(cards_basic, cols=3, extra_class="grid-basic")

    if connected and not has_chart_data and callable(ensure_chart_data):
        with st.spinner(t("observation.cards.info.loading_charts")):
            chart_ready = bool(ensure_chart_data())
        if chart_ready:
            st.rerun() if hasattr(st, "rerun") else st.experimental_rerun()
        else:
            st.info(t("observation.cards.info.loading_charts_unavailable"))

    # ============================================================
    # NIVEL 2 — TERMODINÁMICA (solo si hay barómetro e higrómetro)
    # ============================================================
    has_barometer_now = not is_nan(p_abs)
    has_humidity_now = not is_nan(base.get("RH"))
    if not connected or (has_barometer_now and has_humidity_now):
        section_title(t("observation.sections.thermodynamics"))

        q_val = "—" if is_nan(q_gkg) else f"{q_gkg:.2f}"
        rho_v_val = "—" if is_nan(rho_v_gm3) else f"{rho_v_gm3:.1f}"
        tv_val = _fmt_temp_display(Tv, decimals=1)
        te_val = _fmt_temp_display(Te, decimals=1)
        theta_val = _fmt_temp_display(theta, decimals=1)
        rho_val = "—" if is_nan(rho) else f"{rho:.3f}"
        lcl_val = "—" if is_nan(lcl) else f"{lcl:.0f}"
        sound_speed = float("nan")
        if not is_nan(Tv):
            try:
                sound_speed = math.sqrt(1.4 * RD * (float(Tv) + 273.15))
            except Exception:
                sound_speed = float("nan")
        c_sound_val = "—" if is_nan(sound_speed) else f"{sound_speed:.1f}"

        cards_derived = [
            card(t("observation.cards.thermo.specific_humidity"), q_val, "g/kg", icon_kind="qspec", uid="d1", dark=dark, tooltip_key="humedad específica"),
            card(t("observation.cards.thermo.absolute_humidity"), rho_v_val, "g/m³", icon_kind="qabs", uid="d7a", dark=dark, tooltip_key="humedad absoluta"),
            card(t("observation.cards.thermo.virtual_temp"), tv_val, temp_unit_txt, icon_kind="tv", uid="d3", dark=dark, tooltip_key="temp. virtual"),
            card(t("observation.cards.thermo.equivalent_temp"), te_val, temp_unit_txt, icon_kind="te", uid="d4", dark=dark, tooltip_key="temp. equivalente"),
            card(t("observation.cards.thermo.potential_temp"), theta_val, temp_unit_txt, icon_kind="theta", uid="d2", dark=dark, tooltip_key="temp. potencial"),
            card(t("observation.cards.thermo.air_density"), rho_val, "kg/m³", icon_kind="rho", uid="d5", dark=dark, tooltip_key="densidad del aire"),
            card(t("observation.cards.thermo.lcl"), lcl_val, "m", icon_kind="lcl", uid="d6", dark=dark, tooltip_key="base nube LCL"),
            card(t("observation.cards.thermo.speed_of_sound"), c_sound_val, "m/s", icon_kind="csound", uid="d8", dark=dark, tooltip_key="velocidad del sonido"),
        ]
        render_grid(cards_derived, cols=4, extra_class="grid-thermo")

    # ============================================================
    # NIVEL 3 — RADIACIÓN (solo si la estación tiene sensores)
    # ============================================================

    # Mostrar sección solo si no está conectado (modo demo) O si tiene sensores de radiación
    if not connected or has_radiation:
        section_title(t("observation.sections.radiation"))

        def _active_radiation_series() -> tuple[list, list, list]:
            demo_series = st.session_state.get("demo_radiation_series")
            if st.session_state.get("demo_radiation", False) and isinstance(demo_series, dict):
                return (
                    demo_series.get("epochs", []) or [],
                    demo_series.get("solar_radiations", []) or [],
                    demo_series.get("uv_indexes", []) or [],
                )
            return (
                st.session_state.get("chart_epochs", []) or [],
                st.session_state.get("chart_solar_radiations", []) or [],
                st.session_state.get("chart_uv_indexes", []) or [],
            )

        def _active_radiation_metrics() -> tuple[float, float, float]:
            epochs, solars, uv_indexes = _active_radiation_series()
            if not epochs:
                return float("nan"), float("nan"), float("nan")

            if st.session_state.get("demo_radiation", False):
                now_ep = int(time.time())
            else:
                now_ep = int(base.get("epoch", 0) or time.time())
            day_start_ep = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

            energy_wh_m2 = _solar_energy_today_wh_m2_cached(
                tuple(_safe_int(ep) for ep in epochs),
                tuple(_safe_float(solar) for solar in solars),
                day_start_ep,
                now_ep,
            )

            if is_nan(uv):
                return energy_wh_m2, float("nan"), float("nan")

            dose_sed, dose_j_m2 = _erythemal_dose_today_metrics_cached(
                tuple(_safe_int(ep) for ep in epochs),
                tuple(_safe_float(uv_idx) for uv_idx in uv_indexes),
                now_ep,
            )
            return energy_wh_m2, dose_sed, dose_j_m2

        # Formatear valores
        solar_val = _fmt_radiation_display(solar_rad, decimals=0)
        uv_val = "—" if is_nan(uv) else f"{uv:.1f}"
        energy_today_wh_m2, erythemal_dose_sed, erythemal_dose_j_m2 = _active_radiation_metrics()
        erythemal_dose_val = "—" if is_nan(erythemal_dose_sed) else f"{erythemal_dose_sed:.2f}"
        et0_val = _fmt_precip_display(et0, decimals=1)
        clarity_val = "—" if is_nan(clarity) else f"{clarity * 100:.0f}"
        balance_val = _fmt_precip_display(balance, decimals=1)
        if is_nan(energy_today_wh_m2):
            energy_today_txt = "—"
        else:
            energy_today_mj_m2 = energy_today_wh_m2 * 0.0036
            energy_today_txt = f"{_fmt_radiation_energy_display(energy_today_mj_m2, decimals=2)} {radiation_energy_unit_txt}"
        solar_sub = f"<div>{t('observation.cards.radiation.irradiance.energy_today')}: <b>{energy_today_txt}</b></div>"

        # Subtítulos
        erythema_mw_m2 = float("nan") if is_nan(uv) else (25.0 * uv)
        erythema_txt = "—" if is_nan(erythema_mw_m2) else f"{erythema_mw_m2:.1f} mW/m²"
        uv_sub = f"<div>{t('observation.cards.radiation.uv_index.erythemal_irradiance')}: <b>{erythema_txt}</b></div>"
        erythemal_dose_j_txt = "—" if is_nan(erythemal_dose_j_m2) else f"{erythemal_dose_j_m2:.0f} J/m²"
        erythemal_dose_sub = f"<div>{t('observation.cards.radiation.erythemal_dose.dose_j_m2_label')}: <b>{erythemal_dose_j_txt}</b></div>"

        et0_sub = f"<div style='font-size:0.8rem; opacity:0.65; margin-top:2px;'>{t('observation.cards.radiation.et0_today.model')}</div>"

        from models.radiation import (
            is_nighttime,
            sunrise_sunset_label,
            solar_altitude_deg,
            max_solar_altitude_day_deg,
        )

        clarity_label = sky_clarity_label(clarity)
        try:
            lat_for_clarity = base.get("lat", float("nan"))
            lon_for_clarity = base.get("lon", float("nan"))
            epoch_for_clarity = base.get("epoch", 0) if connected else int(time.time())
            if epoch_for_clarity and not is_nan(lat_for_clarity) and is_nighttime(float(lat_for_clarity), float(epoch_for_clarity), float(lon_for_clarity)):
                clarity_label = t("observation.cards.radiation.sky_clarity.night")
        except Exception:
            pass
        clarity_label = _translate_clarity_label(clarity_label)
        try:
            epoch_for_clarity = base.get("epoch", 0) if connected else int(time.time())
            lat_for_clarity = base.get("lat", float("nan"))
            lon_for_clarity = base.get("lon", float("nan"))
            orto_ocaso_txt = sunrise_sunset_label(float(lat_for_clarity), float(lon_for_clarity), float(epoch_for_clarity))
        except Exception:
            orto_ocaso_txt = t("observation.cards.radiation.sky_clarity.sunrise_sunset", sunrise="—", sunset="—")
        else:
            orto_ocaso_txt = _translate_sunrise_sunset_label(orto_ocaso_txt)
        clarity_sub = (
            f"<div style='font-size:0.85rem; opacity:0.75;'>{clarity_label}</div>"
            f"<div>{orto_ocaso_txt}</div>"
        )

        try:
            lat_for_sun = base.get("lat", float("nan"))
            lon_for_sun = base.get("lon", float("nan"))
            epoch_for_sun = base.get("epoch", 0) if connected else int(time.time())
            sun_altitude = solar_altitude_deg(float(lat_for_sun), float(epoch_for_sun), float(lon_for_sun))
            sun_altitude_max = max_solar_altitude_day_deg(float(lat_for_sun), float(epoch_for_sun), float(lon_for_sun))
        except Exception:
            sun_altitude = float("nan")
            sun_altitude_max = float("nan")

        sun_altitude_val = "—" if is_nan(sun_altitude) else f"{sun_altitude:.1f}"
        sun_altitude_max_txt = "—" if is_nan(sun_altitude_max) else f"{sun_altitude_max:.1f}°"
        sun_altitude_sub = f"<div>{t('observation.cards.radiation.sun_altitude.culmination')}: <b>{sun_altitude_max_txt}</b></div>"

        balance_label = _translate_balance_label(water_balance_label(balance))
        balance_sub = f"<div style='font-size:0.85rem; opacity:0.75; margin-top:2px;'>{balance_label}</div>"

        # Máximo 4 cards por fila.
        # Primera fila: Solar, UV, Dosis eritemática, ET0
        cards_radiation_row1 = [
            card(t("observation.cards.radiation.irradiance.title"), solar_val, radiation_unit_txt, icon_kind="solar", subtitle_html=solar_sub, uid="r1", dark=dark, tooltip_key="irradiancia"),
            card(t("observation.cards.radiation.uv_index.title"), uv_val, "", icon_kind="uv", subtitle_html=uv_sub, uid="r2", dark=dark, tooltip_key="índice uv"),
            card(t("observation.cards.radiation.erythemal_dose.title"), erythemal_dose_val, "SED", icon_kind="sed", subtitle_html=erythemal_dose_sub, uid="r3", dark=dark, tooltip_key="dosis eritemática"),
            card(t("observation.cards.radiation.et0_today.title"), et0_val, precip_unit_txt, icon_kind="et0", subtitle_html=et0_sub, uid="r4", dark=dark, tooltip_key="evapotranspiración hoy"),
        ]
        render_grid(cards_radiation_row1, cols=4)

        # Segunda fila: claridad, geometría solar y balance
        cards_radiation_row2 = [
            card(t("observation.cards.radiation.sky_clarity.title"), clarity_val, "%", icon_kind="clarity", subtitle_html=clarity_sub, uid="r5", dark=dark, tooltip_key="claridad del cielo"),
            card(t("observation.cards.radiation.sun_altitude.title"), sun_altitude_val, "°", icon_kind="sunalt", subtitle_html=sun_altitude_sub, uid="r6", dark=dark, tooltip_key="altura del sol"),
            card(t("observation.cards.radiation.water_balance_today.title"), balance_val, precip_unit_txt, icon_kind="balance", subtitle_html=balance_sub, uid="r7", dark=dark, tooltip_key="balance hídrico hoy"),
        ]
        render_grid(cards_radiation_row2, cols=4, extra_class="grid-row-spacing")

    # ============================================================
    # NIVEL 4 — GRÁFICOS
    # ============================================================
    
    # Definir colores según tema (disponible para todos los gráficos)
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

    if connected and has_chart_data:
        section_title(t("common.charts"))

        import pandas as pd
        import plotly.graph_objects as go
        
        # Obtener datos de gráficos del session_state
        chart_epochs = st.session_state.get("chart_epochs", [])
        chart_temps = st.session_state.get("chart_temps", [])
        chart_humidities = st.session_state.get("chart_humidities", [])
        chart_pressures = st.session_state.get("chart_pressures", [])
        chart_solar_radiations = st.session_state.get("chart_solar_radiations", [])
        chart_winds = st.session_state.get("chart_winds", [])
        
        logger.info(f"📊 [Gráficos] Datos disponibles: {len(chart_epochs)} epochs, {len(chart_temps)} temps, {len(chart_humidities)} humidities")

        # --- 1) Construir serie con datetimes reales
        dt_list = []
        temp_list = []
        for epoch, temp in zip(chart_epochs, chart_temps):
            dt = datetime.fromtimestamp(epoch)  # si fuera UTC: datetime.utcfromtimestamp(epoch)
            dt_list.append(dt)
            temp_list.append(temp)

        df_obs = pd.DataFrame({"dt": dt_list, "temp": temp_list}).sort_values("dt")

        # --- 1.5) Alinear timestamps a la rejilla (clave para que el reindex funcione)
        connection_type = st.session_state.get("connection_type", "")
        series_step_minutes = max(5, _infer_series_step_minutes(df_obs["dt"]))
        if connection_type == "METEOCAT":
            step_minutes = max(30, series_step_minutes)
        elif connection_type == "AEMET":
            step_minutes = max(10, series_step_minutes)
        else:
            step_minutes = 5
        connect_series_gaps = connection_type != "METEOCAT"
        df_obs["dt"] = pd.to_datetime(df_obs["dt"]).dt.floor(f"{step_minutes}min")

        # Si hay duplicados (varios puntos en el mismo tick), nos quedamos con el último
        df_obs = df_obs.groupby("dt", as_index=False)["temp"].last().sort_values("dt")

        # --- 2) Crear malla completa con rango específico por proveedor
        now_local = datetime.now()

        grid_inclusive = "both"

        # Mostrar siempre el día completo y dejar que la serie se "monte"
        # a medida que llegan observaciones.
        day_start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        data_start = day_start_today
        data_end = day_start_today + timedelta(days=1)
        grid_inclusive = "left"  # no incluir 24:00 del día siguiente

        # Guardar para uso en layout
        day_start = data_start
        day_end = data_end

        grid = pd.date_range(
            start=data_start,
            end=data_end,
            freq=f"{step_minutes}min",
            inclusive=grid_inclusive
        )

        # --- 3) Reindexar (ahora sí casan los timestamps)
        s = pd.Series(df_obs["temp"].values, index=pd.to_datetime(df_obs["dt"]))
        y = s.reindex(grid)  # sin rellenar; NaN = huecos
        y_display = y.apply(lambda value: convert_temperature(value, temp_unit_pref) if not is_nan(value) else float("nan"))

        # --- 4) Rango Y con padding inteligente
        y_valid = y_display.dropna()
        if len(y_valid) >= 2:
            temp_min = float(y_valid.min())
            temp_max = float(y_valid.max())
            span = max(1.0, temp_max - temp_min)
            pad = max(0.8, 0.15 * span)
            y_min = temp_min - pad
            y_max = temp_max + pad
        elif len(y_valid) == 1:
            v = float(y_valid.iloc[0])
            y_min, y_max = v - 2, v + 2
        else:
            y_min, y_max = 0, 30

        # --- 5) Gráfico de temperatura
        if int(y_display.notna().sum()) > 0:
            st.markdown(f"### {t('observation.cards.charts.temperature_heading')}")

            fig = go.Figure()

            fig.add_trace(go.Scatter(
                x=grid,
                y=y_display.values,              # <- pasar valores explícitos evita rarezas
                mode="lines",
                name=t("observation.cards.charts.temperature_name"),
                line=dict(color="rgb(255, 107, 107)", width=3),
                connectgaps=connect_series_gaps,
                fill="tozeroy",
                fillcolor="rgba(255, 107, 107, 0.1)"
            ))
            fig.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)

            fig.update_layout(
                title=dict(
                    text=(
                        t("observation.cards.charts.temperature_title_today")
                        if connection_type != "AEMET"
                        else t("observation.cards.charts.temperature_title_day")
                    ),
                    x=0.5,
                    xanchor="center",
                    font=dict(size=18, color=text_color)
                ),
                xaxis=dict(
                    title=dict(text=t("common.hour"), font=dict(color=text_color)),
                    type="date",
                    range=[day_start, day_end],
                    tickformat="%H:%M",
                    dtick=60 * 60 * 1000,   # 1h
                    gridcolor=grid_color,
                    showgrid=True,
                    tickfont=dict(color=text_color, size=x_tick_size)
                ),
                yaxis=dict(
                    title=dict(text=temp_unit_txt, font=dict(color=text_color)),
                    range=[y_min, y_max],
                    gridcolor=grid_color,
                    showgrid=True,
                    tickfont=dict(color=text_color, size=y_tick_size)
                ),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                hovermode="x unified",
                height=400,
                margin=dict(l=60, r=40, t=60, b=60),
                font=dict(family='system-ui, -apple-system, "Segoe UI", Roboto, Arial', color=text_color),

                annotations=[dict(
                    text="meteolabx.com",
                    xref="paper", yref="paper",
                    x=0.98, y=0.02,
                    xanchor="right", yanchor="bottom",
                    showarrow=False,
                    font=dict(size=10, color="rgba(128,128,128,0.5)")
                )]
            )

            _plotly_chart_stretch(fig, key=f"temp_graph_{theme_mode}")

        # Gráfico de presión de vapor solo para WU (AEMET no ofrece HR diezminutal fiable)
        if True:
            humidities_valid = [h for h in chart_humidities if not is_nan(h)]

            if len(humidities_valid) >= 1:
                st.markdown(f"### {t('observation.cards.charts.vapor_heading')}")

                from models.thermodynamics import e_s as calc_e_s, vapor_pressure

                vapor_times = []
                e_values = []
                e_sat_values = []

                for epoch, temp, rh in zip(chart_epochs, chart_temps, chart_humidities):
                    if is_nan(temp) or is_nan(rh):
                        continue

                    vapor_times.append(datetime.fromtimestamp(epoch))
                    e_values.append(vapor_pressure(temp, rh))
                    e_sat_values.append(calc_e_s(temp))

                df_vapor = pd.DataFrame({
                    "dt": vapor_times,
                    "e": e_values,
                    "e_s": e_sat_values
                })
                if not df_vapor.empty:
                    df_vapor["dt"] = pd.to_datetime(df_vapor["dt"]).dt.floor(f"{step_minutes}min")
                    df_vapor = df_vapor.groupby("dt", as_index=False).last()

                    s_e = pd.Series(df_vapor["e"].values, index=pd.to_datetime(df_vapor["dt"]))
                    s_e_s = pd.Series(df_vapor["e_s"].values, index=pd.to_datetime(df_vapor["dt"]))
                    y_e = s_e.reindex(grid)
                    y_e_s = s_e_s.reindex(grid)
                    y_e_display = y_e.apply(lambda value: convert_pressure(value, pressure_unit_pref) if not is_nan(value) else float("nan"))
                    y_e_s_display = y_e_s.apply(lambda value: convert_pressure(value, pressure_unit_pref) if not is_nan(value) else float("nan"))

                    fig_vapor = go.Figure()
                    vapor_mode = "lines+markers" if int(y_e_display.notna().sum()) < 8 else "lines"
                    sat_mode = "lines+markers" if int(y_e_s_display.notna().sum()) < 8 else "lines"

                    fig_vapor.add_trace(go.Scatter(
                        x=grid,
                        y=y_e_display.values,
                        mode=vapor_mode,
                        name=t("observation.cards.charts.vapor_line"),
                        line=dict(color="rgb(52, 152, 219)", width=3),
                        marker=dict(size=4, color="rgb(52, 152, 219)"),
                        connectgaps=connect_series_gaps,
                    ))
                    fig_vapor.add_trace(go.Scatter(
                        x=grid,
                        y=y_e_s_display.values,
                        mode=sat_mode,
                        name=t("observation.cards.charts.vapor_saturation_line"),
                        line=dict(color="rgb(231, 76, 60)", width=2, dash="dash"),
                        marker=dict(size=4, color="rgb(231, 76, 60)"),
                        connectgaps=connect_series_gaps,
                    ))
                    fig_vapor.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                    fig_vapor.update_layout(
                        title=dict(
                            text=t("observation.cards.charts.vapor_title"),
                            x=0.5,
                            xanchor="center",
                            font=dict(size=18, color=text_color)
                        ),
                        xaxis=dict(
                            title=dict(text=t("common.hour"), font=dict(color=text_color)),
                            type="date",
                            range=[day_start, day_end],
                            tickformat="%H:%M",
                            dtick=60 * 60 * 1000,
                            showgrid=True,
                            gridcolor=grid_color,
                            tickfont=dict(color=text_color, size=x_tick_size)
                        ),
                        yaxis=dict(
                            title=dict(text=pressure_unit_txt, font=dict(color=text_color)),
                            showgrid=True,
                            gridcolor=grid_color,
                            tickfont=dict(color=text_color, size=y_tick_size)
                        ),
                        hovermode="x unified",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color=text_color),
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
                        margin=dict(l=60, r=40, t=60, b=60),
                        height=400,
                        annotations=[dict(
                            text="meteolabx.com",
                            xref="paper", yref="paper",
                            x=0.98, y=0.02,
                            xanchor="right", yanchor="bottom",
                            showarrow=False,
                            font=dict(size=10, color="rgba(128,128,128,0.5)")
                        )],
                    )
                    _plotly_chart_stretch(
                        fig_vapor,
                        key=f"vapor_graph_{theme_mode}",
                        config={"displayModeBar": False},
                    )
            # --- Gráfico de viento y rosa de viento (WU/AEMET) ---
            wind_times = []
            wind_vals = []
            gust_vals = []
            dir_vals = []

            chart_gusts = st.session_state.get("chart_gusts", [])
            chart_wind_dirs = st.session_state.get("chart_wind_dirs", [])

            for i, epoch in enumerate(chart_epochs):
                w = chart_winds[i] if i < len(chart_winds) else float("nan")
                g = chart_gusts[i] if i < len(chart_gusts) else float("nan")
                d = chart_wind_dirs[i] if i < len(chart_wind_dirs) else float("nan")

                # Excluir muestras sin información de viento útil
                if is_nan(w) and is_nan(g):
                    continue

                wind_times.append(datetime.fromtimestamp(epoch))
                wind_vals.append(float(w) if not is_nan(w) else float("nan"))
                gust_vals.append(float(g) if not is_nan(g) else float("nan"))
                dir_vals.append(float(d) if not is_nan(d) else float("nan"))

            if len(wind_times) >= 1:
                st.markdown(f"### {t('observation.cards.charts.wind_heading')}")

                df_wind = pd.DataFrame({
                    "dt": wind_times,
                    "wind": wind_vals,
                    "gust": gust_vals,
                    "dir": dir_vals,
                }).sort_values("dt")

                df_wind["dt"] = pd.to_datetime(df_wind["dt"]).dt.floor(f"{step_minutes}min")
                df_wind = df_wind.groupby("dt", as_index=False).last()

                # Limitar análisis de viento/rosa al mismo rango mostrado en el gráfico de "Hoy".
                range_start = pd.Timestamp(day_start)
                range_end = pd.Timestamp(day_end)
                df_wind_view = df_wind[(df_wind["dt"] >= range_start) & (df_wind["dt"] < range_end)].copy()

                if df_wind_view.empty:
                    st.info(f"ℹ️ {t('observation.cards.charts.wind_no_data_today')}")
                    df_wind_view = pd.DataFrame(columns=["dt", "wind", "gust", "dir"])

                # Algunas estaciones AEMET no reportan VV útil (todo 0) pero sí rachas.
                wind_non_nan = [float(v) for v in df_wind_view["wind"].tolist() if not is_nan(v)]
                gust_non_nan = [float(v) for v in df_wind_view["gust"].tolist() if not is_nan(v)]
                vv_all_zero = (len(wind_non_nan) > 0) and (max(abs(v) for v in wind_non_nan) < 0.1)
                gust_has_signal = (len(gust_non_nan) > 0) and (max(gust_non_nan) >= 1.0)
                if _get_aemet_service().is_aemet_connection() and vv_all_zero and gust_has_signal:
                    df_wind_view["wind"] = float("nan")
                    st.caption(f"ℹ️ {t('observation.cards.charts.wind_no_mean')}")

                s_wind = pd.Series(df_wind_view["wind"].values, index=pd.to_datetime(df_wind_view["dt"]))
                s_gust = pd.Series(df_wind_view["gust"].values, index=pd.to_datetime(df_wind_view["dt"]))
                # No mostrar dirección cuando la muestra está en calma usando la
                # mejor referencia disponible (viento medio o racha).
                df_wind_view["dir_plot"] = df_wind_view["dir"]
                speed_ref = df_wind_view[["wind", "gust"]].max(axis=1, skipna=True)
                calm_mask = speed_ref.notna() & (speed_ref < 1.0)
                df_wind_view.loc[calm_mask, "dir_plot"] = float("nan")
                s_dir = pd.Series(df_wind_view["dir_plot"].values, index=pd.to_datetime(df_wind_view["dt"]))
                y_wind = s_wind.reindex(grid)
                y_gust = s_gust.reindex(grid)
                y_dir = s_dir.reindex(grid)
                y_wind_display = y_wind.apply(lambda value: convert_wind(value, wind_unit_pref) if not is_nan(value) else float("nan"))
                y_gust_display = y_gust.apply(lambda value: convert_wind(value, wind_unit_pref) if not is_nan(value) else float("nan"))

                fig_wind = go.Figure()
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_wind_display.values,
                    mode="lines+markers" if int(y_wind_display.notna().sum()) < 8 else "lines",
                    name=t("observation.cards.charts.wind_line"),
                    line=dict(color="rgb(74, 201, 240)", width=2.8),
                    marker=dict(size=4, color="rgb(74, 201, 240)"),
                    connectgaps=connect_series_gaps,
                ))
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_gust_display.values,
                    mode="lines+markers" if int(y_gust_display.notna().sum()) < 8 else "lines",
                    name=t("observation.cards.charts.gust_line"),
                    line=dict(color="rgb(255, 170, 65)", width=2.4, dash="dot"),
                    marker=dict(size=4, color="rgb(255, 170, 65)"),
                    connectgaps=connect_series_gaps,
                ))
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_dir.values,
                    mode="markers",
                    name=t("observation.cards.charts.direction_line"),
                    marker=dict(color="rgba(120, 170, 255, 0.85)", size=5),
                    yaxis="y2",
                    hovertemplate="Dirección: %{y:.0f}°<extra></extra>",
                ))
                fig_wind.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                fig_wind.update_layout(
                    title=dict(text=t("observation.cards.charts.wind_heading"), x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                    xaxis=dict(
                        title=dict(text=t("common.hour"), font=dict(color=text_color)),
                        type="date",
                        range=[day_start, day_end],
                        tickformat="%H:%M",
                        dtick=60 * 60 * 1000,
                        showgrid=True,
                        gridcolor=grid_color,
                        tickfont=dict(color=text_color, size=x_tick_size),
                    ),
                    yaxis=dict(
                        title=dict(text=wind_unit_txt, font=dict(color=text_color)),
                        showgrid=True,
                        gridcolor=grid_color,
                        tickfont=dict(color=text_color, size=y_tick_size),
                        rangemode="tozero",
                    ),
                    yaxis2=dict(
                        title=dict(text=t("observation.cards.charts.direction_axis"), font=dict(color=text_color)),
                        overlaying="y",
                        side="right",
                        range=[0, 360],
                        tickvals=[0, 45, 90, 135, 180, 225, 270, 315, 360],
                        ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"],
                        tickfont=dict(color=text_color, size=right_y_tick_size),
                        ticklabelposition="inside",
                        ticklabelstandoff=10,
                        showgrid=False,
                    ),
                    hovermode="x unified",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=text_color),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
                    margin=dict(l=60, r=40, t=60, b=60),
                    height=400,
                    annotations=[dict(
                        text="meteolabx.com",
                        xref="paper", yref="paper",
                        x=0.98, y=0.02,
                        xanchor="right", yanchor="bottom",
                        showarrow=False,
                        font=dict(size=10, color="rgba(128,128,128,0.5)"),
                    )],
                )
                _plotly_chart_stretch(fig_wind, key=f"wind_graph_{theme_mode}")

                # Rosa de viento 16 rumbos: excluir dirección cuando viento y racha son ambos 0.0
                wind_rose_stats = _wind_rose_stats_cached(
                    tuple(_safe_float(value) for value in df_wind_view["wind"].tolist()),
                    tuple(_safe_float(value) for value in df_wind_view["gust"].tolist()),
                    tuple(_safe_float(value) for value in df_wind_view["dir"].tolist()),
                )
                sectors16 = wind_rose_stats["sectors16"]
                counts = wind_rose_stats["counts"]
                calm = int(wind_rose_stats["calm"])
                total_samples = int(wind_rose_stats["total_samples"])
                dir_total = int(wind_rose_stats["dir_total"])
                dominant_dir = wind_rose_stats["dominant_dir"]

                if dir_total > 0:
                    st.markdown(f"### {t('observation.cards.charts.wind_rose_heading')}")

                    dir_pcts = wind_rose_stats["dir_pcts"]
                    theta_deg = [i * 22.5 for i in range(16)]
                    r_pct = [dir_pcts[s] for s in sectors16]

                    if dominant_dir is not None:
                        rose_colors = [
                            "rgba(255, 170, 65, 0.90)" if s == dominant_dir else "rgba(102, 188, 255, 0.75)"
                            for s in sectors16
                        ]
                    else:
                        rose_colors = ["rgba(102, 188, 255, 0.75)"] * 16

                    col_rose, col_stats = st.columns([0.62, 0.38], gap="large")

                    with col_rose:
                        fig_rose = go.Figure()
                        fig_rose.add_trace(go.Barpolar(
                            r=r_pct,
                            theta=theta_deg,
                            width=[20.0] * 16,
                            marker_color=rose_colors,
                            marker_line_color="rgba(102, 188, 255, 1)",
                            marker_line_width=1,
                            opacity=0.95,
                            customdata=sectors16,
                            hovertemplate="%{customdata}: %{r:.1f}%<extra></extra>",
                            name=t("observation.cards.charts.wind_rose_frequency"),
                        ))

                        radial_max = max(10.0, math.ceil(max(r_pct) / 5.0) * 5.0)

                        fig_rose.update_layout(
                            title=dict(text=t("observation.cards.charts.wind_rose_title"), x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
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
                            annotations=[dict(
                                text="meteolabx.com",
                                xref="paper", yref="paper",
                                x=0.98, y=0.02,
                                xanchor="right", yanchor="bottom",
                                showarrow=False,
                                font=dict(size=10, color="rgba(128,128,128,0.5)"),
                            )],
                        )
                        _plotly_chart_stretch(fig_rose, key=f"wind_rose_{theme_mode}")

                    with col_stats:
                        calm_pct = (100.0 * calm / total_samples) if total_samples > 0 else 0.0
                        dom_pct = (100.0 * counts[dominant_dir] / dir_total) if (dominant_dir is not None and dir_total > 0) else 0.0

                        st.markdown(f"**{t('observation.cards.charts.wind_rose_samples')}:** {total_samples}")
                        st.markdown(f"**{t('observation.cards.charts.wind_rose_calm')}:** {calm_pct:.1f}% ({calm})")
                        if dominant_dir is not None:
                            st.markdown(f"**{t('observation.cards.charts.wind_rose_dominant')}:** **{dominant_dir} ({dom_pct:.1f}%)**")
                        else:
                            st.markdown(f"**{t('observation.cards.charts.wind_rose_dominant')}:** —")

                        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                        rose_items = []
                        for s in sectors16:
                            txt = f"{s}: {dir_pcts[s]:.1f}% ({counts[s]})"
                            item_class = "rose-stat-item is-dominant" if s == dominant_dir else "rose-stat-item"
                            rose_items.append(
                                f"<div class='{item_class}'>{html.escape(txt)}</div>"
                            )
                        st.markdown(
                            "<div class='rose-stats-grid'>"
                            + "".join(rose_items)
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    dir_non_nan = sum(1 for v in df_wind["dir"].tolist() if not is_nan(v))
                    st.warning(
                        t(
                            "observation.cards.info.wind_rose_unavailable",
                            valid_direction=dir_non_nan,
                            calm=calm,
                        )
                    )

            # Irradiancia intradía: medida vs teórica de cielo despejado
            solar_valid = [float(v) for v in chart_solar_radiations if not is_nan(v)]
            try:
                lat_clarity = float(
                    base.get(
                        "lat",
                        st.session_state.get(
                            "provider_station_lat",
                            st.session_state.get("station_lat", float("nan")),
                        ),
                    )
                )
            except Exception:
                lat_clarity = float("nan")
            try:
                lon_clarity = float(
                    base.get(
                        "lon",
                        st.session_state.get(
                            "provider_station_lon",
                            st.session_state.get("station_lon", float("nan")),
                        ),
                    )
                )
            except Exception:
                lon_clarity = float("nan")
            try:
                elevation_clarity = float(
                    z if not is_nan(z) else st.session_state.get("station_elevation", float("nan"))
                )
            except Exception:
                elevation_clarity = float("nan")
            if len(solar_valid) >= 2 and not is_nan(lat_clarity) and not is_nan(lon_clarity):
                from models.radiation import solar_radiation_max_wm2, sunrise_sunset_datetimes

                irradiance_times = []
                measured_vals = []
                theoretical_vals = []
                irradiance_epochs = []
                for epoch, solar_i in zip(chart_epochs, chart_solar_radiations):
                    if is_nan(solar_i):
                        continue

                    theoretical_i = solar_radiation_max_wm2(
                        latitude_deg=float(lat_clarity),
                        elevation_m=float(elevation_clarity if not is_nan(elevation_clarity) else 0.0),
                        timestamp=float(epoch),
                        longitude_deg=float(lon_clarity),
                        period_minutes=1.0,
                    )
                    if is_nan(theoretical_i):
                        continue

                    irradiance_times.append(datetime.fromtimestamp(float(epoch)))
                    measured_vals.append(float(solar_i))
                    theoretical_vals.append(float(theoretical_i))
                    irradiance_epochs.append(int(epoch))

                if len(irradiance_times) >= 3:
                    df_irradiance = pd.DataFrame({
                        "dt": irradiance_times,
                        "measured": measured_vals,
                        "theoretical": theoretical_vals,
                    }).sort_values("dt")
                    df_irradiance["dt"] = (
                        pd.to_datetime(df_irradiance["dt"], errors="coerce")
                        .dt.tz_localize(None)
                        .dt.floor("5min")
                    )
                    df_irradiance = df_irradiance.dropna(subset=["dt"])
                    df_irradiance = df_irradiance.groupby("dt", as_index=False).last()

                    ref_epoch = float(irradiance_epochs[-1]) if irradiance_epochs else float(base.get("epoch", time.time()))
                    sunrise_dt, sunset_dt = sunrise_sunset_datetimes(
                        float(lat_clarity), float(lon_clarity), ref_epoch
                    )
                    if sunrise_dt is None or sunset_dt is None:
                        sunrise_dt = day_start
                        sunset_dt = day_end

                    range_start = pd.Timestamp(sunrise_dt).tz_localize(None)
                    range_end = pd.Timestamp(sunset_dt).tz_localize(None)
                    df_irradiance_view = df_irradiance[
                        (df_irradiance["dt"] >= range_start) & (df_irradiance["dt"] <= range_end)
                    ].copy()
                    if len(df_irradiance_view) < 2:
                        # Fallback defensivo: usar el día de la propia serie para no dejar el gráfico vacío.
                        series_day_start = pd.Timestamp(df_irradiance["dt"].min()).replace(hour=0, minute=0, second=0, microsecond=0)
                        series_day_end = pd.Timestamp(df_irradiance["dt"].min()).replace(hour=23, minute=59, second=59, microsecond=0)
                        df_irradiance_view = df_irradiance[
                            (df_irradiance["dt"] >= series_day_start) & (df_irradiance["dt"] <= series_day_end)
                        ].copy()
                        range_start = series_day_start
                        range_end = series_day_end

                    if len(df_irradiance_view) >= 2:
                        st.markdown(f"### {t('observation.cards.charts.irradiance_heading')}")

                        x_irr = pd.to_datetime(df_irradiance_view["dt"])
                        y_measured = pd.to_numeric(df_irradiance_view["measured"], errors="coerce").apply(
                            lambda value: convert_radiation(value, radiation_unit_pref) if not is_nan(value) else float("nan")
                        )
                        y_theoretical = pd.to_numeric(df_irradiance_view["theoretical"], errors="coerce").apply(
                            lambda value: convert_radiation(value, radiation_unit_pref) if not is_nan(value) else float("nan")
                        )

                        ymax_candidates = [
                            float(v)
                            for v in list(y_measured.dropna()) + list(y_theoretical.dropna())
                            if not is_nan(float(v))
                        ]
                        y_max_irr = max(ymax_candidates) if ymax_candidates else 1200.0
                        y_max_irr = max(400.0, math.ceil(y_max_irr / 100.0) * 100.0)

                        fig_irradiance = go.Figure()
                        fig_irradiance.add_trace(go.Scatter(
                            x=x_irr,
                            y=y_measured,
                            mode="lines",
                            name=t("observation.cards.charts.irradiance_measured"),
                            line=dict(color="rgb(96, 165, 250)", width=3),
                            connectgaps=True,
                            fill="tozeroy",
                            fillcolor="rgba(96, 165, 250, 0.10)",
                        ))
                        fig_irradiance.add_trace(go.Scatter(
                            x=x_irr,
                            y=y_theoretical,
                            mode="lines",
                            name=t("observation.cards.charts.irradiance_theoretical"),
                            line=dict(color="rgb(255, 184, 64)", width=2.5, dash="dash"),
                            connectgaps=True,
                        ))
                        fig_irradiance.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                        fig_irradiance.update_layout(
                            title=dict(text=t("observation.cards.charts.irradiance_title"), x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(
                                title=dict(text=t("observation.cards.charts.irradiance_hour_axis"), font=dict(color=text_color)),
                                type="date",
                                range=[range_start, range_end],
                                tickformat="%H:%M",
                                dtick=60 * 60 * 1000,
                                showgrid=True,
                                gridcolor=grid_color,
                                tickfont=dict(color=text_color, size=x_tick_size),
                            ),
                            yaxis=dict(
                                title=dict(text=radiation_unit_txt, font=dict(color=text_color)),
                                range=[0, y_max_irr],
                                showgrid=True,
                                gridcolor=grid_color,
                                tickfont=dict(color=text_color, size=y_tick_size),
                            ),
                            hovermode="x unified",
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(color=text_color),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
                            margin=dict(l=60, r=40, t=60, b=60),
                            height=400,
                            annotations=[dict(
                                text="meteolabx.com",
                                xref="paper", yref="paper",
                                x=0.98, y=0.02,
                                xanchor="right", yanchor="bottom",
                                showarrow=False,
                                font=dict(size=10, color="rgba(128,128,128,0.5)"),
                            )],
                        )
                        _plotly_chart_stretch(fig_irradiance, key=f"irradiance_graph_{theme_mode}")

    if connected and not has_chart_data:
        section_title(t("common.charts"))
        if _get_aemet_service().is_aemet_connection():
            st.warning(t("observation.cards.info.invalid_aemet_tenmin"))
        else:
            st.info(t("observation.cards.info.no_series"))

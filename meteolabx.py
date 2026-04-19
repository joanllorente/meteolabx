"""
MeteoLabx - Panel meteorológico avanzado
Aplicación principal
"""
import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(
    page_title="MeteoLabX",
    page_icon="favicon.png",
    layout="wide",
    initial_sidebar_state="collapsed"  # Sidebar colapsada por defecto en móvil
)
import time
import math
import logging
import html
import json
import os
import hashlib
from typing import Optional
from datetime import datetime, timedelta

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES, LS_AUTOCONNECT, RD
from utils import html_clean, is_nan, es_datetime_from_epoch, age_string, fmt_hpa, month_name, t
from utils.storage import (
    set_local_storage,
    set_stored_autoconnect_target,
    get_stored_autoconnect,
    get_stored_autoconnect_target,
)
from utils.units import (
    convert_precip,
    convert_pressure,
    convert_radiation,
    convert_radiation_energy,
    convert_temperature,
    convert_temperature_delta,
    convert_wind,
    format_precip,
    format_pressure,
    format_radiation,
    format_radiation_energy,
    format_temperature,
    format_temperature_delta,
    format_wind,
    normalize_unit_preferences,
    precip_unit_label,
    pressure_unit_label,
    radiation_energy_unit_label,
    radiation_unit_label,
    temperature_unit_label,
    wind_unit_label,
)
from api import WuError, fetch_wu_current_session_cached, fetch_daily_timeseries, fetch_hourly_7day_session_cached
from models.thermodynamics import (
    e_s, vapor_pressure, dewpoint_from_vapor_pressure,
    mixing_ratio, specific_humidity, absolute_humidity,
    potential_temperature, virtual_temperature, equivalent_temperature, equivalent_potential_temperature,
    wet_bulb_celsius, msl_to_absolute, air_density, lcl_height,
    apparent_temperature, heat_index_rothfusz,
)
from models.radiation import (
    sky_clarity_label, uv_index_label, water_balance, water_balance_label,
)
from services import (
    rain_rates_from_total, rain_intensity_label, reset_rain_history,
    init_pressure_history, push_pressure, pressure_trend_3h
)
from services.wu_calibration import (
    apply_wu_current_calibration,
    apply_wu_series_calibration,
    default_wu_calibration,
    detect_wu_sensor_presence,
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, render_sidebar
)

from components.station_selector import render_station_selector
from components.browser_geolocation import get_browser_geolocation
from providers import search_nearby_stations

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_frost_service():
    """Importa Frost bajo demanda para aligerar el arranque inicial."""
    from services import frost as frost_service
    return frost_service


def _get_meteofrance_service():
    """Importa Meteo-France bajo demanda para aligerar el arranque inicial."""
    from services import meteofrance as meteofrance_service
    return meteofrance_service


def _get_climograms_service():
    """Importa cálculos de climogramas bajo demanda para aligerar el arranque inicial."""
    from services import climograms as climograms_service
    return climograms_service


def _get_aemet_service():
    """Importa AEMET bajo demanda para aligerar el arranque inicial."""
    from services import aemet as aemet_service
    return aemet_service


def _get_meteocat_service():
    """Importa Meteocat bajo demanda para aligerar el arranque inicial."""
    from services import meteocat as meteocat_service
    return meteocat_service


def _get_euskalmet_service():
    """Importa Euskalmet bajo demanda para aligerar el arranque inicial."""
    from services import euskalmet as euskalmet_service
    return euskalmet_service


def _get_meteogalicia_service():
    """Importa MeteoGalicia bajo demanda para aligerar el arranque inicial."""
    from services import meteogalicia as meteogalicia_service
    return meteogalicia_service


def _get_poem_service():
    """Importa POEM bajo demanda para aligerar el arranque inicial."""
    from services import poem as poem_service
    return poem_service


def _get_nws_service():
    """Importa NWS bajo demanda para aligerar el arranque inicial."""
    from services import nws as nws_service
    return nws_service


def _browser_viewport_width() -> int:
    """Devuelve el ancho CSS del viewport del navegador si está disponible."""
    raw = st.query_params.get("_vw", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    try:
        return max(0, int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return 0


def _first_valid_float(*values: object, default: float = float("nan")) -> float:
    """Devuelve el primer float válido no-NaN de una lista heterogénea."""
    for value in values:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if not is_nan(candidate):
            return candidate
    return default


def _build_demo_radiation_series(
    current_solar: float,
    current_uv: float,
    *,
    now_dt: Optional[datetime] = None,
) -> dict:
    """Serie diaria sintética para visualizar radiación/UV en modo DEMO."""
    now_local = now_dt or datetime.now().astimezone()
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    step_seconds = 5 * 60
    total_steps = max(2, int((now_local - day_start).total_seconds() // step_seconds) + 1)

    # El slider del DEMO representa una intensidad "típica" del tramo fuerte del día,
    # no un valor a extrapolar desde la hora real actual. Así evitamos picos absurdos
    # cuando se prueba de noche con UVI manual.
    solar_noon_hour = 14.0
    sigma_hours = 2.8
    solar_peak = min(1200.0, max(0.0, float(current_solar) if current_solar > 0 else 850.0))
    uv_peak = min(15.0, max(0.0, float(current_uv) if current_uv > 0 else 8.5))

    epochs = []
    solar_radiations = []
    uv_indexes = []
    temps = []
    humidities = []
    winds = []
    gusts = []
    wind_dirs = []

    for step_idx in range(total_steps):
        point_dt = day_start + timedelta(seconds=step_idx * step_seconds)
        point_hour = point_dt.hour + (point_dt.minute / 60.0)
        diurnal_shape = math.exp(-((point_hour - solar_noon_hour) ** 2) / (2.0 * sigma_hours ** 2))

        solar_val = solar_peak * (diurnal_shape ** 1.02)
        uv_val = uv_peak * (diurnal_shape ** 1.18)
        if diurnal_shape < 0.08:
            solar_val = 0.0
        if diurnal_shape < 0.10:
            uv_val = 0.0

        # Variables auxiliares plausibles para que ET0 y balance también se vean.
        temp_shape = math.exp(-((point_hour - 16.0) ** 2) / (2.0 * 4.6 ** 2))
        rh_shape = math.exp(-((point_hour - 14.5) ** 2) / (2.0 * 4.0 ** 2))
        wind_shape = math.exp(-((point_hour - 15.5) ** 2) / (2.0 * 4.8 ** 2))

        epochs.append(int(point_dt.timestamp()))
        solar_radiations.append(float(max(0.0, solar_val)))
        uv_indexes.append(float(max(0.0, uv_val)))
        temps.append(float(14.5 + (11.0 * temp_shape)))
        humidities.append(float(max(28.0, min(92.0, 82.0 - (30.0 * rh_shape)))))
        winds.append(float(6.0 + (8.0 * wind_shape)))
        gusts.append(float(8.5 + (11.0 * wind_shape)))
        wind_dirs.append(210.0)

    return {
        "epochs": epochs,
        "solar_radiations": solar_radiations,
        "uv_indexes": uv_indexes,
        "temps": temps,
        "humidities": humidities,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": wind_dirs,
        "has_data": bool(epochs),
    }


def _is_small_mobile_client() -> bool:
    """Heurística de cliente móvil pequeño basada en viewport y user-agent."""
    viewport_width = _browser_viewport_width()
    if 0 < viewport_width <= 600:
        return True

    try:
        headers = getattr(st.context, "headers", {}) or {}
    except Exception:
        headers = {}

    user_agent = str(headers.get("user-agent", "")).lower()
    if not user_agent:
        return False

    mobile_tokens = (
        "iphone",
        "android",
        "mobile",
        "ipad",
        "ipod",
    )
    return any(token in user_agent for token in mobile_tokens)


def _apply_compact_plotly_layout(fig) -> None:
    """Compacta un gráfico temporal Plotly con menos ruido visual."""
    fig.update_layout(
        margin=dict(l=8, r=8, t=52, b=24),
    )

    def _compact_xaxis(axis):
        axis_type = getattr(axis, "type", None)
        updates = {
            "title": None,
            "tickangle": 0,
            "automargin": True,
            "nticks": 7,
            "tickfont": dict(size=11),
        }
        if axis_type == "date":
            updates["dtick"] = 4 * 60 * 60 * 1000
            updates["tickformat"] = "%H:%M"
        axis.update(**updates)

    def _compact_yaxis(axis):
        axis.update(
            title=None,
            automargin=True,
            tickfont=dict(size=11),
        )

    fig.for_each_xaxis(_compact_xaxis)
    fig.for_each_yaxis(_compact_yaxis)


def _compact_plotly_for_mobile(fig) -> None:
    """Compacta gráficos Plotly en pantallas pequeñas."""
    if not _is_small_mobile_client():
        return
    _apply_compact_plotly_layout(fig)


def _plotly_chart_stretch(fig, key: str, config: Optional[dict] = None, compact: bool = False):
    """Renderiza Plotly ocupando todo el ancho del contenedor."""
    if compact:
        _apply_compact_plotly_layout(fig)
    else:
        _compact_plotly_for_mobile(fig)
    cfg = config if isinstance(config, dict) else {}
    st.plotly_chart(fig, use_container_width=True, key=key, config=cfg)


def _render_neutral_info_note(message: str, title: Optional[str] = None) -> None:
    """Muestra una nota informativa neutra sin apariencia de error."""
    safe_message = html.escape(str(message))
    safe_title = html.escape(str(title or t("common.information")))
    st.markdown(
        html_clean(
            f"""
            <div style="
                margin: 0.3rem 0 0.95rem 0;
                padding: 0.9rem 1rem;
                border-radius: 14px;
                border: 1px solid rgba(127, 127, 127, 0.18);
                background: rgba(127, 127, 127, 0.08);
                color: var(--text);
                box-shadow: none;
            ">
                <div style="font-weight: 700; margin-bottom: 0.2rem;">{safe_title}</div>
                <div style="opacity: 0.88;">{safe_message}</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def _translate_pressure_trend_label(label: str) -> str:
    mapping = {
        "Estable": "stable",
        "Subiendo rápido": "rising_fast",
        "Subiendo": "rising",
        "Bajando rápido": "falling_fast",
        "Bajando": "falling",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.pressure.{key}") if key else str(label or "—")


def _translate_rain_intensity_label(label: str) -> str:
    mapping = {
        "Sin precipitación": "no_precip",
        "Traza de precipitación": "trace",
        "Lluvia muy débil": "very_light",
        "Lluvia débil": "light",
        "Lluvia ligera": "light_moderate",
        "Lluvia moderada": "moderate",
        "Lluvia fuerte": "heavy",
        "Lluvia muy fuerte": "very_heavy",
        "Lluvia torrencial": "torrential",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.rain.{key}") if key else str(label or "—")


def _translate_clarity_label(label: str) -> str:
    mapping = {
        "Despejado": "clear",
        "Poco nuboso": "mostly_clear",
        "Parcialmente nuboso": "partly_cloudy",
        "Nuboso": "cloudy",
        "Muy nuboso": "very_cloudy",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.clarity.{key}") if key else str(label or "—")


def _translate_balance_label(label: str) -> str:
    mapping = {
        "Superávit": "surplus",
        "Positivo": "positive",
        "Equilibrio": "balance",
        "Déficit": "deficit",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.balance.{key}") if key else str(label or "—")


def _translate_sunrise_sunset_label(label: str) -> str:
    text = str(label or "").strip()
    if not text or "·" not in text:
        return text
    left, right = [part.strip() for part in text.split("·", 1)]
    sunrise = left.replace("Orto", "").replace("Sunrise", "").strip()
    sunset = right.replace("Ocaso", "").replace("Sunset", "").strip()
    if not sunrise and not sunset:
        return text
    return t("observation.cards.radiation.sky_clarity.sunrise_sunset", sunrise=sunrise, sunset=sunset)


def _inject_mobile_plotly_compactor() -> None:
    """Compacta gráficos Plotly solo en viewports pequeños desde el DOM padre."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          const plotlyApi = host.Plotly;
          if (!doc || !plotlyApi) return;

          function isSmallViewport() {
            const vw = Math.round(host.innerWidth || doc.documentElement.clientWidth || 0);
            return vw > 0 && vw <= 600;
          }

          function titleText(axis) {
            if (!axis || axis.title == null) return "";
            if (typeof axis.title === "string") return axis.title;
            return axis.title.text || "";
          }

          function captureOriginal(plot) {
            if (plot.__mlbxOriginalLayout) return plot.__mlbxOriginalLayout;
            const layout = plot.layout || {};
            plot.__mlbxOriginalLayout = {
              margin: {
                l: layout.margin && layout.margin.l != null ? layout.margin.l : 60,
                r: layout.margin && layout.margin.r != null ? layout.margin.r : 40,
                t: layout.margin && layout.margin.t != null ? layout.margin.t : 60,
                b: layout.margin && layout.margin.b != null ? layout.margin.b : 60
              },
              xaxis: {
                title: titleText(layout.xaxis),
                dtick: layout.xaxis && layout.xaxis.dtick != null ? layout.xaxis.dtick : null,
                tickformat: layout.xaxis && layout.xaxis.tickformat != null ? layout.xaxis.tickformat : null,
                tickangle: layout.xaxis && layout.xaxis.tickangle != null ? layout.xaxis.tickangle : 0,
                automargin: !!(layout.xaxis && layout.xaxis.automargin),
                nticks: layout.xaxis && layout.xaxis.nticks != null ? layout.xaxis.nticks : null,
                tickfontSize: layout.xaxis && layout.xaxis.tickfont && layout.xaxis.tickfont.size != null ? layout.xaxis.tickfont.size : null
              },
              yaxis: {
                title: titleText(layout.yaxis),
                automargin: !!(layout.yaxis && layout.yaxis.automargin),
                tickfontSize: layout.yaxis && layout.yaxis.tickfont && layout.yaxis.tickfont.size != null ? layout.yaxis.tickfont.size : null
              },
              yaxis2: {
                title: titleText(layout.yaxis2),
                automargin: !!(layout.yaxis2 && layout.yaxis2.automargin),
                tickfontSize: layout.yaxis2 && layout.yaxis2.tickfont && layout.yaxis2.tickfont.size != null ? layout.yaxis2.tickfont.size : null
              }
            };
            return plot.__mlbxOriginalLayout;
          }

          function compactPlot(plot) {
            if (!plot || !plot.layout || !plot.layout.xaxis) return;
            const original = captureOriginal(plot);
            if (plot.dataset.mlbxCompactMode === "mobile") return;
            plotlyApi.relayout(plot, {
              "margin.l": 8,
              "margin.r": 8,
              "margin.t": 52,
              "margin.b": 24,
              "xaxis.title.text": "",
              "xaxis.dtick": 4 * 60 * 60 * 1000,
              "xaxis.tickformat": "%H:%M",
              "xaxis.tickangle": 0,
              "xaxis.automargin": true,
              "xaxis.nticks": 7,
              "xaxis.tickfont.size": 11,
              "yaxis.title.text": "",
              "yaxis.automargin": true,
              "yaxis.tickfont.size": 11,
              "yaxis2.title.text": "",
              "yaxis2.automargin": true,
              "yaxis2.tickfont.size": 11
            }).then(function () {
              plot.dataset.mlbxCompactMode = "mobile";
            }).catch(function () {});
          }

          function restorePlot(plot) {
            const original = plot && plot.__mlbxOriginalLayout;
            if (!plot || !original || plot.dataset.mlbxCompactMode !== "mobile") return;
            plotlyApi.relayout(plot, {
              "margin.l": original.margin.l,
              "margin.r": original.margin.r,
              "margin.t": original.margin.t,
              "margin.b": original.margin.b,
              "xaxis.title.text": original.xaxis.title,
              "xaxis.dtick": original.xaxis.dtick,
              "xaxis.tickformat": original.xaxis.tickformat,
              "xaxis.tickangle": original.xaxis.tickangle,
              "xaxis.automargin": original.xaxis.automargin,
              "xaxis.nticks": original.xaxis.nticks,
              "xaxis.tickfont.size": original.xaxis.tickfontSize,
              "yaxis.title.text": original.yaxis.title,
              "yaxis.automargin": original.yaxis.automargin,
              "yaxis.tickfont.size": original.yaxis.tickfontSize,
              "yaxis2.title.text": original.yaxis2.title,
              "yaxis2.automargin": original.yaxis2.automargin,
              "yaxis2.tickfont.size": original.yaxis2.tickfontSize
            }).then(function () {
              plot.dataset.mlbxCompactMode = "desktop";
            }).catch(function () {});
          }

          function syncPlots() {
            const plots = Array.from(doc.querySelectorAll('[data-testid="stPlotlyChart"] .js-plotly-plot'));
            plots.forEach(function (plot) {
              if (isSmallViewport()) compactPlot(plot);
              else restorePlot(plot);
            });
          }

          syncPlots();

          if (!host.__mlbxViewportPlotObserver) {
            host.__mlbxViewportPlotObserver = new host.MutationObserver(syncPlots);
            host.__mlbxViewportPlotObserver.observe(doc.body, { childList: true, subtree: true });
            host.addEventListener("resize", syncPlots, { passive: true });
          }
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _inject_live_age_updater() -> None:
    """Mantiene actualizados edad y hora local del usuario sin esperar a un rerun completo."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          if (!doc || !doc.body) return;

          function formatAge(epoch) {
            const now = Math.floor(Date.now() / 1000);
            const diff = Math.max(0, now - epoch);
            if (diff < 60) return `${diff}s`;
            if (diff < 3600) return `${Math.floor(diff / 60)}m`;
            return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
          }

          function nextDelayMs(epoch) {
            const now = Math.floor(Date.now() / 1000);
            const diff = Math.max(0, now - epoch);
            if (diff < 60) return 1000;
            const rem = diff % 60;
            return ((60 - rem) || 60) * 1000;
          }

          function formatLocalDateTime(epoch) {
            const date = new Date(epoch * 1000);
            if (!Number.isFinite(date.getTime())) return "";
            const pad = function (value) {
              return String(value).padStart(2, "0");
            };
            return [
              pad(date.getDate()),
              pad(date.getMonth() + 1),
              date.getFullYear()
            ].join("-") + " " + [
              pad(date.getHours()),
              pad(date.getMinutes()),
              pad(date.getSeconds())
            ].join(":");
          }

          function refreshUserTimes() {
            doc.querySelectorAll(".mlbx-live-user-time[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              const text = formatLocalDateTime(epoch);
              if (text && el.textContent !== text) el.textContent = text;
            });
            doc.querySelectorAll(".mlbx-live-user-time-label").forEach(function (el) {
              const fallback = el.getAttribute("data-fallback-label") || "Hora usuario";
              if (el.textContent !== fallback) el.textContent = fallback;
            });
          }

          function refreshAges() {
            let minDelay = 60000;
            let found = false;
            refreshUserTimes();
            doc.querySelectorAll(".mlbx-live-age[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              found = true;
              const text = formatAge(epoch);
              if (el.textContent !== text) el.textContent = text;
              minDelay = Math.min(minDelay, nextDelayMs(epoch));
            });
            return found ? Math.max(1000, minDelay) : 60000;
          }

          function schedule() {
            if (host.__mlbxAgeTimer) host.clearTimeout(host.__mlbxAgeTimer);
            host.__mlbxAgeTimer = host.setTimeout(schedule, refreshAges());
          }

          if (!host.__mlbxAgeObserver) {
            host.__mlbxAgeObserver = new host.MutationObserver(function () {
              schedule();
            });
            host.__mlbxAgeObserver.observe(doc.body, { childList: true, subtree: true });
            host.addEventListener("pageshow", schedule, { passive: true });
          }

          schedule();
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _pydeck_chart_stretch(deck, key: str, height: int = 900):
    """Renderiza pydeck de forma compatible entre versiones de Streamlit."""
    try:
        return st.pydeck_chart(
            deck, use_container_width=True, height=int(height), key=key,
            on_select="rerun", selection_mode="single-object",
        )
    except TypeError:
        return st.pydeck_chart(deck, use_container_width=True, height=int(height), key=key)


# ============================================================
# PROCESAMIENTO ESTÁNDAR DE PROVEEDORES
# ============================================================

from dataclasses import dataclass


@dataclass
class ProcessedData:
    """Variables derivadas del procesamiento post-fetch de un proveedor estándar."""
    z: float
    p_abs: float
    p_msl: float
    p_abs_disp: str
    p_msl_disp: str
    dp3: float
    rate_h: float
    p_label: str
    p_arrow: str
    inst_mm_h: float
    r1_mm_h: float
    r5_mm_h: float
    inst_label: str
    e_sat: float
    e: float
    Td_calc: float
    Tw: float
    q: float
    q_gkg: float
    theta: float
    Tv: float
    Te: float
    rho: float
    rho_v_gm3: float
    lcl: float
    solar_rad: float
    uv: float
    et0: float
    clarity: float
    balance: float
    has_radiation: bool
    has_chart_data: bool


def process_standard_provider(
    base: dict,
    provider_name: str,
    elevation_fallback_key: str,
    series_override: Optional[dict] = None,
    series_7d: Optional[dict] = None,
) -> ProcessedData:
    """Procesamiento post-fetch común a todos los proveedores estándar.

    Parámetros:
        base: dict devuelto por get_xxx_data() con keys canónicas (Tc, RH, p_hpa…)
        provider_name: nombre del proveedor ("EUSKALMET", "METEOCAT"…)
        elevation_fallback_key: key de session_state para altitud de respaldo
        series_override: si no es None, se usa como serie de charts en vez de base["_series"]
        series_7d: si no es None, se escribe en trend_hourly_* de session_state
    """
    NaN = float("nan")

    # 1. Session state: lat, lon, elevation, timestamp
    st.session_state["last_update_time"] = time.time()
    st.session_state["station_lat"] = base.get("lat", NaN)
    st.session_state["station_lon"] = base.get("lon", NaN)

    z = base.get("elevation", st.session_state.get(elevation_fallback_key, 0))
    st.session_state["station_elevation"] = z
    st.session_state["elevation_source"] = provider_name

    # 2. Warning de datos antiguos
    data_age_minutes = (time.time() - base["epoch"]) / 60
    if data_age_minutes > MAX_DATA_AGE_MINUTES:
        st.warning(
            f"⚠️ Datos de {provider_name} con {data_age_minutes:.0f} minutos "
            "de antigüedad. La estación puede no estar reportando."
        )
        logger.warning(f"Datos {provider_name} antiguos: {data_age_minutes:.1f} minutos")

    # 3. Lluvia
    inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
    inst_label = rain_intensity_label(inst_mm_h)

    # 4. Presión
    p_abs = float(base.get("p_abs_hpa", NaN))
    p_msl = float(base.get("p_hpa", NaN))
    provider_for_pressure = st.session_state.get("connection_type", provider_name)
    p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
    p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

    if not is_nan(p_abs):
        init_pressure_history()
        push_pressure(p_abs, base["epoch"])

    # 5. Tendencia presión 3h (desde base primero)
    if not is_nan(p_msl):
        dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
            p_now=p_msl,
            epoch_now=base["epoch"],
            p_3h_ago=base.get("pressure_3h_ago"),
            epoch_3h_ago=base.get("epoch_3h_ago"),
        )
    else:
        dp3, rate_h, p_label, p_arrow = NaN, NaN, "—", "•"

    # 6. Termodinámica
    e_sat = e_v = Td_calc = Tw = q_val = q_gkg = theta = Tv_val = Te_val = NaN
    rho_val = rho_v_gm3 = lcl_val = NaN

    if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
        e_sat = e_s(base["Tc"])
        e_v = vapor_pressure(base["Tc"], base["RH"])
        Td_calc = dewpoint_from_vapor_pressure(e_v)
        Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)
        base["Td"] = Td_calc

        if not is_nan(p_abs):
            q_val = specific_humidity(e_v, p_abs)
            q_gkg = q_val * 1000
            theta = potential_temperature(base["Tc"], p_abs)
            Tv_val = virtual_temperature(base["Tc"], q_val)
            Te_val = equivalent_temperature(base["Tc"], q_val)
            rho_val = air_density(p_abs, Tv_val)
            rho_v_gm3 = absolute_humidity(e_v, base["Tc"])
            lcl_val = lcl_height(base["Tc"], Td_calc)
    else:
        base["Td"] = NaN

    # 6.5 Sensación térmica y Heat Index (calculados, nunca del API)
    wind_fl = base.get("wind", 0.0)
    if is_nan(wind_fl):
        wind_fl = 0.0
    wind_fl_ms = float(wind_fl) / 3.6
    base["feels_like"] = apparent_temperature(base["Tc"], e_v, wind_fl_ms)
    base["heat_index"] = heat_index_rothfusz(base["Tc"], base.get("RH", NaN))

    # 7. Radiación / UV / claridad  (ET0 se acumula desde la serie en paso 8.5)
    solar_rad = base.get("solar_radiation", NaN)
    uv = base.get("uv", NaN)
    has_radiation = not is_nan(solar_rad) or not is_nan(uv)
    et0 = clarity = balance = NaN

    if has_radiation:
        from models.radiation import sky_clarity_index
        lat = base.get("lat", NaN)
        lon = base.get("lon", NaN)
        clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)

    # 8. Series para gráficos
    if series_override is not None:
        series = series_override if isinstance(series_override, dict) else {}
    else:
        raw = base.get("_series")
        series = raw if isinstance(raw, dict) else {}

    chart_epochs = series.get("epochs", [])
    chart_temps = series.get("temps", [])
    chart_humidities = series.get("humidities", [])
    chart_pressures = series.get("pressures_abs", [])
    chart_winds = series.get("winds", [])
    chart_gusts = series.get("gusts", [])
    chart_wind_dirs = series.get("wind_dirs", [])
    chart_uv_indexes = series.get("uv_indexes", [])
    chart_solar_radiations = series.get("solar_radiations", [])
    has_chart_data = series.get("has_data", False)

    # 8.5. ET0 acumulada desde serie — integra cada paso temporal igual que WU
    if has_radiation and chart_solar_radiations:
        from models.radiation import penman_monteith_et0
        lat_et0 = base.get("lat", NaN)
        et0_accum = 0.0
        valid_steps = 0
        fallback_wind = base.get("wind", 2.0)
        if is_nan(fallback_wind):
            fallback_wind = 2.0
        for i, epoch_i in enumerate(chart_epochs):
            solar_i = chart_solar_radiations[i] if i < len(chart_solar_radiations) else NaN
            temp_i  = chart_temps[i]       if i < len(chart_temps)       else NaN
            rh_i    = chart_humidities[i]  if i < len(chart_humidities)  else NaN
            if is_nan(solar_i) or solar_i < 0 or is_nan(temp_i) or is_nan(rh_i):
                continue
            wind_i = chart_winds[i] if i < len(chart_winds) else NaN
            if is_nan(wind_i):
                wind_i = fallback_wind
            if wind_i < 0.1:
                wind_i = 0.1
            et0_i = penman_monteith_et0(
                solar_i, temp_i, rh_i, wind_i, lat_et0, z, float(epoch_i),
            )
            if is_nan(et0_i):
                continue
            step_hours = 5.0 / 60.0
            if i > 0:
                try:
                    dt_s = float(epoch_i) - float(chart_epochs[i - 1])
                    if 120 <= dt_s <= 1800:
                        step_hours = dt_s / 3600.0
                except Exception:
                    pass
            et0_accum += et0_i / 24.0 * step_hours
            valid_steps += 1
        if valid_steps > 0:
            et0 = et0_accum
            balance = water_balance(base["precip_total"], et0)

    # 9. Tendencia presión 3h desde serie (abs → MSL)
    if has_chart_data and len(chart_epochs) == len(chart_pressures):
        press_valid = [
            (int(ep), float(p))
            for ep, p in zip(chart_epochs, chart_pressures)
            if not is_nan(float(p))
        ]
        if len(press_valid) >= 2:
            press_valid.sort(key=lambda x: x[0])
            ep_now, p_abs_now = press_valid[-1]
            target_ep = ep_now - (3 * 3600)
            ep_3h, p_abs_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))
            msl_factor = math.exp(z / 8000.0)
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_abs_now * msl_factor,
                epoch_now=ep_now,
                p_3h_ago=p_abs_3h * msl_factor,
                epoch_3h_ago=ep_3h,
            )

    # 10. Guardar chart data en session_state
    st.session_state["chart_epochs"] = chart_epochs
    st.session_state["chart_temps"] = chart_temps
    st.session_state["chart_humidities"] = chart_humidities
    st.session_state["chart_dewpts"] = []
    st.session_state["chart_pressures"] = chart_pressures
    st.session_state["chart_uv_indexes"] = chart_uv_indexes
    st.session_state["chart_solar_radiations"] = chart_solar_radiations
    st.session_state["chart_winds"] = chart_winds
    st.session_state["chart_gusts"] = chart_gusts
    st.session_state["chart_wind_dirs"] = chart_wind_dirs
    st.session_state["has_chart_data"] = has_chart_data

    # 11. Trend hourly opcional (MeteoGalicia, NWS)
    if series_7d is not None:
        if isinstance(series_7d, dict) and series_7d.get("has_data"):
            st.session_state["trend_hourly_epochs"] = series_7d.get("epochs", [])
            st.session_state["trend_hourly_temps"] = series_7d.get("temps", [])
            st.session_state["trend_hourly_humidities"] = series_7d.get("humidities", [])
            st.session_state["trend_hourly_pressures"] = series_7d.get("pressures_abs", [])
        else:
            st.session_state["trend_hourly_epochs"] = chart_epochs
            st.session_state["trend_hourly_temps"] = chart_temps
            st.session_state["trend_hourly_humidities"] = chart_humidities
            st.session_state["trend_hourly_pressures"] = chart_pressures
    else:
        st.session_state["trend_hourly_epochs"] = []
        st.session_state["trend_hourly_temps"] = []
        st.session_state["trend_hourly_humidities"] = []
        st.session_state["trend_hourly_pressures"] = []

    return ProcessedData(
        z=z, p_abs=p_abs, p_msl=p_msl, p_abs_disp=p_abs_disp, p_msl_disp=p_msl_disp,
        dp3=dp3, rate_h=rate_h, p_label=p_label, p_arrow=p_arrow,
        inst_mm_h=inst_mm_h, r1_mm_h=r1_mm_h, r5_mm_h=r5_mm_h, inst_label=inst_label,
        e_sat=e_sat, e=e_v, Td_calc=Td_calc, Tw=Tw, q=q_val, q_gkg=q_gkg,
        theta=theta, Tv=Tv_val, Te=Te_val, rho=rho_val, rho_v_gm3=rho_v_gm3, lcl=lcl_val,
        solar_rad=solar_rad, uv=uv, et0=et0, clarity=clarity, balance=balance,
        has_radiation=has_radiation, has_chart_data=has_chart_data,
    )


def _unpack_processed(r: ProcessedData) -> tuple:
    """Desempaqueta ProcessedData en la tupla de variables locales que espera el display."""
    return (
        r.z, r.p_abs, r.p_msl, r.p_abs_disp, r.p_msl_disp,
        r.dp3, r.rate_h, r.p_label, r.p_arrow,
        r.inst_mm_h, r.r1_mm_h, r.r5_mm_h, r.inst_label,
        r.e_sat, r.e, r.Td_calc, r.Tw, r.q, r.q_gkg,
        r.theta, r.Tv, r.Te, r.rho, r.rho_v_gm3, r.lcl,
        r.solar_rad, r.uv, r.et0, r.clarity, r.balance,
        r.has_radiation, r.has_chart_data,
    )


def _infer_series_step_minutes(times_like) -> int:
    try:
        import pandas as pd
        times = pd.to_datetime(times_like, errors="coerce")
        if isinstance(times, pd.Series):
            times_series = times.dropna().sort_values().reset_index(drop=True)
        else:
            times_series = (
                pd.Series(pd.DatetimeIndex(times))
                .dropna()
                .sort_values()
                .reset_index(drop=True)
            )
        if len(times_series) < 2:
            return 0
        diffs = times_series.diff().dropna().dt.total_seconds() / 60.0
        diffs = diffs[diffs > 0]
        if diffs.empty:
            return 0
        return int(round(float(diffs.median())))
    except Exception:
        return 0


# ============================================================
# SIDEBAR Y TEMA
# ============================================================

theme_mode, dark = render_sidebar()
unit_preferences = normalize_unit_preferences(st.session_state.get("unit_preferences"))
temp_unit_pref = unit_preferences["temperature"]
wind_unit_pref = unit_preferences["wind"]
pressure_unit_pref = unit_preferences["pressure"]
precip_unit_pref = unit_preferences["precip"]
radiation_unit_pref = unit_preferences["radiation"]

temp_unit_txt = temperature_unit_label(temp_unit_pref)
wind_unit_txt = wind_unit_label(wind_unit_pref)
pressure_unit_txt = pressure_unit_label(pressure_unit_pref)
precip_unit_txt = precip_unit_label(precip_unit_pref)
radiation_unit_txt = radiation_unit_label(radiation_unit_pref)
radiation_energy_unit_txt = radiation_energy_unit_label(radiation_unit_pref)


def _fmt_temp_display(value, decimals: int = 1) -> str:
    return format_temperature(value, temp_unit_pref, decimals=decimals)


def _fmt_temp_delta_display(value, decimals: int = 1) -> str:
    return format_temperature_delta(value, temp_unit_pref, decimals=decimals)


def _fmt_wind_display(value, decimals: int = 1) -> str:
    return format_wind(value, wind_unit_pref, decimals=decimals)


def _fmt_pressure_display(value, decimals: int = 1) -> str:
    return format_pressure(value, pressure_unit_pref, decimals=decimals)


def _fmt_precip_display(value, decimals: int = 1) -> str:
    return format_precip(value, precip_unit_pref, decimals=decimals)


def _fmt_radiation_display(value, decimals: int = 0) -> str:
    return format_radiation(value, radiation_unit_pref, decimals=decimals)


def _fmt_radiation_energy_display(value, decimals: int = 2) -> str:
    return format_radiation_energy(value, radiation_unit_pref, decimals=decimals)


def _render_theme_table(df, table_class: str = "mlbx-data-table") -> None:
    """Renderiza una tabla HTML simple para respetar el tema claro/oscuro."""
    try:
        styled_df = df.copy()
    except Exception:
        styled_df = df
    try:
        html_table = styled_df.to_html(index=False, classes=table_class, border=0)
    except Exception:
        st.dataframe(df, width="stretch", hide_index=True)
        return
    st.markdown(
        html_clean(f"<div class='mlbx-table-wrap'>{html_table}</div>"),
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_map_search_nearby_stations(
    lat: float,
    lon: float,
    max_results: int,
    provider_ids: tuple[str, ...],
):
    """Cache corto para que cambiar el tema no dispare de nuevo toda la búsqueda del mapa."""
    return search_nearby_stations(
        lat,
        lon,
        max_results=max_results,
        provider_ids=list(provider_ids),
    )

# Configuración global de Plotly según tema
import plotly.io as pio

# Crear template personalizado basado en el tema
if dark:
    # Template oscuro
    pio.templates["meteolabx_dark"] = pio.templates["plotly_dark"]
    pio.templates["meteolabx_dark"].layout.font.color = "rgba(255, 255, 255, 0.92)"
    pio.templates["meteolabx_dark"].layout.title.font.color = "rgba(255, 255, 255, 0.92)"
    pio.templates["meteolabx_dark"].layout.xaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
    pio.templates["meteolabx_dark"].layout.yaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
    pio.templates.default = "meteolabx_dark"
    plotly_title_color = "rgba(255, 255, 255, 0.92)"
else:
    # Template claro
    pio.templates["meteolabx_light"] = pio.templates["plotly_white"]
    pio.templates["meteolabx_light"].layout.font.color = "rgba(15, 18, 25, 0.92)"
    pio.templates["meteolabx_light"].layout.title.font.color = "rgba(15, 18, 25, 0.92)"
    pio.templates["meteolabx_light"].layout.xaxis.title.font.color = "rgba(15, 18, 25, 0.92)"
    pio.templates["meteolabx_light"].layout.yaxis.title.font.color = "rgba(15, 18, 25, 0.92)"
    pio.templates.default = "meteolabx_light"
    plotly_title_color = "rgba(15, 18, 25, 0.92)"

# CSS para sidebar y botones
sidebar_bg = "#f4f6fb" if not dark else "#262730"
sidebar_text = "rgb(15, 18, 25)" if not dark else "rgb(250, 250, 250)"
button_bg = "#ffffff" if not dark else "#0e1117"
button_text = "rgb(15, 18, 25)" if not dark else "rgb(250, 250, 250)"
button_border = "rgba(180, 180, 180, 0.55)" if not dark else "rgba(120, 126, 138, 0.55)"
button_border_width = "1px"
eye_color = "rgba(0, 0, 0, 0.5)" if not dark else "rgba(255, 255, 255, 0.8)"
eye_color_hover = "rgba(0, 0, 0, 0.7)" if not dark else "rgba(255, 255, 255, 1)"
theme_color_scheme = "light" if not dark else "dark"
expander_bg = "rgba(255,255,255,0.45)" if not dark else "rgba(22,25,31,0.45)"
expander_summary_bg = "rgba(255,255,255,0.85)" if not dark else "rgba(17,22,30,0.92)"

sidebar_css_hash = hashlib.md5(f"sidebar-{theme_color_scheme}-{sidebar_bg}-{button_bg}-{button_border}".encode()).hexdigest()[:8]

st.markdown(f"""
<style data-sidebar-theme="{sidebar_css_hash}">
/* Forzar tema de sidebar */
[data-testid="stSidebar"] {{
    background-color: {sidebar_bg} !important;
    color-scheme: {theme_color_scheme} !important;
    --mlbx-control-bg: {'#ffffff' if not dark else '#0e1117'};
    --mlbx-control-bg-hover: {'#f3f5fa' if not dark else '#141821'};
    --mlbx-control-border: {button_border};
    --mlbx-sidebar-text: {sidebar_text};
}}

[data-testid="stSidebar"],
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] li,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] h5,
[data-testid="stSidebar"] h6 {{
    color: var(--mlbx-sidebar-text) !important;
}}

/* Excepción: banners de estado con color tintado propio */
[data-testid="stSidebar"] .mlbx-status-banner,
[data-testid="stSidebar"] .mlbx-status-banner * {{
    color: var(--mlbx-banner-fg) !important;
    font-weight: 500 !important;
}}

[data-testid="stSidebar"] label {{
    color: {sidebar_text} !important;
}}

[data-testid="stSidebar"] input[type="text"],
[data-testid="stSidebar"] input[type="password"],
[data-testid="stSidebar"] input[type="number"],
[data-testid="stSidebar"] textarea {{
    color: {sidebar_text} !important;
    background-color: var(--mlbx-control-bg) !important;
}}

/* Contenedor de inputs en sidebar (incluye zona del ojo y +/-) */
[data-testid="stSidebar"] [data-baseweb="input"] {{
    background-color: var(--mlbx-control-bg) !important;
    border-color: {button_border} !important;
}}

/* Selectbox de sidebar: forzar fondo y texto del control visible */
[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
    background: var(--mlbx-control-bg) !important;
    border: {button_border_width} solid var(--mlbx-control-border) !important;
    color: var(--mlbx-sidebar-text) !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] div,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] div {{
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] svg,
[data-testid="stSidebar"] [data-testid="stSelectbox"] svg path,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] svg,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] svg path {{
    fill: var(--mlbx-sidebar-text) !important;
    stroke: var(--mlbx-sidebar-text) !important;
}}

/* Botón del ojo de la API key (evitar cuadro negro) */
[data-testid="stSidebar"] [data-testid="stTextInput"] button {{
    background: var(--mlbx-control-bg) !important;
    border: 0 !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button svg,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg path,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg circle,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg rect {{
    color: {eye_color} !important;
    fill: {eye_color} !important;
    stroke: {eye_color} !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg path,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg circle,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg rect {{
    color: {eye_color_hover} !important;
    fill: {eye_color_hover} !important;
    stroke: {eye_color_hover} !important;
}}

/* Líneas separadoras visibles */
[data-testid="stSidebar"] hr {{
    border-color: {'rgba(0, 0, 0, 0.12)' if not dark else 'rgba(255, 255, 255, 0.12)'} !important;
    border-width: 1px !important;
    margin: 1rem 0 !important;
}}

/* Botones principales de la sidebar (Guardar, Conectar, etc.) - bordes visibles */
[data-testid="stSidebar"] div[data-testid="stButton"] > button {{
    background-color: {button_bg} !important;
    color: {sidebar_text} !important;
    border: {button_border_width} solid {button_border} !important;
}}

[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {{
    background-color: {'#e8ecf3' if not dark else '#1f2229'} !important;
    border-color: {'rgba(100, 100, 100, 0.9)' if not dark else 'rgba(150, 150, 150, 0.9)'} !important;
}}

/* Checkbox */
[data-testid="stSidebar"] [data-testid="stCheckbox"] {{
    color: {sidebar_text} !important;
}}

/* Radios y toggles en sidebar: forzar esquema de color dinámico */
[data-testid="stSidebar"] input[type="radio"],
[data-testid="stSidebar"] input[type="checkbox"] {{
    color-scheme: {theme_color_scheme} !important;
}}

/* Ocultar el control nativo y dejar visible el indicador custom del label */
[data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"],
[data-testid="stSidebar"] [data-testid="stCheckbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    pointer-events: none !important;
}}

/* Radios del tema: forzar colores para que cambien al alternar claro/oscuro */
[data-testid="stSidebar"] input[type="radio"] {{
    -webkit-appearance: none !important;
    appearance: none !important;
    accent-color: #ff4b4b !important;
    width: 0.95rem !important;
    height: 0.95rem !important;
    border-radius: 999px !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    box-shadow: inset 0 0 0 0.24rem transparent !important;
}}

[data-testid="stSidebar"] input[type="radio"]:checked {{
    border-color: #ff4b4b !important;
    box-shadow: inset 0 0 0 0.24rem #ff4b4b !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
}}

[data-testid="stSidebar"] input[type="checkbox"] {{
    -webkit-appearance: none !important;
    appearance: none !important;
    accent-color: #ff4b4b !important;
    width: 1.0rem !important;
    height: 1.0rem !important;
    border-radius: 0.22rem !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] input[type="checkbox"]:checked {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Radios de Streamlit/BaseWeb en sidebar: círculo visible */
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {{
    width: 0.95rem !important;
    height: 0.95rem !important;
    border-radius: 999px !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) > div:first-child {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-color: #ff4b4b !important;
    box-shadow: inset 0 0 0 0.24rem #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child * {{
    color: transparent !important;
    fill: transparent !important;
    stroke: transparent !important;
}}

/* Checkbox de Streamlit/BaseWeb en sidebar: cuadrado visible */
[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child {{
    width: 1.0rem !important;
    height: 1.0rem !important;
    border-radius: 0.22rem !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child svg,
[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child svg path {{
    fill: {'#ffffff' if not dark else '#ffffff'} !important;
    stroke: {'#ffffff' if not dark else '#ffffff'} !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-child::after {{
    content: "✓";
    color: #ffffff;
    display: block;
    text-align: center;
    line-height: 1rem;
    font-size: 0.8rem;
    font-weight: 700;
}}

/* Radios del selector de tema cuando Streamlit/BaseWeb los renderiza como círculos custom */
[data-testid="stSidebar"] [role="radiogroup"] [role="radio"] {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [role="radiogroup"] [role="radio"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [role="radiogroup"] [role="radio"] *,
[data-testid="stSidebar"] [role="radiogroup"] label * {{
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [role="checkbox"] {{
    width: 1.05rem !important;
    height: 1.05rem !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-radius: 0.25rem !important;
}}

[data-testid="stSidebar"] [role="checkbox"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggle de sidebar (switch) visible en claro/oscuro */
[data-testid="stSidebar"] [data-baseweb="switch"] input + div {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {button_border} !important;
}}

[data-testid="stSidebar"] [data-baseweb="switch"] input + div > div {{
    background-color: {'#ffffff' if not dark else '#dbe4f2'} !important;
}}

[data-testid="stSidebar"] [data-baseweb="switch"] input:checked + div {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [role="switch"] {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {button_border} !important;
    border-radius: 999px !important;
}}

[data-testid="stSidebar"] [role="switch"][aria-checked="true"] {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Segmented control real de Streamlit en sidebar */
[data-testid="stSidebar"] [data-testid="stButtonGroup"] {{
    width: 100% !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] {{
    width: 100% !important;
    background: var(--mlbx-control-bg) !important;
    border: 1px solid var(--mlbx-control-border) !important;
    border-radius: 0.95rem !important;
    overflow: hidden !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
    font-weight: 600 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"] {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
    font-weight: 600 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    box-shadow: inset 0 0 0 1px #ff4b4b !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"]:hover {{
    background: #ff5f5f !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"]:hover {{
    background: #ff5f5f !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] p {{
    color: #ffffff !important;
}}

/* Toggle real de Streamlit/BaseWeb en sidebar */
[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] {{
    color: var(--mlbx-sidebar-text) !important;
    background: transparent !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child {{
    background: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid var(--mlbx-control-border) !important;
    border-radius: 999px !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child > div {{
    background: {'#ffffff' if not dark else '#dbe4f2'} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    appearance: auto !important;
    -webkit-appearance: auto !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] {{
    color: var(--mlbx-sidebar-text) !important;
}}

/* Botones +/- de calibración / number_input */
[data-testid="stSidebar"] [data-testid="stNumberInput"] button {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stNumberInput"] button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stNumberInput"] button svg,
[data-testid="stSidebar"] [data-testid="stNumberInput"] button svg path {{
    fill: var(--mlbx-sidebar-text) !important;
    stroke: var(--mlbx-sidebar-text) !important;
}}
</style>
""", unsafe_allow_html=True)

sidebar_control_bg = '#ffffff' if not dark else '#0e1117'
sidebar_toggle_bg = '#d7dbe4' if not dark else '#1f2734'

components.html(f"""
<script>
(function() {{
  const host = window.parent || window;
  const doc = host.document;
  const SIDEBAR_BG = "{sidebar_control_bg}";
  const SIDEBAR_BORDER = "{button_border}";
  const SIDEBAR_TEXT = "{sidebar_text}";
  const TOGGLE_BG = "{sidebar_toggle_bg}";
  const ACTIVE = "#ff4b4b";
  const MAP_BG = "{button_bg}";
  const MAP_BORDER = "{button_border}";
  const MAP_TEXT = "{'rgba(15, 18, 25, 0.92)' if not dark else 'rgba(255, 255, 255, 0.92)'}";
  const MAP_HOVER = "{'#eef2f8' if not dark else '#1b2230'}";
  const IS_DARK = {str(bool(dark)).lower()};

  function paintSidebarControls() {{
    const sidebar = doc.querySelector('[data-testid="stSidebar"]');
    if (!sidebar) return;

    sidebar.querySelectorAll('[data-testid="stButtonGroup"] [data-baseweb="button-group"]').forEach((group) => {{
      group.style.width = "100%";
      group.style.background = SIDEBAR_BG;
      group.style.border = "1px solid " + SIDEBAR_BORDER;
      group.style.borderRadius = "0.95rem";
      group.style.overflow = "hidden";
      group.style.boxShadow = "none";

      group.querySelectorAll('[role="radio"], button').forEach((btn) => {{
        const checked = btn.getAttribute('aria-checked') === 'true'
          || btn.getAttribute('data-testid') === 'stBaseButton-segmented_controlActive';
        btn.style.background = checked ? ACTIVE : SIDEBAR_BG;
        btn.style.color = checked ? "#ffffff" : SIDEBAR_TEXT;
        btn.style.borderColor = checked ? ACTIVE : SIDEBAR_BORDER;
        btn.style.boxShadow = checked ? "inset 0 0 0 1px " + ACTIVE : "none";
        btn.style.fontWeight = checked ? "700" : "600";
        btn.querySelectorAll('*').forEach((node) => {{
          node.style.color = checked ? "#ffffff" : SIDEBAR_TEXT;
          node.style.fill = checked ? "#ffffff" : "";
          node.style.stroke = checked ? "#ffffff" : "";
        }});
      }});
    }});

    sidebar.querySelectorAll('input[type="radio"]').forEach((input) => {{
      if (input.closest('[data-baseweb="button-group"]')) return;
      const label = input.closest('label');
      const bubble = label && label.children && label.children[0] ? label.children[0] : null;
      input.style.webkitAppearance = "none";
      input.style.appearance = "none";
      input.style.width = "0.95rem";
      input.style.height = "0.95rem";
      input.style.borderRadius = "999px";
      input.style.border = "1px solid " + SIDEBAR_BORDER;
      input.style.background = SIDEBAR_BG;
      input.style.boxShadow = input.checked ? "inset 0 0 0 0.24rem #ff4b4b" : "none";
      if (!bubble) return;
      bubble.style.width = "0.95rem";
      bubble.style.height = "0.95rem";
      bubble.style.borderRadius = "999px";
      bubble.style.border = "1px solid " + SIDEBAR_BORDER;
      bubble.style.background = SIDEBAR_BG;
      bubble.style.boxShadow = input.checked ? "inset 0 0 0 0.24rem #ff4b4b" : "none";
      bubble.style.color = "transparent";
      bubble.querySelectorAll('*').forEach((node) => {{
        node.style.color = "transparent";
        node.style.fill = "transparent";
        node.style.stroke = "transparent";
      }});
    }});

    sidebar.querySelectorAll('input[type="checkbox"]').forEach((input) => {{
      if (input.closest('[data-baseweb="checkbox"]')) return;
      const label = input.closest('label');
      const box = label && label.children && label.children[0] ? label.children[0] : null;
      input.style.webkitAppearance = "none";
      input.style.appearance = "none";
      input.style.width = "1rem";
      input.style.height = "1rem";
      input.style.borderRadius = "0.22rem";
      input.style.border = "1px solid " + (input.checked ? ACTIVE : SIDEBAR_BORDER);
      input.style.background = input.checked ? ACTIVE : SIDEBAR_BG;
      if (!box) return;
      box.style.width = "1rem";
      box.style.height = "1rem";
      box.style.borderRadius = "0.22rem";
      box.style.border = "1px solid " + (input.checked ? ACTIVE : SIDEBAR_BORDER);
      box.style.background = input.checked ? ACTIVE : SIDEBAR_BG;
      box.querySelectorAll('svg, svg *').forEach((node) => {{
        node.style.fill = "#ffffff";
        node.style.stroke = "#ffffff";
      }});
    }});

    sidebar.querySelectorAll('[data-baseweb="switch"]').forEach((sw) => {{
      const knobTrack = sw.querySelector('label > div, div[role="switch"]');
      const roleSwitch = sw.querySelector('[role="switch"]');
      const input = sw.querySelector('input[type="checkbox"]');
      const checked = !!(input && input.checked) || (roleSwitch && roleSwitch.getAttribute('aria-checked') === 'true');
      const track = roleSwitch || knobTrack;
      if (track) {{
        track.style.background = checked ? ACTIVE : TOGGLE_BG;
        track.style.border = "1px solid " + (checked ? ACTIVE : SIDEBAR_BORDER);
        track.style.borderRadius = "999px";
      }}
      const knob = sw.querySelector('label > div > div');
      if (knob) {{
        knob.style.background = "#ffffff";
      }}
    }});
  }}

  function paintMapControls() {{
    doc.querySelectorAll('.mapboxgl-ctrl-group, .maplibregl-ctrl-group').forEach((group) => {{
      group.style.background = MAP_BG;
      group.style.border = "1px solid " + MAP_BORDER;
      group.style.borderRadius = "12px";
      group.style.overflow = "hidden";
      group.style.boxShadow = "0 10px 24px rgba(0,0,0," + (IS_DARK ? "0.28" : "0.12") + ")";
      group.style.filter = "none";

      group.querySelectorAll('button').forEach((btn, index) => {{
        btn.style.background = MAP_BG;
        btn.style.color = MAP_TEXT;
        btn.style.width = "38px";
        btn.style.height = "38px";
        btn.style.borderBottom = index === group.querySelectorAll('button').length - 1 ? "none" : ("1px solid " + MAP_BORDER);
      }});

      group.querySelectorAll('.mapboxgl-ctrl-icon, .maplibregl-ctrl-icon').forEach((icon) => {{
        icon.style.filter = IS_DARK ? "brightness(0) invert(1)" : "none";
      }});
    }});
  }}

  paintSidebarControls();
  paintMapControls();

  if (!host.__mlbxSidebarThemeObserver) {{
    host.__mlbxSidebarThemeObserver = new host.MutationObserver(() => {{
      paintSidebarControls();
      paintMapControls();
    }});
    host.__mlbxSidebarThemeObserver.observe(doc.body, {{ childList: true, subtree: true, attributes: true }});
  }}
}})();
</script>
""", height=0, width=0)


# ============================================================
# CSS (CLARO / OSCURO)
# ============================================================

if not dark:
    css = html_clean("""
    <style>
      :root{
        --bg: #f4f6fb;
        --panel: rgba(255,255,255,0.85);
        --border: rgba(18, 18, 18, 0.08);
        --shadow: 0 10px 24px rgba(0,0,0,0.08);
        --text: rgba(15,18,25,0.92);
        --muted: rgba(15,18,25,0.55);
        --accent: rgba(35, 132, 255, 0.20);
      }
      .stApp{
        color-scheme: light;
        background: radial-gradient(circle at 15% 10%, #ffffff 0%, var(--bg) 50%, #eef2fb 100%);
      }
    </style>
    """)
else:
    css = html_clean("""
    <style>
      :root{
        --bg: #0f1115;
        --panel: rgba(22, 25, 31, 0.78);
        --border: rgba(255,255,255,0.10);
        --shadow: 0 12px 26px rgba(0,0,0,0.50);
        --text: rgba(255,255,255,0.92);
        --muted: rgba(255,255,255,0.62);
        --accent: rgba(120, 180, 255, 0.12);
      }
      .stApp{
        color-scheme: dark;
        background: radial-gradient(circle at 15% 10%, #2a2f39 0%, #14171d 55%, #0f1115 100%);
      }
    </style>
    """)

st.markdown(css, unsafe_allow_html=True)

# CSS adicional para forzar colores en headers de Streamlit
main_button_bg = "#ffffff" if not dark else "rgba(22, 25, 31, 0.88)"
main_button_text = "rgba(15, 18, 25, 0.92)" if not dark else "rgba(255, 255, 255, 0.92)"
main_button_border = "rgba(18, 18, 18, 0.22)" if not dark else "rgba(255, 255, 255, 0.22)"
main_hr_color = "rgba(18, 18, 18, 0.16)" if not dark else "rgba(255, 255, 255, 0.16)"

st.markdown(f"""
<style>
[data-testid="stDecoration"] {{
    display: none !important;
}}

/* Mantener visible el botón para desplegar sidebar cuando está colapsada */
button[data-testid="collapsedControl"] {{
    display: flex !important;
}}

/* Ocultar solo el menú de Streamlit (tres puntos), sin tocar el control de sidebar */
#MainMenu {{
    visibility: hidden !important;
}}

[data-testid="stToolbar"] button[aria-label="Main menu"],
[data-testid="stToolbar"] button[title="Main menu"],
[data-testid="stToolbar"] button[aria-haspopup="menu"]:not([data-testid="collapsedControl"]) {{
    display: none !important;
}}

/* Texto del contenido principal dependiente de tema */
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] li,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] span,
[data-testid="stMainBlockContainer"] [data-testid="stText"] {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] {{
    color: var(--muted) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMetricLabel"] > div,
[data-testid="stMainBlockContainer"] [data-testid="stMetricValue"] > div,
[data-testid="stMainBlockContainer"] [data-testid="stMetricDelta"] > div {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMetricLabel"] {{
    opacity: 0.72;
}}

/* Botones secundarios del contenido principal (sin tocar CTA primario rojo) */
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="secondary"],
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="tertiary"] {{
    background: {main_button_bg} !important;
    color: {main_button_text} !important;
    border: 1px solid {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="secondary"]:hover,
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="tertiary"]:hover {{
    filter: brightness(0.97);
}}

/* Mantener texto correcto dentro de botones (evitar herencia global oscura) */
[data-testid="stMainBlockContainer"] button [data-testid="stMarkdownContainer"] p,
[data-testid="stMainBlockContainer"] button [data-testid="stMarkdownContainer"] span {{
    color: inherit !important;
}}

/* Separadores en contenido principal */
[data-testid="stMainBlockContainer"] hr {{
    border-color: {main_hr_color} !important;
}}

/* Expander de búsqueda manual: borde/contorno visible en ambos temas */
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] {{
    border: 1px solid {main_button_border} !important;
    border-radius: 12px !important;
    background: {expander_bg} !important;
    color-scheme: {theme_color_scheme} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] details,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] > div {{
    background: {expander_bg} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary {{
    background: {expander_summary_bg} !important;
    border-radius: 10px !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary p,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary span {{
    color: var(--text) !important;
}}

/* Inputs dentro del expander: respetar tema claro/oscuro */
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stTextInput"] input,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stNumberInput"] input {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(255,255,255,0.92)'} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-baseweb="input"] {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-color: {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stNumberInput"] button {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(255,255,255,0.92)'} !important;
    border-color: {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] label {{
    color: var(--text) !important;
}}

/* Selectores (multiselect de Mapa/Filtros y otros) siguiendo tema activo */
[data-testid="stMainBlockContainer"] [data-baseweb="select"] > div {{
    background: {main_button_bg} !important;
    border-color: {main_button_border} !important;
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="select"] input {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="tag"] {{
    border-color: {main_button_border} !important;
}}

/* Multiselect de filtros (Mapa): forzar fondo/contraste correctos */
[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
    background: {main_button_bg} !important;
    border-color: {main_button_border} !important;
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] [data-baseweb="tag"] {{
    background: {'rgba(255,75,75,0.95)' if not dark else 'rgba(255,75,75,0.95)'} !important;
    color: #ffffff !important;
    border-color: transparent !important;
}}

[data-baseweb="popover"],
body [data-baseweb="popover"] {{
    color-scheme: {theme_color_scheme} !important;
}}

[data-baseweb="popover"] [role="listbox"],
body [role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
    border: 1px solid {main_button_border} !important;
}}

[data-baseweb="popover"] [role="option"],
body [role="option"],
[data-baseweb="popover"] li,
[data-baseweb="menu"] li {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
}}

[data-baseweb="popover"] [role="option"]:hover,
body [role="option"]:hover,
[data-baseweb="popover"] li:hover,
[data-baseweb="menu"] li:hover {{
    background: {'#eef2f8' if not dark else '#1b2230'} !important;
}}

/* Tablas HTML tematizadas */
.mlbx-table-wrap {{
    width: 100%;
    overflow-x: auto;
    margin: 0.25rem 0 1rem 0;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    box-shadow: var(--shadow);
}}

.mlbx-data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.95rem;
    color: var(--text);
}}

.mlbx-data-table thead th {{
    text-align: left;
    padding: 0.72rem 0.78rem;
    background: {'rgba(233,237,243,0.95)' if not dark else 'rgba(42, 46, 56, 0.96)'};
    color: var(--text);
    border-bottom: 1px solid var(--border);
    font-weight: 700;
}}

.mlbx-data-table tbody td {{
    padding: 0.58rem 0.78rem;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    background: transparent;
}}

.mlbx-data-table tbody tr:last-child td {{
    border-bottom: 0;
}}

[data-testid="stMainBlockContainer"] [role="checkbox"] {{
    width: 1.05rem !important;
    height: 1.05rem !important;
    border: 1px solid {main_button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-radius: 0.25rem !important;
}}

[data-testid="stMainBlockContainer"] [role="checkbox"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggles del contenido principal (estaciones cercanas / mapa) */
[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input + div {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input + div > div {{
    background-color: {'#ffffff' if not dark else '#dbe4f2'} !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input:checked + div {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stMainBlockContainer"] [role="switch"] {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 999px !important;
}}

[data-testid="stMainBlockContainer"] [role="switch"][aria-checked="true"] {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggle real de Streamlit/BaseWeb en contenido principal (mapa) */
[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] {{
    color: var(--text) !important;
    background: transparent !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child {{
    background: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 999px !important;
    box-shadow: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child > div {{
    background: {'#ffffff' if not dark else '#dbe4f2'} !important;
    box-shadow: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    appearance: auto !important;
    -webkit-appearance: auto !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] {{
    color: var(--text) !important;
}}

/* Mapa: controles de zoom y chips de metadatos */
[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group,
.stApp .mapboxgl-ctrl-group,
.stApp .maplibregl-ctrl-group {{
    background: {main_button_bg} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    box-shadow: 0 10px 24px rgba(0,0,0,{'0.12' if not dark else '0.28'}) !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button,
.stApp .mapboxgl-ctrl-group button,
.stApp .maplibregl-ctrl-group button {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
    border-bottom: 1px solid {main_button_border} !important;
    width: 38px !important;
    height: 38px !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button:last-child,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button:last-child,
.stApp .mapboxgl-ctrl-group button:last-child,
.stApp .maplibregl-ctrl-group button:last-child {{
    border-bottom: none !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button:hover,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button:hover,
.stApp .mapboxgl-ctrl-group button:hover,
.stApp .maplibregl-ctrl-group button:hover {{
    background: {'#eef2f8' if not dark else '#1b2230'} !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group .mapboxgl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group .maplibregl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group .maplibregl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group .mapboxgl-ctrl-icon,
.stApp .mapboxgl-ctrl-group .mapboxgl-ctrl-icon,
.stApp .maplibregl-ctrl-group .maplibregl-ctrl-icon,
.stApp .mapboxgl-ctrl-group .maplibregl-ctrl-icon,
.stApp .maplibregl-ctrl-group .mapboxgl-ctrl-icon {{
    filter: none !important;
}}

.mlbx-map-meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem 0.65rem;
    align-items: center;
    margin-top: 0.2rem;
    color: var(--text);
    font-size: 0.98rem;
}}

.mlbx-map-meta-item {{
    color: var(--text);
}}

.mlbx-map-chip {{
    display: inline-flex;
    align-items: center;
    padding: 0.16rem 0.52rem;
    border-radius: 0.55rem;
    background: {'rgba(233,237,243,0.95)' if not dark else 'rgba(24, 29, 38, 0.96)'};
    border: 1px solid {'rgba(15,18,25,0.12)' if not dark else 'rgba(255,255,255,0.10)'};
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(121, 242, 165, 0.96)'};
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-size: 0.95em;
    font-weight: 700;
    letter-spacing: 0.01em;
}}

/* Forzar que todos los headers usen la variable --text */
h1, h2, h3, h4, h5, h6 {{
    color: var(--text) !important;
}}

/* Headers de markdown también */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {{
    color: var(--text) !important;
}}
</style>
""", unsafe_allow_html=True)

# CSS de componentes y responsive mobile
st.markdown(html_clean("""
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MeteoLabX">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/apple-touch-icon-pwa.png?v=5">
<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon-pwa.png?v=5">
<link rel="apple-touch-icon-precomposed" href="/static/apple-touch-icon-pwa.png?v=5">
<meta name="theme-color" content="#2384ff">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png?v=3">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png?v=3">
<link rel="shortcut icon" href="/static/apple-touch-icon-pwa.png?v=5">

<script>
(function () {
  const appWin = window;
  const appDoc = document;
  const hostWin = window.parent || window;
  const head = appDoc.head || document.head;
  if (!head) return;

  function upsertLink(rel, href, sizes) {
    let el = head.querySelector(`link[rel="${rel}"]${sizes ? `[sizes="${sizes}"]` : ""}`);
    if (!el) {
      el = appDoc.createElement("link");
      el.setAttribute("rel", rel);
      if (sizes) el.setAttribute("sizes", sizes);
      head.appendChild(el);
    }
    el.setAttribute("href", href);
  }

  upsertLink("apple-touch-icon", "/static/apple-touch-icon-pwa.png?v=5");
  upsertLink("apple-touch-icon", "/static/apple-touch-icon-pwa.png?v=5", "180x180");
  upsertLink("apple-touch-icon-precomposed", "/static/apple-touch-icon-pwa.png?v=5");
  upsertLink("icon", "/favicon-32x32.png?v=3", "32x32");
  upsertLink("icon", "/favicon-16x16.png?v=3", "16x16");
  upsertLink("shortcut icon", "/static/apple-touch-icon-pwa.png?v=5");

  // Escribir timezone del navegador en query param para que Python la use en modo Auto
  try {
    var _tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    var _vw = Math.round(appWin.innerWidth || appDoc.documentElement.clientWidth || 0);
    var _url = new URL(hostWin.location.href);
    if (_url.searchParams.get("_tz") !== _tz || _url.searchParams.get("_vw") !== String(_vw)) {
      _url.searchParams.set("_tz", _tz);
      _url.searchParams.set("_vw", String(_vw));
      hostWin.history.replaceState({}, "", _url.toString());
    }
  } catch (_e) {}
})();
</script>

<style>
  .block-container { 
    padding-top: 1.2rem; 
    max-width: 1200px;
  }

  .header{
    display:flex; 
    align-items:center; 
    justify-content:space-between;
    margin-bottom: 0.1rem;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .header h1{ 
    margin:0; 
    font-size:2.0rem; 
    color:var(--text); 
  }
  .header-sub{
    margin: 0;
    font-size: 0.82rem;
    color: var(--muted);
    opacity: 0.9;
    font-weight: 500;
  }
  .meta{ 
    color:var(--muted); 
    font-size:0.95rem; 
  }

  .station-count{
    margin: 0 0 0.45rem 0;
  }
  .station-selector-gap{
    height: 0.42rem;
  }

  /* CTA primario geolocalización */
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="primary"]{
    background: linear-gradient(135deg, #d62828, #b51717) !important;
    border: 1px solid #a41212 !important;
    color: #ffffff !important;
    font-weight: 700 !important;
  }
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="primary"]:hover{
    background: linear-gradient(135deg, #e63946, #c1121f) !important;
    border: 1px solid #b10f1a !important;
  }
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"]{
    margin-top: 0 !important;
    margin-bottom: 0.12rem !important;
  }

  .section-title{
    margin-top: 1.2rem;
    margin-bottom: 0.8rem;
    font-weight: 800;
    color: var(--text);
    letter-spacing: 0.2px;
    font-size: 1.15rem;
  }

  .grid{
    display: grid;
    gap: 16px;
    overflow: visible;
  }

  .grid-row-spacing{
    margin-top: 16px;
  }

  .grid-6{
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }

  .grid-4{
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .grid-3{
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  /* Tablets grandes */
  @media (max-width: 1300px){
    .grid-6{ grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }

  /* Tablets */
  @media (max-width: 1000px){
    .grid-3{ grid-template-columns: repeat(2, 1fr); }
  }

  /* Tablets pequeñas */
  @media (max-width: 900px){
    .grid-6{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .grid-4{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }

  /* Móviles grandes */
  @media (max-width: 600px){
    .block-container { 
      padding-top: 0.8rem;
      padding-left: 1rem;
      padding-right: 1rem;
    }
    
    .grid-3, .grid-4, .grid-6 { 
      grid-template-columns: 1fr; 
      gap: 12px;
    }

    .grid-thermo.grid-4 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    
    .header h1 { 
      font-size: 1.6rem; 
    }
    
    .section-title {
      font-size: 1rem;
      margin-top: 1rem;
      margin-bottom: 0.6rem;
    }
    
    .meta {
      font-size: 0.85rem;
    }

    [data-testid="stPlotlyChart"] {
      margin-left: -0.8rem !important;
      margin-right: -0.45rem !important;
      width: calc(100% + 1.25rem) !important;
      max-width: calc(100% + 1.25rem) !important;
      overflow: visible !important;
    }

    [data-testid="stPlotlyChart"] > div,
    [data-testid="stPlotlyChart"] .js-plotly-plot,
    [data-testid="stPlotlyChart"] .plot-container,
    [data-testid="stPlotlyChart"] .svg-container {
      width: 100% !important;
      max-width: 100% !important;
      overflow: visible !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .plot-container {
      margin-left: 0 !important;
      margin-right: 0 !important;
      width: 100% !important;
    }

    [data-testid="stPlotlyChart"] .main-svg .cartesianlayer,
    [data-testid="stPlotlyChart"] .main-svg .gridlayer,
    [data-testid="stPlotlyChart"] .main-svg .zerolinelayer,
    [data-testid="stPlotlyChart"] .main-svg .xaxislayer-above,
    [data-testid="stPlotlyChart"] .main-svg .xaxislayer-below,
    [data-testid="stPlotlyChart"] .main-svg .shapelayer,
    [data-testid="stPlotlyChart"] .main-svg .imagelayer,
    [data-testid="stPlotlyChart"] .main-svg .overplot {
      transform-box: fill-box !important;
      transform-origin: left center !important;
      transform: translateX(-20px) scaleX(1.11) !important;
    }

    [data-testid="stPlotlyChart"] .main-svg .yaxislayer-above,
    [data-testid="stPlotlyChart"] .main-svg .yaxislayer-below {
      transform-box: fill-box !important;
      transform-origin: left center !important;
      transform: translateX(-2px) !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-above > .y2tick,
    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-below > .y2tick {
      transform: translateX(-22px) !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .g-ytitle,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-xtitle,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-x2title,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-y2title {
      display: none !important;
    }

    [data-testid="stPlotlyChart"] .main-svg .infolayer:has(.legend) .g-gtitle,
    [data-testid="stPlotlyChart"] .main-svg .infolayer:has(.legend) .gtitle {
      display: none !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .xaxislayer-above > .xtick text {
      font-size: 10px !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-above > .ytick text,
    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-above > .y2tick text {
      font-size: 10px !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .xaxislayer-above > .xtick:not(:nth-of-type(4n+1)) text {
      display: none !important;
    }
  }

  /* Móviles pequeños */
  @media (max-width: 400px){
    .block-container {
      padding-left: 0.75rem;
      padding-right: 0.75rem;
    }
    
    .header h1 { 
      font-size: 1.4rem; 
    }
    
    .grid {
      gap: 10px;
    }
  }

  .card{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 22px;
    box-shadow: var(--shadow);
    padding: 14px;
    min-height: 0;
    backdrop-filter: blur(12px);
    transition: transform .12s ease;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  
  /* Deshabilitar hover en móviles táctiles */
  @media (hover: hover) {
    .card:hover{ transform: translateY(-2px); }
  }

  .card.card-h{
    flex-direction: row;
    align-items: flex-start;
    gap: 14px;
    position: relative;
    overflow: visible;
  }

  .card-help-wrap{
    position: absolute;
    top: auto;
    bottom: 10px;
    right: 10px;
    z-index: 8;
    display: inline-flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
  }

  .card-help-btn{
    width: 16px;
    height: 16px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.62rem;
    font-weight: 800;
    line-height: 1;
    background: rgba(0, 0, 0, 0.26);
    color: rgba(255, 255, 255, 0.78);
    user-select: none;
    cursor: help;
  }

  .card-help-tooltip{
    min-width: 260px;
    max-width: min(420px, calc(100vw - 44px));
    padding: 9px 10px;
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    background: rgba(14, 18, 26, 0.96);
    color: rgba(248, 251, 255, 0.96);
    font-size: 0.74rem;
    line-height: 1.34;
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
    opacity: 0;
    transform: translateY(4px);
    transition: opacity .15s ease, transform .15s ease;
    pointer-events: none;
    text-align: left;
    position: absolute;
    right: 0;
    bottom: calc(100% + 8px);
    z-index: 9999;
  }

  .card-help-wrap:hover .card-help-tooltip,
  .card-help-wrap:focus-within .card-help-tooltip,
  .card-help-wrap:focus .card-help-tooltip{
    opacity: 1;
    transform: translateY(0);
  }
  
  /* Tarjetas en layout compacto en móviles */
  @media (max-width: 420px){
    .card {
      padding: 12px;
      border-radius: 18px;
    }
    
    /* Mantener layout horizontal pero más compacto */
    .card.card-h {
      gap: 10px;
    }
  }
  
  /* Layout vertical solo en móviles muy pequeños */
  @media (max-width: 360px){
    .card.card-h {
      flex-direction: column;
      gap: 10px;
    }
  }

  .icon-col{
    flex: 0 0 auto;
    display: flex;
    align-items: flex-start;
    padding-top: 2px;
  }

  .content-col{
    flex: 1 1 auto;
    min-width: 0;
  }

.side-col{
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: center;
  gap: 4px;
  margin-left: 10px;
  min-width: 52px;
}
.side-col .max,
.side-col .min{
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1.15;
  white-space: nowrap;
}

/* Optimizar side-col en móviles */
@media (max-width: 420px){
  .side-col {
    min-width: 44px;
    margin-left: 8px;
    gap: 3px;
  }
  
  .side-col .max,
  .side-col .min {
    font-size: 0.90rem;
  }
}

@media (max-width: 360px){
  .side-col {
    min-width: 42px;
    margin-left: 6px;
    gap: 2px;
  }
  
  .side-col .max,
  .side-col .min {
    font-size: 0.86rem;
  }
}

  .card-title{
    color: var(--muted);
    font-size: 0.78rem;
    font-weight: 800;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    margin-top: 2px;
    white-space: normal;
    overflow: visible;
    line-height: 1.15;
  }

  .card-value{
    margin-top: 6px;
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--text);
    line-height: 1.1;
    white-space: nowrap;
  }

  .grid-basic .card-value{
    font-size: 2.4rem;
    font-weight: 700;
    line-height: 1.05;
  }
  
  /* Tamaños de fuente optimizados para móviles */
  @media (max-width: 600px){
    .card-title {
      font-size: 0.72rem;
      letter-spacing: 0.4px;
    }
    
    .card-value {
      font-size: 1.6rem;
      margin-top: 4px;
    }
    
    .grid-basic .card-value {
      font-size: 2.0rem;
    }
  }
  
  /* iPhone estándar (390-420px) - reducir aún más para dar espacio a max/min */
  @media (max-width: 420px){
    .card-value {
      font-size: 1.5rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.85rem;
    }
    
    .card-title {
      font-size: 0.70rem;
    }
  }
  
  @media (max-width: 360px){
    .card-value {
      font-size: 1.4rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.7rem;
    }
  }
  
  @media (max-width: 400px){
    .card-value {
      font-size: 1.5rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.8rem;
    }
  }

  .unit{
    margin-left: 6px;
    font-size: 1.0rem;
    color: var(--muted);
    font-weight: 600;
  }
  
  @media (max-width: 600px){
    .unit {
      font-size: 0.85rem;
      margin-left: 4px;
    }
  }
  
  @media (max-width: 420px){
    .unit {
      font-size: 0.80rem;
      margin-left: 3px;
    }
  }

  .icon.big{
    width: 54px; height: 54px;
    border-radius: 18px;
    display:flex; align-items:center; justify-content:center;
    flex: 0 0 auto;
    background: transparent;
    box-shadow: none;
  }

  .rose-stats-grid{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    column-gap: 14px;
    row-gap: 6px;
    margin-top: 0.15rem;
  }

  .rose-stat-item{
    margin: 0;
    font-size: 0.98rem;
    line-height: 1.45;
    color: var(--text);
  }

  .rose-stat-item.is-dominant{
    font-weight: 800;
  }

  .icon-img{
    width: 54px;
    height: 54px;
    display:block;
    image-rendering: auto;
    filter: none;
  }
  
  /* Iconos más pequeños en móviles */
  @media (max-width: 600px){
    .icon.big {
      width: 48px;
      height: 48px;
    }
    
    .icon-img {
      width: 48px;
      height: 48px;
    }
  }
  
  @media (max-width: 400px){
    .icon.big {
      width: 42px;
      height: 42px;
    }
    
    .icon-img {
      width: 42px;
      height: 42px;
    }
  }

  .subtitle{
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.35;
  }

  .subtitle div{
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .subtitle b{ color: var(--text); font-weight: 600; }
  
  @media (max-width: 600px){
    .subtitle {
      font-size: 0.82rem;
      margin-top: 8px;
    }
  }
  
  /* Sidebar colapsada por defecto en móviles pero accesible */
  @media (max-width: 768px){
    /* Ocultar contenido del sidebar cuando está colapsada */
    [data-testid="stSidebar"][aria-expanded="false"] > div {
      display: none;
    }
    
    /* Reducir ancho del sidebar colapsado para evitar texto flotante */
    [data-testid="stSidebar"][aria-expanded="false"] {
      width: 0 !important;
      min-width: 0 !important;
      overflow: hidden;
    }
    
    /* Mostrar normalmente cuando está expandida */
    [data-testid="stSidebar"][aria-expanded="true"] {
      width: 21rem !important;
    }
    
    /* Asegurar que el botón de colapsar está visible */
    button[data-testid="collapsedControl"] {
      display: flex !important;
    }
  }
  
  /* Optimizar tabs en móviles */
  @media (max-width: 600px){
    [data-baseweb="tab-list"] {
      gap: 8px;
    }
    
    [data-baseweb="tab"] {
      font-size: 0.85rem !important;
      padding: 8px 12px !important;
    }
  }
</style>
"""), unsafe_allow_html=True)

_inject_mobile_plotly_compactor()
_inject_live_age_updater()

# Registro del Service Worker para PWA
st.markdown(html_clean("""
<script>
  // Registrar Service Worker para funcionalidad PWA
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/static/sw.js')
        .then(registration => {
          console.log('SW registrado:', registration.scope);
        })
        .catch(err => {
          console.log('SW falló:', err);
        });
    });
  }
  
  // Prompt de instalación PWA
  let deferredPrompt;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    console.log('PWA instalable');
  });
  
  // Detectar si ya está instalado como PWA
  window.addEventListener('appinstalled', () => {
    console.log('PWA instalada');
    deferredPrompt = null;
  });
</script>
"""), unsafe_allow_html=True)

# ============================================================
# HEADER
# ============================================================

def _provider_refresh_seconds() -> int:
    """Intervalo de refresh sugerido según proveedor conectado."""
    provider_id = st.session_state.get("connection_type", "")

    # Permite override por proveedor futuro sin tocar core.
    custom_value = st.session_state.get("provider_refresh_seconds")
    if custom_value not in (None, ""):
        try:
            return max(MIN_REFRESH_SECONDS, int(custom_value))
        except Exception:
            pass

    defaults = {
        "AEMET": 600,  # AEMET reporta típicamente en ventanas de ~10 min
        "METEOCAT": 600,  # Meteocat XEMA actualiza en base semihoraria/horaria según estación
        "EUSKALMET": 600,  # Euskalmet suele reportar en slots de 10 min
        "FROST": 300,  # Frost ofrece dato subhorario y series densas para muchas estaciones
        "METEOFRANCE": 300,  # Meteo-France ofrece dato actual a 6 min y serie horaria del día
        "METEOGALICIA": 600,  # MeteoGalicia ofrece estado y serie horaria reciente
        "NWS": 600,  # NWS suele actualizar en intervalo subhorario según estación
        "POEM": 300,  # POEM dispone de endpoints TR y series con mayor frecuencia
        "WU": REFRESH_SECONDS,
    }
    return int(defaults.get(provider_id, REFRESH_SECONDS))


def _disconnect_active_station() -> None:
    """Desconecta la estación activa (mismo criterio que el botón del sidebar)."""
    if str(st.session_state.get("connection_type", "")).strip().upper() == "AEMET":
        try:
            _get_aemet_service().clear_aemet_runtime_cache()
        except Exception:
            pass
    st.session_state["connected"] = False
    st.session_state["connection_type"] = None
    for key in ["wu_connected_station", "wu_connected_api_key", "wu_connected_z"]:
        if key in st.session_state:
            del st.session_state[key]
    for state_key in list(st.session_state.keys()):
        if (
            state_key.startswith("aemet_")
            or state_key.startswith("provider_station_")
            or state_key.startswith("meteocat_")
            or state_key.startswith("euskalmet_")
            or state_key.startswith("frost_")
            or state_key.startswith("meteofrance_")
            or state_key.startswith("meteogalicia_")
            or state_key.startswith("nws_")
            or state_key.startswith("poem_")
        ):
            del st.session_state[state_key]


def _pressure_decimals_for_provider(provider_id: str) -> int:
    return 0 if str(provider_id).strip().upper() == "WU" else 1


def _fmt_pressure_for_provider(value, provider_id: str) -> str:
    try:
        v = float(value)
    except Exception:
        return "—"
    if is_nan(v):
        return "—"
    decimals = _pressure_decimals_for_provider(provider_id)
    return f"{v:.{decimals}f}"


@st.cache_data(ttl=3600)
def _total_catalog_stations() -> int:
    files = [
        "data_estaciones_aemet.json",
        "data_estaciones_meteocat.json",
        "data_estaciones_euskalmet.json",
        "data_estaciones_frost.json",
        "data_estaciones_meteofrance.json",
        "data_estaciones_meteogalicia.json",
        "data_estaciones_nws.json",
        "data_estaciones_poem.json",
    ]

    def _count_payload(payload) -> int:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in (
                "stations",
                "estaciones",
                "listaEstacionsMeteo",
                "lista_estaciones",
                "items",
                "data",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    return len(value)
        return 0

    total = 0
    for filename in files:
        path = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            total += _count_payload(payload)
        except Exception:
            continue
    return int(total)


header_refresh_seconds = _provider_refresh_seconds() if st.session_state.get("connected", False) else REFRESH_SECONDS
header_refresh_label = (
    f"{header_refresh_seconds // 60} min"
    if header_refresh_seconds % 60 == 0 and header_refresh_seconds >= 60
    else f"{header_refresh_seconds}s"
)

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 9</span></h1>
      <div class="meta">
        Versión beta — la interfaz y las funciones pueden cambiar ·
        Tema: {"Oscuro" if dark else "Claro"} · Refresh: {header_refresh_label}
      </div>
    </div>
    <div class="header-sub station-count">{_total_catalog_stations()} estaciones disponibles</div>
    """),
    unsafe_allow_html=True
)


# ============================================================
# COMPROBACIÓN DE CONEXIÓN
# ============================================================

connected = st.session_state.get("connected", False)

if connected:
    provider_id = str(st.session_state.get("connection_type", "")).strip().upper()

    if provider_id == "AEMET":
        station_name = st.session_state.get("aemet_station_name") or st.session_state.get("provider_station_name") or "Estación AEMET"
        station_id = st.session_state.get("aemet_station_id") or st.session_state.get("provider_station_id") or "—"
        lat = st.session_state.get("aemet_station_lat", st.session_state.get("provider_station_lat", st.session_state.get("station_lat")))
        lon = st.session_state.get("aemet_station_lon", st.session_state.get("provider_station_lon", st.session_state.get("station_lon")))
        alt = st.session_state.get("aemet_station_alt", st.session_state.get("provider_station_alt", st.session_state.get("station_elevation")))
    elif provider_id == "WU":
        station_name = st.session_state.get("provider_station_name") or st.session_state.get("active_station") or "Estación WU"
        station_id = st.session_state.get("provider_station_id") or st.session_state.get("active_station") or "—"
        lat = st.session_state.get("provider_station_lat", st.session_state.get("station_lat"))
        lon = st.session_state.get("provider_station_lon", st.session_state.get("station_lon"))
        alt = st.session_state.get("provider_station_alt", st.session_state.get("station_elevation", st.session_state.get("active_z")))
    else:
        station_name = st.session_state.get("provider_station_name", "Estación")
        station_id = st.session_state.get("provider_station_id", "—")
        lat = st.session_state.get("provider_station_lat", st.session_state.get("station_lat"))
        lon = st.session_state.get("provider_station_lon", st.session_state.get("station_lon"))
        alt = st.session_state.get("provider_station_alt", st.session_state.get("station_elevation"))

    def _fmt_num(value, ndigits=2):
        try:
            v = float(value)
            if is_nan(v):
                return "—"
            return f"{v:.{ndigits}f}"
        except Exception:
            return "—"

    alt_txt = _fmt_num(alt, ndigits=0)
    lat_txt = _fmt_num(lat, ndigits=4)
    lon_txt = _fmt_num(lon, ndigits=4)

    badge_bg = "rgba(56, 92, 132, 0.35)" if dark else "rgba(51, 126, 215, 0.12)"
    badge_border = "rgba(92, 158, 230, 0.45)" if dark else "rgba(51, 126, 215, 0.28)"
    badge_text = "rgba(142, 201, 255, 0.96)" if dark else "rgba(34, 93, 170, 0.96)"

    station_col, action_col = st.columns([0.84, 0.16], gap="small")
    with station_col:
        st.markdown(
            html_clean(
                f"""
                <div style="
                    margin: 0.2rem 0 0.75rem 0;
                    display: inline-block;
                    padding: 0.52rem 0.82rem;
                    border-radius: 14px;
                    border: 1px solid {badge_border};
                    background: {badge_bg};
                    color: {badge_text};
                    font-size: 0.88rem;
                    font-weight: 600;
                    line-height: 1.45;
                ">
                    <div>📡 {provider_id} · <b>{station_name}</b></div>
                    <div style="font-weight:500; opacity:0.92;">ID: {station_id} · Alt: {alt_txt} m · Lat: {lat_txt} · Lon: {lon_txt}</div>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )
    with action_col:
        st.markdown("<div style='height:0.28rem;'></div>", unsafe_allow_html=True)
        if st.button("Desconectar", key="disconnect_header_btn", width="stretch"):
            _disconnect_active_station()
            try:
                st.rerun()
            except Exception:
                st.experimental_rerun()

if not connected:
    st.markdown(
        html_clean(
            """
            <div style="
                margin: 0.35rem 0 0.0rem 0;
                padding: 0.9rem 1rem;
                border-radius: 10px;
                background: rgba(66, 133, 244, 0.20);
                color: rgb(47, 156, 255);
                font-weight: 500;
            ">
                👈 Conecta tu estación desde el panel lateral o explora estaciones cercanas.
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    # Mostrar selector de estaciones en pantalla principal
    render_station_selector()



# ============================================================
# OBTENCIÓN Y PROCESAMIENTO DE DATOS
# ============================================================

# Valores por defecto (se usan cuando no hay conexión)
base = {
    "Tc": float("nan"),
    "RH": float("nan"),
    "p_hpa": float("nan"),
    "Td": float("nan"),
    "wind": float("nan"),
    "gust": float("nan"),
    "feels_like": float("nan"),
    "heat_index": float("nan"),
    "wind_dir_deg": float("nan"),
    "precip_total": float("nan"),
    "solar_radiation": float("nan"),
    "uv": float("nan"),
    "epoch": 0,
    "temp_max": None,
    "temp_min": None,
    "rh_max": None,
    "rh_min": None,
    "gust_max": None,
}

z = 0
inst_mm_h = float("nan")
r1_mm_h = float("nan")
r5_mm_h = float("nan")
inst_label = "—"
p_abs = float("nan")
p_msl = float("nan")
p_abs_disp = "—"
p_msl_disp = "—"
dp3 = float("nan")
p_label = "—"
p_arrow = "•"
e = float("nan")
q_gkg = float("nan")
theta = float("nan")
Tv = float("nan")
Te = float("nan")
Tw = float("nan")
lcl = float("nan")
rho = float("nan")
rho_v_gm3 = float("nan")

# Radiación
solar_rad = float("nan")
uv = float("nan")
et0 = float("nan")
clarity = float("nan")
balance = float("nan")
has_radiation = False  # Flag para saber si hay datos de radiación

# Gráficos
chart_epochs = []
chart_temps = []
has_chart_data = False

# Solo calcular datos si está conectado
if connected:
    # Determinar origen de datos
    if _get_aemet_service().is_aemet_connection():
        # ========== DATOS DE AEMET ==========
        aemet_service = _get_aemet_service()
        
        # Primero obtener datos históricos (más frescos, cada 10 min)
        (
            chart_epochs,
            chart_temps,
            chart_humidities,
            chart_pressures,
            chart_winds,
            chart_gusts,
            chart_wind_dirs,
            chart_precips,
        ) = aemet_service.get_aemet_daily_charts()
        has_chart_data = len(chart_epochs) > 0
        
        print(f"🔍 [DEBUG] get_aemet_daily_charts() devolvió: {len(chart_epochs)} epochs")
        print(f"🔍 [DEBUG] has_chart_data = {has_chart_data}")
        
        # Obtener dato actual del endpoint normal (puede ser antiguo)
        base = aemet_service.get_aemet_data()
        
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de AEMET por ahora. Intenta de nuevo en unos minutos.")
            err_detail = str(st.session_state.get("aemet_last_error", "")).strip()
            if err_detail:
                st.caption(f"Detalle técnico AEMET: {err_detail}")
            st.stop()
        
        # Si tenemos datos históricos, usar el último punto como dato actual (más fresco)
        if has_chart_data:
            logger.info(f"[AEMET] Serie diezminutal disponible ({len(chart_epochs)} puntos)")
            # No heredar máximos de viento del endpoint "actual" (puede venir desfasado)
            base["gust_max"] = None
            
            # Último punto del gráfico
            last_idx = -1
            from datetime import datetime
            chart_last_epoch = chart_epochs[last_idx]
            base_epoch = base.get("epoch", 0)
            use_chart_for_current = (
                is_nan(base_epoch)
                or base_epoch <= 0
                or chart_last_epoch > base_epoch
            )
            
            print(f"📊 [DEBUG] Datos endpoint normal: epoch={base['epoch']} → {datetime.fromtimestamp(base['epoch']).strftime('%H:%M')}, T={base['Tc']:.1f}°C")
            print(f"📊 [DEBUG] Último punto gráfico: epoch={chart_epochs[last_idx]} → {datetime.fromtimestamp(chart_epochs[last_idx]).strftime('%H:%M')}, T={chart_temps[last_idx]:.1f}°C")
            
            # Panel principal: usar SIEMPRE la fuente más fresca (actual vs serie)
            if use_chart_for_current:
                base["epoch"] = chart_last_epoch
                if not is_nan(chart_temps[last_idx]):
                    base["Tc"] = chart_temps[last_idx]
                if not is_nan(chart_humidities[last_idx]):
                    base["RH"] = chart_humidities[last_idx]
                if not is_nan(chart_pressures[last_idx]):
                    # PRES diezminutal es presión de estación; forzar recálculo de MSLP
                    base["p_station"] = chart_pressures[last_idx]
                    base["p_hpa"] = None
                if not is_nan(chart_winds[last_idx]):
                    base["wind"] = chart_winds[last_idx]
                if not is_nan(chart_gusts[last_idx]):
                    base["gust"] = chart_gusts[last_idx]
                if not is_nan(chart_wind_dirs[last_idx]):
                    base["wind_dir_deg"] = chart_wind_dirs[last_idx]
                if not is_nan(chart_precips[last_idx]):
                    base["precip_total"] = chart_precips[last_idx]
                logger.info("[AEMET] Panel actualizado con último punto diezminutal (más fresco)")
            else:
                logger.info("[AEMET] Panel mantiene dato actual (más fresco que la serie)")
            
            print(f"✅ [DEBUG] Dato actualizado: {datetime.fromtimestamp(base['epoch']).strftime('%H:%M')}, T={base['Tc']:.1f}°C, RH={base['RH']:.1f}%")
            
            # Calcular max/min solo del día ACTUAL (desde medianoche de hoy)
            from datetime import datetime
            now_local = datetime.now()
            today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_epoch = int(today_start.timestamp())
            
            # Filtrar solo puntos del día actual
            temps_hoy = []
            gusts_hoy = []
            winds_hoy = []
            precs_hoy = []
            for epoch, temp, gust, wind in zip(chart_epochs, chart_temps, chart_gusts, chart_winds):
                if epoch >= today_start_epoch and not is_nan(temp):
                    temps_hoy.append(temp)
                if epoch >= today_start_epoch:
                    if not is_nan(gust):
                        gusts_hoy.append(gust)
                    if not is_nan(wind):
                        winds_hoy.append(wind)

            for epoch, prec in zip(chart_epochs, chart_precips):
                if epoch >= today_start_epoch and not is_nan(prec):
                    # Normalizar valores negativos espurios
                    precs_hoy.append(max(0.0, float(prec)))
            
            if len(temps_hoy) > 0:
                # La card de temperatura usa temp_max/temp_min
                base["temp_max"] = max(temps_hoy)
                base["temp_min"] = min(temps_hoy)
                print(
                    f"✅ [DEBUG] Max/Min del DÍA ACTUAL ({len(temps_hoy)} puntos desde "
                    f"{today_start.strftime('%H:%M')}): {base['temp_max']:.1f}°C / {base['temp_min']:.1f}°C"
                )
            else:
                print(f"⚠️ [DEBUG] No hay datos del día actual - usando del endpoint normal")

            wind_candidates = gusts_hoy + winds_hoy
            if len(wind_candidates) > 0:
                # La card de viento usa gust_max en la esquina derecha
                base["gust_max"] = max(wind_candidates)
                print(
                    f"✅ [DEBUG] Racha máxima del DÍA ACTUAL ({len(wind_candidates)} puntos desde "
                    f"{today_start.strftime('%H:%M')}): {base['gust_max']:.1f} km/h"
                )
            else:
                base["gust_max"] = None
                print("⚠️ [DEBUG] No hay rachas válidas del día actual - gust_max oculto")

            dir_validas = [
                float(d)
                for d, w, g in zip(chart_wind_dirs, chart_winds, chart_gusts)
                if (not is_nan(d)) and (
                    (not is_nan(w) and float(w) > 0.3) or
                    (not is_nan(g) and float(g) > 0.3)
                )
            ]
            if len(dir_validas) == 0:
                base["wind_dir_deg"] = float("nan")

            # Precipitación de hoy desde diezminutal (evitar endpoint actual desfasado)
            if len(precs_hoy) > 0:
                # Detectar si la serie parece acumulada (monótona) o incremental.
                diffs = [precs_hoy[i] - precs_hoy[i - 1] for i in range(1, len(precs_hoy))]
                non_negative_ratio = (
                    sum(1 for d in diffs if d >= -0.05) / len(diffs)
                    if len(diffs) > 0 else 1.0
                )

                if non_negative_ratio >= 0.8:
                    # Serie acumulada: sumar incrementos positivos, tolerando reseteos.
                    total_today = 0.0
                    for i in range(1, len(precs_hoy)):
                        d = precs_hoy[i] - precs_hoy[i - 1]
                        if d >= 0:
                            total_today += d
                        else:
                            # Reset del contador: arrancar desde el nuevo valor.
                            total_today += max(0.0, precs_hoy[i])
                else:
                    # Serie incremental por paso (10 min): sumar directamente.
                    total_today = sum(precs_hoy)

                base["precip_total"] = max(0.0, total_today)
                print(
                    f"✅ [DEBUG] Precipitación HOY desde diezminutal ({len(precs_hoy)} puntos): "
                    f"{base['precip_total']:.2f} mm"
                )
            else:
                base["precip_total"] = float("nan")
                print("⚠️ [DEBUG] Sin datos de precipitación diezminutal hoy")
            
            # Guardar en session_state para que estén disponibles en tab Tendencias
            st.session_state["chart_epochs"] = chart_epochs
            st.session_state["chart_temps"] = chart_temps
            st.session_state["chart_humidities"] = chart_humidities
            st.session_state["chart_dewpts"] = []
            st.session_state["chart_pressures"] = chart_pressures
            st.session_state["chart_uv_indexes"] = []
            st.session_state["chart_solar_radiations"] = []
            st.session_state["chart_winds"] = chart_winds
            st.session_state["chart_gusts"] = chart_gusts
            st.session_state["chart_wind_dirs"] = chart_wind_dirs
        else:
            print(f"⚠️ [DEBUG] No hay datos de gráficos - usando datos del endpoint normal")
            # Evitar extremos desfasados cuando no hay serie diezminutal válida
            base["temp_max"] = None
            base["temp_min"] = None
            base["gust_max"] = None
            st.session_state["chart_epochs"] = []
            st.session_state["chart_temps"] = []
            st.session_state["chart_humidities"] = []
            st.session_state["chart_dewpts"] = []
            st.session_state["chart_pressures"] = []
            st.session_state["chart_uv_indexes"] = []
            st.session_state["chart_solar_radiations"] = []
            st.session_state["chart_winds"] = []
            st.session_state["chart_gusts"] = []
            st.session_state["chart_wind_dirs"] = []
        
        # AEMET devuelve datos ya parseados en formato compatible
        # Guardar timestamp
        st.session_state["last_update_time"] = time.time()
        
        # Guardar coordenadas
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))
        
        # Altitud de AEMET
        z = base.get("elevation", st.session_state.get("aemet_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "AEMET"
        
        # Advertir si los datos son muy antiguos
        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(f"⚠️ Datos de AEMET con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
            logger.warning(f"Datos AEMET antiguos: {data_age_minutes:.1f} minutos")
        
        logger.info(f"Datos AEMET obtenidos para estación {base.get('idema')} - Edad: {data_age_minutes:.1f} min")
        
        # ========== PROCESAMIENTO DE DATOS AEMET ==========
        
        # Lluvia: acumulada del día desde endpoint diario + intensidad minutal (72).
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
        rain_1min_mm = base.get("rain_1min_mm", float("nan"))
        if not is_nan(rain_1min_mm):
            r1_mm_h = rain_1min_mm * 60.0
            inst_mm_h = r1_mm_h
            r5_mm_h = float("nan")
        inst_label = rain_intensity_label(inst_mm_h)
        
        # Presión - AEMET puede devolver None si no tiene dato
        p_hpa_raw = base.get("p_hpa")
        if p_hpa_raw is None or p_hpa_raw == "":
            # Si no hay presión nivel del mar, intentar con presión de estación
            p_station_raw = base.get("p_station")
            if p_station_raw is not None and p_station_raw != "":
                # Tenemos presión de estación, calcular MSLP
                p_abs = float(p_station_raw)
                # Calcular MSLP desde presión de estación (inverso de msl_to_absolute)
                # Aproximación simple: p_msl ≈ p_station * exp(z / 8000)
                import math
                p_msl = p_abs * math.exp(z / 8000.0)
            else:
                # No hay ningún dato de presión
                p_msl = float("nan")
                p_abs = float("nan")
        else:
            # Tenemos MSLP, calcular presión absoluta
            p_msl = float(p_hpa_raw)
            p_abs = msl_to_absolute(p_msl, z, base["Tc"])
        
        provider_for_pressure = st.session_state.get("connection_type", "AEMET")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)
        has_pressure_now = not is_nan(p_msl) and not is_nan(p_abs)
        
        if has_pressure_now:
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if has_pressure_now:
            # Tendencia de presión 3h usando diezminutal (si hay datos de barómetro).
            # Si no hay serie válida, fallback automático al comportamiento existente.
            trend_p_now = p_msl
            trend_epoch_now = base["epoch"]
            trend_p_3h = None
            trend_epoch_3h = None

            if has_chart_data:
                press_valid = []
                for ep, p_st in zip(chart_epochs, chart_pressures):
                    if not is_nan(p_st):
                        press_valid.append((ep, p_st))

                if len(press_valid) >= 2:
                    press_valid.sort(key=lambda x: x[0])
                    ep_now, p_station_now = press_valid[-1]
                    target_ep = ep_now - (3 * 3600)
                    ep_3h, p_station_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))

                    # Convertir presión de estación a MSL con el mismo factor para ambos puntos
                    import math
                    msl_factor = math.exp(z / 8000.0)
                    trend_p_now = p_station_now * msl_factor
                    trend_epoch_now = ep_now
                    trend_p_3h = p_station_3h * msl_factor
                    trend_epoch_3h = ep_3h

                    logger.info(
                        "[AEMET] Tendencia presión 3h desde diezminutal: "
                        f"t_now={ep_now}, t_old={ep_3h}, p_now={trend_p_now:.2f}, p_old={trend_p_3h:.2f}"
                    )

            # Tendencia de presión
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=trend_p_now,
                epoch_now=trend_epoch_now,
                p_3h_ago=trend_p_3h,
                epoch_3h_ago=trend_epoch_3h
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"
        
        # Inicializar variables termodinámicas
        e_sat = float("nan")
        e = float("nan")
        Td_calc = float("nan")
        Tw = float("nan")
        q = float("nan")
        q_gkg = float("nan")
        theta = float("nan")
        Tv = float("nan")
        Te = float("nan")
        rho = float("nan")
        rho_v_gm3 = float("nan")
        lcl = float("nan")
        
        # Termodinámica básica - NO necesita presión (solo T y RH)
        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)

            # Actualizar base con Td calculado
            base["Td"] = Td_calc
            
            # Termodinámica avanzada - SÍ necesita presión
            if not is_nan(p_abs):
                q = specific_humidity(e, p_abs)
                q_gkg = q * 1000
                theta = potential_temperature(base["Tc"], p_abs)
                Tv = virtual_temperature(base["Tc"], q)
                Te = equivalent_temperature(base["Tc"], q)
                rho = air_density(p_abs, Tv)
                rho_v_gm3 = absolute_humidity(e, base["Tc"])
                lcl = lcl_height(base["Tc"], Td_calc)
        else:
            base["Td"] = float("nan")

        # Sensación térmica y Heat Index (calculados, nunca del API)
        wind_fl = base.get("wind", 0.0)
        if is_nan(wind_fl):
            wind_fl = 0.0
        wind_fl_ms = float(wind_fl) / 3.6
        base["feels_like"] = apparent_temperature(base["Tc"], e, wind_fl_ms)
        base["heat_index"] = heat_index_rothfusz(base["Tc"], base.get("RH", float("nan")))

        # Radiación (no disponible en AEMET)
        solar_rad = float("nan")
        uv = float("nan")
        et0 = float("nan")
        clarity = float("nan")
        balance = float("nan")
        has_radiation = False

    elif _get_euskalmet_service().is_euskalmet_connection():
        # ========== DATOS DE EUSKALMET ==========
        base = _get_euskalmet_service().get_euskalmet_data()
        if base is None:
            err_detail = str(st.session_state.get("euskalmet_last_error", "")).strip()
            st.warning(
                "⚠️ No se pudieron obtener datos de Euskalmet. "
                "Se intenta generar JWT automáticamente desde "
                "`EUSKALMET_PRIVATE_KEY_PATH` / `EUSKALMET_PUBLIC_KEY_PATH`."
            )
            if err_detail:
                st.caption(f"Detalle técnico Euskalmet: {err_detail}")
            st.stop()

        _r = process_standard_provider(base, "EUSKALMET", "euskalmet_station_alt")
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_meteocat_service().is_meteocat_connection():
        # ========== DATOS DE METEOCAT ==========
        base = _get_meteocat_service().get_meteocat_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de Meteocat por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        _r = process_standard_provider(
            base, "METEOCAT", "meteocat_station_alt",
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_meteofrance_service().is_meteofrance_connection():
        # ========== DATOS DE METEO-FRANCE ==========
        base = _get_meteofrance_service().get_meteofrance_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de Meteo-France por ahora. Intenta de nuevo en unos minutos.")
            err_detail = str(st.session_state.get("meteofrance_last_error", "")).strip()
            if err_detail:
                st.caption(f"Detalle técnico Meteo-France: {err_detail}")
            st.stop()

        raw_7d = base.get("_series_7d")
        series_7d = raw_7d if isinstance(raw_7d, dict) else {}

        _r = process_standard_provider(
            base, "METEOFRANCE", "meteofrance_station_alt", series_7d=series_7d,
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_frost_service().is_frost_connection():
        # ========== DATOS DE FROST ==========
        base = _get_frost_service().get_frost_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de Frost por ahora. Intenta de nuevo en unos minutos.")
            err_detail = str(st.session_state.get("frost_last_error", "")).strip()
            if err_detail:
                st.caption(f"Detalle técnico Frost: {err_detail}")
            st.stop()

        raw_7d = base.get("_series_7d")
        series_7d = raw_7d if isinstance(raw_7d, dict) else {}

        _r = process_standard_provider(
            base, "FROST", "frost_station_alt", series_7d=series_7d,
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_meteogalicia_service().is_meteogalicia_connection():
        # ========== DATOS DE METEOGALICIA ==========
        base = _get_meteogalicia_service().get_meteogalicia_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de MeteoGalicia por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        # series_7d={} (sin has_data) → copia chart data a trend_hourly
        _r = process_standard_provider(
            base, "METEOGALICIA", "meteogalicia_station_alt", series_7d={},
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_nws_service().is_nws_connection():
        # ========== DATOS DE NWS ==========
        base = _get_nws_service().get_nws_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de NWS por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        raw_7d = base.get("_series_7d")
        series_7d = raw_7d if isinstance(raw_7d, dict) else {}

        _r = process_standard_provider(
            base, "NWS", "nws_station_alt", series_7d=series_7d,
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    elif _get_poem_service().is_poem_connection():
        # ========== DATOS DE POEM ==========
        base = _get_poem_service().get_poem_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de POEM por ahora. Intenta de nuevo en unos minutos.")
            err_detail = str(st.session_state.get("poem_last_error", "")).strip()
            if err_detail:
                st.caption(f"Detalle técnico POEM: {err_detail}")
            st.stop()

        raw_7d = base.get("_series_7d")
        series_7d = raw_7d if isinstance(raw_7d, dict) else {}

        _r = process_standard_provider(
            base, "POEM", "poem_station_alt", series_7d=series_7d,
        )
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    else:
        # ========== DATOS DE WEATHER UNDERGROUND ==========
        station_id = str(st.session_state.get("active_station", "")).strip()
        api_key = str(st.session_state.get("active_key", "")).strip()

        if not station_id:
            station_id = str(st.session_state.get("wu_connected_station", "")).strip()
            if station_id:
                st.session_state["active_station"] = station_id
        if not api_key:
            api_key = str(st.session_state.get("wu_connected_api_key", "")).strip()
            if api_key:
                st.session_state["active_key"] = api_key

        # Verificar que tenemos los datos mínimos necesarios
        if not station_id or not api_key:
            st.error("❌ Faltan datos de conexión. Introduce Station ID y API Key en el sidebar.")
            st.session_state["connected"] = False
            st.stop()

        try:
            calibration_station = str(st.session_state.get("wu_station_calibration_station", "")).strip().upper()
            if calibration_station == station_id.upper():
                station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
            else:
                station_calibration = default_wu_calibration()

            if st.session_state.pop("_wu_calibration_changed", False):
                reset_rain_history()
                st.session_state.pop("p_hist", None)

            # Obtener datos de WU (con cache)
            base_raw = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)
            base = apply_wu_current_calibration(base_raw, station_calibration)

            # Guardar timestamp de última actualización exitosa
            st.session_state["last_update_time"] = time.time()

            # Guardar latitud y longitud para cálculos de radiación
            st.session_state["station_lat"] = base.get("lat", float("nan"))
            st.session_state["station_lon"] = base.get("lon", float("nan"))

            # ========== ALTITUD ==========
            # Prioridad: 1) active_z del usuario, 2) elevation de API
            elevation_api = base.get("elevation", float("nan"))

            # Obtener elevation_user manejando string vacío
            active_z_str = str(st.session_state.get("active_z", "0")).strip()
            try:
                elevation_user = float(active_z_str) if active_z_str else 0.0
            except ValueError:
                elevation_user = 0.0

            # PRIORIDAD: Usuario primero, luego API
            if elevation_user > 0:
                z = elevation_user
                st.session_state["elevation_source"] = "usuario"
                logger.info(f"Usando altitud de usuario: {z:.1f}m")
            elif not is_nan(elevation_api):
                z = elevation_api
                st.session_state["elevation_source"] = "API"
                logger.info(f"Usando altitud de API: {z:.1f}m")
            else:
                z = 0
                st.session_state["elevation_source"] = "ninguna"
                st.warning("⚠️ **Falta dato de altitud**. Los cálculos de presión absoluta y temperatura potencial pueden ser incorrectos. Introduce la altitud manualmente en el sidebar.")
                logger.error("Sin dato de altitud (API ni usuario)")

            st.session_state["station_elevation"] = z

            now_ts = time.time()

            # Advertir si los datos son muy antiguos
            data_age_minutes = (now_ts - base["epoch"]) / 60
            if data_age_minutes > MAX_DATA_AGE_MINUTES:
                st.warning(f"⚠️ Datos con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
                logger.warning(f"Datos antiguos: {data_age_minutes:.1f} minutos")

            # ========== LLUVIA ==========
            inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
            inst_label = rain_intensity_label(inst_mm_h)

            # ========== PRESIÓN ==========
            p_msl = float(base["p_hpa"])
            p_abs = msl_to_absolute(p_msl, z, base["Tc"])
            provider_for_pressure = st.session_state.get("connection_type", "WU")
            p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
            p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago")
            )

            # ========== TERMODINÁMICA ==========
            # Todas las variables calculadas a partir de T, RH y p_abs
            e_sat = e_s(base["Tc"])  # Presión de saturación
            e = vapor_pressure(base["Tc"], base["RH"])  # Presión de vapor
            Td_calc = dewpoint_from_vapor_pressure(e)  # Td calculado (para LCL)
            q = specific_humidity(e, p_abs)  # Humedad específica
            q_gkg = q * 1000  # g/kg
            theta = potential_temperature(base["Tc"], p_abs)  # Temperatura potencial
            Tv = virtual_temperature(base["Tc"], q)  # Temperatura virtual
            Te = equivalent_temperature(base["Tc"], q)  # Temperatura equivalente
            Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)  # Psicrométrica si hay p_abs
            rho = air_density(p_abs, Tv)  # Densidad del aire
            rho_v_gm3 = absolute_humidity(e, base["Tc"])  # Humedad absoluta
            lcl = lcl_height(base["Tc"], Td_calc)  # Altura LCL

            # Sensación térmica y Heat Index (calculados, nunca del API)
            wind_fl = base.get("wind", 0.0)
            if is_nan(wind_fl):
                wind_fl = 0.0
            wind_fl_ms = float(wind_fl) / 3.6
            base["feels_like"] = apparent_temperature(base["Tc"], e, wind_fl_ms)
            base["heat_index"] = heat_index_rothfusz(base["Tc"], base["RH"])

            # ========== RADIACIÓN ==========
            solar_rad = base.get("solar_radiation", float("nan"))
            uv = base.get("uv", float("nan"))
        
            # MODO DEMO: Reemplazar con valores demo si está activado
            if st.session_state.get("demo_radiation", False):
                demo_solar = st.session_state.get("demo_solar")
                demo_uv = st.session_state.get("demo_uv")
                if demo_solar is not None:
                    solar_rad = float(demo_solar)
                if demo_uv is not None:
                    uv = float(demo_uv)

            # Determinar si la estación tiene sensores de radiación
            has_radiation = not is_nan(solar_rad) or not is_nan(uv)

            if has_radiation:
                # Obtener latitud, elevación y timestamp para FAO-56
                lat = base.get("lat", float("nan"))
                now_ts = time.time()
            
                # ET0 por FAO-56 Penman-Monteith
                wind_speed = base.get("wind", 2.0)  # Velocidad viento (default 2 m/s si no hay)
                if not is_nan(wind_speed) and wind_speed < 0.1:
                    wind_speed = 0.1  # Mínimo para evitar división por cero
            
                from models.radiation import penman_monteith_et0
                et0 = penman_monteith_et0(
                    solar_rad, 
                    base["Tc"], 
                    base["RH"], 
                    wind_speed, 
                    lat, 
                    z,  # elevación
                    now_ts
                )

                # Claridad del cielo con latitud y elevación (FAO-56)
                # Usar epoch del dato (no time.time()) para que la referencia
                # teórica coincida con el momento de la medición.
                from models.radiation import sky_clarity_index
                lon = base.get("lon", float("nan"))
                clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)

                # ET0 y balance mostrados en UI se recalculan como acumulado "hoy"
                # usando la serie /all/1day tras cargar los puntos temporales.
                et0 = float("nan")
                balance = float("nan")

                # Logging seguro (manejar NaN)
                solar_str = f"{solar_rad:.0f}" if not is_nan(solar_rad) else "N/A"
                uv_str = f"{uv:.1f}" if not is_nan(uv) else "N/A"
                logger.info(f"   Radiación: Solar={solar_str} W/m², UV={uv_str}")


            # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
            timeseries_raw = fetch_daily_timeseries(station_id, api_key)
            timeseries = apply_wu_series_calibration(timeseries_raw, station_calibration)
            chart_epochs = timeseries.get("epochs", [])
            chart_temps = timeseries.get("temps", [])
            chart_humidities = timeseries.get("humidities", [])
            chart_dewpts = timeseries.get("dewpts", [])
            # WU devuelve presiones MSL → convertir a absoluta para coherencia
            _cp_msl = timeseries.get("pressures", [])
            _msl_factor = math.exp(-z / 8000.0)
            chart_pressures = [
                p * _msl_factor if not is_nan(p) else float("nan")
                for p in _cp_msl
            ]
            chart_uv_indexes = timeseries.get("uv_indexes", [])
            chart_solar_radiations = timeseries.get("solar_radiations", [])
            chart_winds = timeseries.get("winds", [])
            chart_gusts = timeseries.get("gusts", [])
            chart_wind_dirs = timeseries.get("wind_dirs", [])

            # Fallback de coordenadas desde /all/1day si current no las trajo.
            ts_lat = timeseries.get("lat", float("nan"))
            ts_lon = timeseries.get("lon", float("nan"))
            if is_nan(st.session_state.get("station_lat", float("nan"))) and not is_nan(ts_lat):
                st.session_state["station_lat"] = ts_lat
                base["lat"] = ts_lat
            if is_nan(st.session_state.get("station_lon", float("nan"))) and not is_nan(ts_lon):
                st.session_state["station_lon"] = ts_lon
                base["lon"] = ts_lon

            if is_nan(base.get("lat", float("nan"))) and not is_nan(st.session_state.get("station_lat", float("nan"))):
                base["lat"] = st.session_state.get("station_lat")
            if is_nan(base.get("lon", float("nan"))) and not is_nan(st.session_state.get("station_lon", float("nan"))):
                base["lon"] = st.session_state.get("station_lon")
            has_chart_data = timeseries.get("has_data", False)

            wu_sensor_presence = detect_wu_sensor_presence(base_raw, timeseries_raw)
            prev_wu_sensor_presence = st.session_state.get("wu_sensor_presence", {})
            prev_wu_sensor_station = str(st.session_state.get("wu_sensor_presence_station", "")).strip().upper()
            st.session_state["wu_sensor_presence"] = wu_sensor_presence
            st.session_state["wu_sensor_presence_station"] = station_id.upper()
            if prev_wu_sensor_station != station_id.upper() or prev_wu_sensor_presence != wu_sensor_presence:
                st.rerun()
        
            # FALLBACK: Si no hay humidities, calcularlas desde T y Td
            # (esto no debería ser necesario normalmente)
            if len(chart_humidities) == 0 or all(is_nan(h) for h in chart_humidities):
                logger.warning("⚠️  API no devolvió humedad - usando fallback desde T y Td")
                chart_humidities = []
                for temp, td in zip(chart_temps, chart_dewpts):
                    if is_nan(temp) or is_nan(td):
                        chart_humidities.append(float("nan"))
                    else:
                        # Calcular RH desde T y Td: RH = 100 * e(Td) / e_s(T)
                        e_td = e_s(td)
                        e_s_t = e_s(temp)
                        rh = 100.0 * e_td / e_s_t if e_s_t > 0 else float("nan")
                        chart_humidities.append(rh)

            # ET0 "hoy" acumulada desde serie diaria (típicamente 5 min con piranómetro).
            if has_radiation:
                from models.radiation import penman_monteith_et0

                et0_accum_mm = 0.0
                valid_steps = 0
                fallback_wind = base.get("wind", 2.0)

                for i, epoch_i in enumerate(chart_epochs):
                    solar_i = chart_solar_radiations[i] if i < len(chart_solar_radiations) else float("nan")
                    temp_i = chart_temps[i] if i < len(chart_temps) else float("nan")
                    rh_i = chart_humidities[i] if i < len(chart_humidities) else float("nan")

                    if is_nan(solar_i) or is_nan(temp_i) or is_nan(rh_i):
                        continue

                    wind_i = chart_winds[i] if i < len(chart_winds) else float("nan")
                    if is_nan(wind_i):
                        wind_i = fallback_wind
                    if not is_nan(wind_i) and wind_i < 0.1:
                        wind_i = 0.1

                    et0_daily_i = penman_monteith_et0(
                        solar_i,
                        temp_i,
                        rh_i,
                        wind_i,
                        base.get("lat", float("nan")),
                        z,
                        epoch_i,
                    )
                    if is_nan(et0_daily_i):
                        continue

                    step_hours = 5.0 / 60.0
                    if i > 0 and i - 1 < len(chart_epochs):
                        try:
                            dt_seconds = float(epoch_i) - float(chart_epochs[i - 1])
                            if 120 <= dt_seconds <= 1800:
                                step_hours = dt_seconds / 3600.0
                        except Exception:
                            pass

                    et0_mmh_i = et0_daily_i / 24.0
                    et0_accum_mm += et0_mmh_i * step_hours
                    valid_steps += 1

                et0 = et0_accum_mm if valid_steps > 0 else float("nan")
                balance = water_balance(base["precip_total"], et0)

                et0_str = f"{et0:.2f}" if not is_nan(et0) else "N/A"
                balance_str = f"{balance:.2f}" if not is_nan(balance) else "N/A"
                logger.info(f"   ET0 hoy acumulada={et0_str} mm, Balance hoy={balance_str} mm")

            # Guardar en session_state para acceso desde otras tabs
            st.session_state["chart_epochs"] = chart_epochs
            st.session_state["chart_temps"] = chart_temps
            st.session_state["chart_humidities"] = chart_humidities
            st.session_state["chart_dewpts"] = chart_dewpts
            st.session_state["chart_pressures"] = chart_pressures
            st.session_state["chart_uv_indexes"] = chart_uv_indexes
            st.session_state["chart_solar_radiations"] = chart_solar_radiations
            st.session_state["chart_winds"] = chart_winds
            st.session_state["chart_gusts"] = chart_gusts
            st.session_state["chart_wind_dirs"] = chart_wind_dirs
            st.session_state["has_chart_data"] = has_chart_data

            if has_chart_data:
                logger.info(f"   Gráficos: {len(chart_epochs)} puntos de temperatura")
                # Debug: verificar humidities
                humidities_validas = sum(1 for h in chart_humidities if not is_nan(h))
                logger.info(f"   Humidities: {len(chart_humidities)} totales, {humidities_validas} válidas")

        except WuError as e:
            if e.kind == "unauthorized":
                st.error("❌ API key inválida o sin permisos.")
            elif e.kind == "notfound":
                st.error("❌ Station ID no encontrado.")
            elif e.kind == "ratelimit":
                st.error("❌ Demasiadas peticiones. Aumenta el refresh.")
            elif e.kind == "timeout":
                st.error("❌ Timeout consultando Weather Underground.")
            elif e.kind == "network":
                st.error("❌ Error de red.")
            else:
                st.error("❌ Error consultando Weather Underground.")
        except Exception as err:
            # Usar concatenación simple para evitar cualquier problema con format specifiers
            st.error("❌ Error inesperado: " + str(err))
            logger.error(f"Error inesperado: {repr(err)}")

if st.session_state.get("demo_radiation", False):
    demo_solar = _first_valid_float(st.session_state.get("demo_solar"), default=650.0)
    demo_uv = _first_valid_float(st.session_state.get("demo_uv"), default=6.0)
    demo_lat = _first_valid_float(
        base.get("lat"),
        st.session_state.get("provider_station_lat"),
        st.session_state.get("aemet_station_lat"),
        st.session_state.get("station_lat"),
        default=41.3874,
    )
    demo_lon = _first_valid_float(
        base.get("lon"),
        st.session_state.get("provider_station_lon"),
        st.session_state.get("aemet_station_lon"),
        st.session_state.get("station_lon"),
        default=2.1686,
    )
    demo_alt = _first_valid_float(
        z,
        st.session_state.get("provider_station_alt"),
        st.session_state.get("aemet_station_alt"),
        st.session_state.get("station_elevation"),
        default=12.0,
    )

    demo_series = _build_demo_radiation_series(demo_solar, demo_uv)
    demo_epoch = demo_series["epochs"][-1] if demo_series["epochs"] else int(time.time())

    base.update({
        "epoch": int(base.get("epoch") or demo_epoch),
        "solar_radiation": demo_solar,
        "uv": demo_uv,
        "lat": demo_lat,
        "lon": demo_lon,
        "precip_total": _first_valid_float(base.get("precip_total"), default=0.0),
    })
    z = demo_alt
    solar_rad = demo_solar
    uv = demo_uv
    has_radiation = True
    st.session_state["demo_radiation_series"] = demo_series

    from models.radiation import penman_monteith_et0, sky_clarity_index

    clarity = sky_clarity_index(solar_rad, demo_lat, z, demo_epoch, demo_lon)

    et0_accum_mm = 0.0
    valid_steps = 0
    demo_epochs = demo_series["epochs"]
    demo_temps = demo_series["temps"]
    demo_humidities = demo_series["humidities"]
    demo_winds = demo_series["winds"]
    demo_solars = demo_series["solar_radiations"]

    for idx, epoch_i in enumerate(demo_epochs):
        if idx >= len(demo_solars) or idx >= len(demo_temps) or idx >= len(demo_humidities) or idx >= len(demo_winds):
            continue

        solar_i = float(demo_solars[idx])
        temp_i = float(demo_temps[idx])
        rh_i = float(demo_humidities[idx])
        wind_i = max(0.1, float(demo_winds[idx]))

        et0_i = penman_monteith_et0(solar_i, temp_i, rh_i, wind_i, demo_lat, z, float(epoch_i))
        if is_nan(et0_i):
            continue

        step_hours = 5.0 / 60.0
        if idx > 0:
            dt_seconds = float(epoch_i) - float(demo_epochs[idx - 1])
            if 120 <= dt_seconds <= 1800:
                step_hours = dt_seconds / 3600.0

        et0_accum_mm += et0_i / 24.0 * step_hours
        valid_steps += 1

    et0 = et0_accum_mm if valid_steps > 0 else float("nan")
    balance = water_balance(base["precip_total"], et0)

    st.session_state["station_lat"] = demo_lat
    st.session_state["station_lon"] = demo_lon
    st.session_state["station_elevation"] = demo_alt
    if not connected:
        has_chart_data = demo_series.get("has_data", False)
        chart_epochs = demo_series["epochs"]
        chart_temps = demo_series["temps"]
        chart_humidities = demo_series["humidities"]
        chart_pressures = []
        chart_uv_indexes = demo_series["uv_indexes"]
        chart_solar_radiations = demo_series["solar_radiations"]
        chart_winds = demo_series["winds"]
        chart_gusts = demo_series["gusts"]
        chart_wind_dirs = demo_series["wind_dirs"]
        st.session_state["chart_epochs"] = chart_epochs
        st.session_state["chart_temps"] = chart_temps
        st.session_state["chart_humidities"] = chart_humidities
        st.session_state["chart_dewpts"] = []
        st.session_state["chart_pressures"] = chart_pressures
        st.session_state["chart_uv_indexes"] = chart_uv_indexes
        st.session_state["chart_solar_radiations"] = chart_solar_radiations
        st.session_state["chart_winds"] = chart_winds
        st.session_state["chart_gusts"] = chart_gusts
        st.session_state["chart_wind_dirs"] = chart_wind_dirs
        st.session_state["has_chart_data"] = has_chart_data
else:
    st.session_state.pop("demo_radiation_series", None)

# Mostrar metadata si está conectado (común para AEMET y WU)
if connected:
    browser_tz_name = str(
        st.session_state.get("browser_tz") or st.query_params.get("_tz", "")
    ).strip()
    station_tz_name = str(
        base.get("station_tz", st.session_state.get("provider_station_tz", ""))
    ).strip()

    user_time_txt = es_datetime_from_epoch(base["epoch"], browser_tz_name)
    user_time_label = t("meta.user_time")
    user_time_label_safe = html.escape(user_time_label)
    user_time_fallback_label = html.escape(t("meta.user_time"))
    user_time_txt_safe = html.escape(user_time_txt)

    station_time_txt = ""
    if station_tz_name:
        try:
            station_epoch_txt = es_datetime_from_epoch(base["epoch"], station_tz_name)
            if station_epoch_txt != user_time_txt:
                station_time_txt = (
                    f" · {t('meta.station_time')} ({station_tz_name}): {station_epoch_txt}"
                )
        except Exception:
            station_time_txt = ""

    st.markdown(
        html_clean(
            (
                f"<div class='meta'>{t('meta.last_data')} · "
                f"<span class='mlbx-live-user-time-label' data-fallback-label='{user_time_fallback_label}'>{user_time_label_safe}</span>: "
                f"<span class='mlbx-live-user-time' data-epoch='{int(base['epoch'])}'>{user_time_txt_safe}</span>"
                f"{station_time_txt} · {t('meta.age')}: "
                f"<span class='mlbx-live-age' data-epoch='{int(base['epoch'])}'>{html.escape(age_string(base['epoch']))}</span></div>"
            )
        ),
        unsafe_allow_html=True
    )

# ============================================================
# NAVEGACIÓN CON TABS
# ============================================================

# ============================================================
# SELECTOR DE TABS CON st.radio (estilizado como tabs)
# ============================================================

# CSS para ocultar círculos y estilizar como tabs (dinámico según tema)
# DEBE IR ANTES del radio button para que se aplique correctamente
tabs_color = "rgba(15, 18, 25, 0.92)" if not dark else "rgba(255, 255, 255, 0.92)"

# Añadir hash único al CSS para forzar regeneración
import hashlib
css_hash = hashlib.md5(f"{tabs_color}{dark}".encode()).hexdigest()[:8]

st.markdown(f"""
<style data-theme-hash="{css_hash}">
/* Ocultar el círculo del radio */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label > div:first-child {{
    display: none;
}}
/* Estilo base de cada opción */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label {{
    padding: 0.5rem 1rem;
    margin-right: 0.5rem;
    border-bottom: 3px solid transparent;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.2s ease;
}}
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label div[data-testid="stMarkdownContainer"] p {{
    color: {tabs_color} !important;
}}
/* Hover */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:hover {{
    border-bottom: 3px solid rgba(255, 75, 75, 0.3);
}}
/* Opción seleccionada */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:has(input:checked) {{
    border-bottom: 3px solid #ff4b4b;
    font-weight: 600;
}}
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
    color: #ff4b4b !important;
}}
</style>

<script>
// Aplicar colores a las pestañas con JavaScript como fallback
(function() {{
    const tabColor = '{tabs_color}';
    const labels = document.querySelectorAll('[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label');
    labels.forEach(label => {{
        const p = label.querySelector('p');
        if (p && !label.querySelector('input:checked')) {{
            p.style.setProperty('color', tabColor, 'important');
        }}
    }});
}})();
</script>
""", unsafe_allow_html=True)

tab_options = ["observation", "trends", "historical", "info", "map"]
legacy_tab_aliases = {
    "Observación": "observation",
    "Tendencias": "trends",
    "Climogramas": "historical",
    "Histórico": "historical",
    "Divulgación": "info",
    "Mapa": "map",
}

# Aplicar navegación diferida ANTES de instanciar el widget de tabs.
pending_tab = st.session_state.get("_pending_active_tab")
if isinstance(pending_tab, str):
    pending_tab = legacy_tab_aliases.get(pending_tab, pending_tab)
if isinstance(pending_tab, str) and pending_tab in tab_options:
    st.session_state["active_tab"] = pending_tab
if "_pending_active_tab" in st.session_state:
    del st.session_state["_pending_active_tab"]

# Inicializar tab activo una sola vez y dejar que el propio widget
# gestione su estado en reruns (evita casos de "doble clic" al cambiar pestaña).
active_tab_state = st.session_state.get("active_tab")
if isinstance(active_tab_state, str):
    st.session_state["active_tab"] = legacy_tab_aliases.get(active_tab_state, active_tab_state)
if st.session_state.get("active_tab") not in tab_options:
    st.session_state["active_tab"] = tab_options[0]

# Radio buttons estilizados como tabs con underline
active_tab = st.radio(
    "Navegación",
    tab_options,
    horizontal=True,
    format_func=lambda tab_id: t(f"tabs.{tab_id}"),
    key="active_tab",
    label_visibility="collapsed"
)

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# TAB 1: OBSERVACIÓN
if active_tab == "observation":
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
                 f"<div>{t('observation.cards.basic.precipitation_today.instantaneous')}: <b>{fmt_rate(inst_mm_h)}</b></div>"
                 f"<div style='font-size:0.9rem; opacity:0.85;'>{inst_label_card}</div>"
                 f"<div style='margin-top:6px; font-size:0.8rem; opacity:0.6;'>{t('observation.cards.basic.precipitation_today.minute_1')}: {fmt_rate(r1_mm_h)} · {t('observation.cards.basic.precipitation_today.minute_5')}: {fmt_rate(r5_mm_h)}</div>"
             ), 
             uid="b6", dark=dark, tooltip_key="precipitación hoy"),
    ]
    render_grid(cards_basic, cols=3, extra_class="grid-basic")

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

        def _solar_energy_today_wh_m2() -> float:
            import math
            epochs, solars, _ = _active_radiation_series()
            if not epochs or not solars:
                return float("nan")
            points = []
            for ep, s in zip(epochs, solars):
                try:
                    ep_i = int(ep)
                    s_f = float(s)
                    if math.isnan(s_f):
                        continue
                    # Sin radiación negativa físicamente útil para el acumulado.
                    points.append((ep_i, max(0.0, s_f)))
                except Exception:
                    continue
            if len(points) < 2:
                return float("nan")
            points.sort(key=lambda x: x[0])
            now_local_dt = datetime.now()
            day_start = now_local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            day_start_ep = int(day_start.timestamp())
            now_ep = int(now_local_dt.timestamp())
            today_points = [(ep, s) for ep, s in points if day_start_ep <= ep <= now_ep]
            if len(today_points) < 2:
                return float("nan")
            e_wh_m2 = 0.0
            prev_ep, prev_s = today_points[0]
            for ep, s in today_points[1:]:
                dt_s = ep - prev_ep
                # Evita integrar huecos anómalos muy largos.
                if 0 < dt_s <= 2 * 3600:
                    dt_h = dt_s / 3600.0
                    e_wh_m2 += ((prev_s + s) * 0.5) * dt_h
                prev_ep, prev_s = ep, s
            return float(e_wh_m2) if e_wh_m2 > 0 else 0.0

        def _erythemal_dose_today_metrics() -> tuple[float, float]:
            import math
            if is_nan(uv):
                return float("nan"), float("nan")

            epochs, _, uv_indexes = _active_radiation_series()
            if not epochs or not uv_indexes:
                return float("nan"), float("nan")

            try:
                if st.session_state.get("demo_radiation", False):
                    now_ep = int(time.time())
                else:
                    now_ep = int(base.get("epoch", 0) or time.time())
            except Exception:
                now_ep = int(time.time())

            points = []
            for ep, uv_i in zip(epochs, uv_indexes):
                try:
                    ep_i = int(ep)
                    uv_f = float(uv_i)
                    if math.isnan(uv_f) or ep_i <= 0 or ep_i > now_ep:
                        continue
                    points.append((ep_i, max(0.0, uv_f)))
                except Exception:
                    continue

            if not points:
                return float("nan"), float("nan")

            points.sort(key=lambda item: item[0])
            dose_j_m2 = 0.0

            for idx, (ep_i, uv_i) in enumerate(points):
                if idx + 1 < len(points):
                    next_ep = points[idx + 1][0]
                else:
                    next_ep = now_ep

                dt_s = next_ep - ep_i
                if dt_s <= 0 and idx > 0:
                    dt_s = ep_i - points[idx - 1][0]
                if dt_s <= 0:
                    dt_s = 300

                dt_s = max(60, min(1800, int(dt_s)))
                dose_j_m2 += 0.025 * uv_i * dt_s

            dose_j_m2 = max(0.0, float(dose_j_m2))
            return dose_j_m2 / 100.0, dose_j_m2

        # Formatear valores
        solar_val = _fmt_radiation_display(solar_rad, decimals=0)
        uv_val = "—" if is_nan(uv) else f"{uv:.1f}"
        erythemal_dose_sed, erythemal_dose_j_m2 = _erythemal_dose_today_metrics()
        erythemal_dose_val = "—" if is_nan(erythemal_dose_sed) else f"{erythemal_dose_sed:.2f}"
        et0_val = _fmt_precip_display(et0, decimals=1)
        clarity_val = "—" if is_nan(clarity) else f"{clarity * 100:.0f}"
        balance_val = _fmt_precip_display(balance, decimals=1)
        energy_today_wh_m2 = _solar_energy_today_wh_m2()
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

        from datetime import datetime, timedelta
        import pandas as pd
        import plotly.graph_objects as go
        
        # Obtener datos de gráficos del session_state
        chart_epochs = st.session_state.get("chart_epochs", [])
        chart_temps = st.session_state.get("chart_temps", [])
        chart_humidities = st.session_state.get("chart_humidities", [])
        chart_pressures = st.session_state.get("chart_pressures", [])
        chart_solar_radiations = st.session_state.get("chart_solar_radiations", [])
        chart_winds = st.session_state.get("chart_winds", [])
        
        print(f"🔍 [DEBUG Gráficos] Obtenidos del session_state:")
        print(f"   - chart_epochs: {len(chart_epochs)} elementos")
        print(f"   - chart_temps: {len(chart_temps)} elementos")  
        print(f"   - chart_solar_radiations: {len(chart_solar_radiations)} elementos")
        print(f"   - Keys en session_state: {[k for k in st.session_state.keys() if 'chart' in k]}")
        
        logger.info(f"📊 [Gráficos] Datos disponibles: {len(chart_epochs)} epochs, {len(chart_temps)} temps, {len(chart_humidities)} humidities")

        # --- 1) Construir serie con datetimes reales
        dt_list = []
        temp_list = []
        for epoch, temp in zip(chart_epochs, chart_temps):
            dt = datetime.fromtimestamp(epoch)  # si fuera UTC: datetime.utcfromtimestamp(epoch)
            dt_list.append(dt)
            temp_list.append(temp)

        print(f"🔍 [DEBUG] Después del loop: dt_list={len(dt_list)}, temp_list={len(temp_list)}")
        if len(dt_list) > 0:
            print(f"   Primeros 3 dt: {dt_list[:3]}")
            print(f"   Primeras 3 temps: {temp_list[:3]}")

        df_obs = pd.DataFrame({"dt": dt_list, "temp": temp_list}).sort_values("dt")
        print(f"🔍 [DEBUG] DataFrame creado: {len(df_obs)} filas")

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
        print(f"🔍 [DEBUG] Después de groupby: {len(df_obs)} filas")

        # --- 2) Crear malla completa con rango específico por proveedor
        now_local = datetime.now()

        grid_inclusive = "both"

        # Mostrar siempre el día completo y dejar que la serie se "monte"
        # a medida que llegan observaciones.
        day_start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        data_start = day_start_today
        data_end = day_start_today + timedelta(days=1)
        grid_inclusive = "left"  # no incluir 24:00 del día siguiente
        print(f"🔍 [DEBUG] [{connection_type}] Ventana HOY: {data_start} → {data_end} (left-inclusive)")

        # Guardar para uso en layout
        day_start = data_start
        day_end = data_end

        grid = pd.date_range(
            start=data_start,
            end=data_end,
            freq=f"{step_minutes}min",
            inclusive=grid_inclusive
        )
        print(f"🔍 [DEBUG] Grid creado: {len(grid)} puntos de {grid[0]} a {grid[-1]}")

        # --- 3) Reindexar (ahora sí casan los timestamps)
        s = pd.Series(df_obs["temp"].values, index=pd.to_datetime(df_obs["dt"]))
        y = s.reindex(grid)  # sin rellenar; NaN = huecos
        y_display = y.apply(lambda value: convert_temperature(value, temp_unit_pref) if not is_nan(value) else float("nan"))
        print(f"🔍 [DEBUG] Serie reindexada: {len(y)} puntos, {y.notna().sum()} válidos")

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

            print(f"🔍 [DEBUG] Antes de crear gráfico:")
            print(f"   - y.shape: {y.shape}")
            print(f"   - y.notna().sum(): {y.notna().sum()}")
            print(f"   - Primeros 10 valores de y: {y.head(10).tolist()}")
            print(f"   - y_min={y_min}, y_max={y_max}")
            print(f"   - grid.shape: {len(grid)}")
            print(f"   - grid primeros 3: {grid[:3].tolist()}")

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

            print(f"✅ [DEBUG] Gráfico creado - trazas: {len(fig.data)}")

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
                    tickfont=dict(color=text_color)
                ),
                yaxis=dict(
                    title=dict(text=temp_unit_txt, font=dict(color=text_color)),
                    range=[y_min, y_max],
                    gridcolor=grid_color,
                    showgrid=True,
                    tickfont=dict(color=text_color)
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
                            tickfont=dict(color=text_color)
                        ),
                        yaxis=dict(
                            title=dict(text=pressure_unit_txt, font=dict(color=text_color)),
                            showgrid=True,
                            gridcolor=grid_color,
                            tickfont=dict(color=text_color)
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
                # No mostrar dirección cuando el viento medio está en calma (0),
                # para evitar "direcciones fantasma" de la veleta.
                df_wind_view["dir_plot"] = df_wind_view["dir"]
                calm_mask = df_wind_view["wind"].apply(
                    lambda v: (not is_nan(v)) and abs(float(v)) < 0.1
                )
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
                        tickfont=dict(color=text_color),
                    ),
                    yaxis=dict(
                        title=dict(text=wind_unit_txt, font=dict(color=text_color)),
                        showgrid=True,
                        gridcolor=grid_color,
                        tickfont=dict(color=text_color),
                        rangemode="tozero",
                    ),
                    yaxis2=dict(
                        title=dict(text=t("observation.cards.charts.direction_axis"), font=dict(color=text_color)),
                        overlaying="y",
                        side="right",
                        range=[0, 360],
                        tickvals=[0, 45, 90, 135, 180, 225, 270, 315, 360],
                        ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"],
                        tickfont=dict(color=text_color),
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
                sectors16 = [
                    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
                ]

                counts = {s: 0 for s in sectors16}
                calm = 0
                valid_dir_samples = 0

                for _, row in df_wind_view.iterrows():
                    w = float(row["wind"]) if not is_nan(row["wind"]) else float("nan")
                    g = float(row["gust"]) if not is_nan(row["gust"]) else float("nan")
                    d = float(row["dir"]) if not is_nan(row["dir"]) else float("nan")

                    has_w = not is_nan(w)
                    has_g = not is_nan(g)

                    # Velocidad efectiva para calma: usar el mejor dato disponible.
                    if has_w and has_g:
                        speed_ref = max(w, g)
                    elif has_w:
                        speed_ref = w
                    elif has_g:
                        speed_ref = g
                    else:
                        continue

                    is_calm_sample = speed_ref < 1.0
                    if is_calm_sample:
                        calm += 1

                    # Rosa: solo muestras con dirección y no calmadas.
                    if is_nan(d) or is_calm_sample:
                        continue

                    idx = int((d + 11.25) // 22.5) % 16
                    counts[sectors16[idx]] += 1
                    valid_dir_samples += 1

                total_samples = len(df_wind_view)
                dir_total = sum(counts.values())
                dominant_dir = None
                if dir_total > 0:
                    dominant_dir = max(sectors16, key=lambda s: counts[s])

                if dir_total > 0:
                    st.markdown(f"### {t('observation.cards.charts.wind_rose_heading')}")

                    dir_pcts = {
                        s: (100.0 * counts[s] / dir_total) if dir_total > 0 else 0.0
                        for s in sectors16
                    }
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
                    lat_clarity = float(base.get("lat", float("nan")))
                except Exception:
                    lat_clarity = float("nan")
                try:
                    lon_clarity = float(base.get("lon", float("nan")))
                except Exception:
                    lon_clarity = float("nan")
                if len(solar_valid) >= 3 and not is_nan(lat_clarity) and not is_nan(lon_clarity):
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
                            elevation_m=float(z),
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
                                    tickfont=dict(color=text_color),
                                ),
                                yaxis=dict(
                                    title=dict(text=radiation_unit_txt, font=dict(color=text_color)),
                                    range=[0, y_max_irr],
                                    showgrid=True,
                                    gridcolor=grid_color,
                                    tickfont=dict(color=text_color),
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

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "trends":
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
        data_source_label = ""
        has_barometer_series = True
        uv_series_override = None

        if periodo == "today":
            st.markdown(t("trends.derivatives_today"))

            if provider_id == "AEMET":
                idema = str(st.session_state.get("aemet_station_id", "")).strip().upper()
                if not idema:
                    st.warning(t("trends.warnings.series_unavailable"))
                    logger.warning("Tendencias AEMET Hoy: falta idema")
                else:
                    try:
                        lookback_series = _get_aemet_service().fetch_aemet_today_series_with_lookback(
                            idema,
                            hours_before_start=3,
                        )
                    except Exception as err:
                        lookback_series = {"has_data": False}
                        logger.warning(f"Tendencias AEMET Hoy: error obteniendo serie con lookback: {err}")
                    if not lookback_series.get("has_data", False):
                        st.warning(t("trends.warnings.series_unavailable"))
                        logger.warning("Tendencias AEMET Hoy: sin serie con lookback")
                    else:
                        uv_series_override = lookback_series
                        dt_list = [datetime.fromtimestamp(ep) for ep in lookback_series.get("epochs", [])]
                        temp_list = [float(v) for v in lookback_series.get("temps", [])]
                        rh_list = [float(v) for v in lookback_series.get("humidities", [])]
                        p_list = [float(v) for v in lookback_series.get("pressures", [])]

                        df_trends = pd.DataFrame({
                            "dt": dt_list,
                            "temp": temp_list,
                            "rh": rh_list,
                            "p": p_list,
                        }).sort_values("dt")

                        if df_trends.empty:
                            st.warning(t("trends.warnings.series_unavailable"))
                            logger.warning("Tendencias AEMET Hoy: DataFrame con lookback vacío")
                        else:
                            df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("10min")
                            df_trends = df_trends.groupby("dt", as_index=False).last()
                            raw_series_step_min = max(10, _infer_series_step_minutes(df_trends["dt"]))
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

                            series_step_min = max(trend_grid_step_min, _infer_series_step_minutes(df_trends["dt"]))
                            interval_theta_e = trend_grid_step_min
                            interval_e = trend_grid_step_min
                            interval_p = 180
                            dataset_ready = True
                            data_source_label = t("trends.sources.aemet_today", minutes=trend_grid_step_min)

            elif provider_id == "METEOCAT":
                station_code = str(
                    st.session_state.get("meteocat_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip().upper()
                if not station_code:
                    st.warning(t("trends.warnings.series_unavailable"))
                    logger.warning("Tendencias METEOCAT Hoy: falta station_code")
                else:
                    lookback_series = _get_meteocat_service().fetch_meteocat_today_series_with_lookback(
                        station_code,
                        hours_before_start=3,
                    )
                    if not lookback_series.get("has_data", False):
                        st.warning(t("trends.warnings.series_unavailable"))
                        logger.warning("Tendencias METEOCAT Hoy: sin serie con lookback")
                    else:
                        uv_series_override = lookback_series
                        dt_list = [datetime.fromtimestamp(ep) for ep in lookback_series.get("epochs", [])]
                        temp_list = [float(v) for v in lookback_series.get("temps", [])]
                        rh_list = [float(v) for v in lookback_series.get("humidities", [])]
                        p_list = [float(v) for v in lookback_series.get("pressures_abs", [])]

                        df_trends = pd.DataFrame({
                            "dt": dt_list,
                            "temp": temp_list,
                            "rh": rh_list,
                            "p": p_list,
                        }).sort_values("dt")

                        if df_trends.empty:
                            st.warning(t("trends.warnings.series_unavailable"))
                            logger.warning("Tendencias METEOCAT Hoy: DataFrame vacío")
                        else:
                            raw_series_step_min = max(5, _infer_series_step_minutes(pd.to_datetime(df_trends["dt"])))
                            trend_grid_step_min = max(20, raw_series_step_min)
                            df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor(f"{trend_grid_step_min}min")
                            df_trends = df_trends.groupby("dt", as_index=False).last()

                            day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                            day_end = day_start + timedelta(days=1)
                            series_step_min = max(trend_grid_step_min, _infer_series_step_minutes(df_trends["dt"]))
                            grid = pd.date_range(
                                start=day_start,
                                end=day_end,
                                freq=f"{trend_grid_step_min}min",
                                inclusive="left",
                            )
                            interval_theta_e = max(20, series_step_min)
                            interval_e = max(20, series_step_min)
                            interval_p = 180
                            dataset_ready = True
                            data_source_label = t("trends.sources.meteocat_today", minutes=trend_grid_step_min)

            else:
                chart_epochs = st.session_state.get("chart_epochs", [])
                chart_temps = st.session_state.get("chart_temps", [])
                chart_humidities = st.session_state.get("chart_humidities", [])
                chart_pressures = st.session_state.get("chart_pressures", [])

                if len(chart_epochs) == 0:
                    st.warning(t("trends.warnings.series_unavailable"))
                    logger.warning("Tendencias 20 min: sin chart_epochs")
                else:
                    dt_list = []
                    temp_list = []
                    rh_list = []
                    p_list = []

                    for i, (epoch, temp) in enumerate(zip(chart_epochs, chart_temps)):
                        dt_list.append(datetime.fromtimestamp(epoch))
                        temp_list.append(float(temp))

                        rh = chart_humidities[i] if i < len(chart_humidities) else float("nan")
                        p = chart_pressures[i] if i < len(chart_pressures) else float("nan")

                        rh_list.append(float(rh))
                        p_list.append(float(p))

                    df_trends = pd.DataFrame({
                        "dt": dt_list,
                        "temp": temp_list,
                        "rh": rh_list,
                        "p": p_list,
                    }).sort_values("dt")

                    if df_trends.empty:
                        st.warning(t("trends.warnings.series_unavailable"))
                        logger.warning("Tendencias 20 min: DataFrame vacío")
                    else:
                        raw_series_step_min = max(5, _infer_series_step_minutes(pd.to_datetime(df_trends["dt"])))
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
                        series_step_min = max(trend_grid_step_min, _infer_series_step_minutes(df_trends["dt"]))
                        interval_theta_e = max(20, series_step_min)
                        interval_e = max(20, series_step_min)
                        interval_p = 180
                        dataset_ready = True
                        data_source_label = t("trends.sources.local_today", minutes=interval_theta_e)

        else:
            st.markdown(t("trends.derivatives_synoptic"))

            if provider_id == "AEMET":
                idema = st.session_state.get("aemet_station_id", "")
                if not idema:
                    hourly7d = {"has_data": False, "epochs": [], "temps": [], "humidities": [], "pressures": []}
                else:
                    with st.spinner("Obteniendo serie sinóptica reciente de AEMET..."):
                        hourly7d = _get_aemet_service().fetch_aemet_recent_synoptic_series(
                            idema,
                            days_back=7,
                            step_hours=3,
                        )
                data_source_label = t("trends.sources.aemet_synoptic")
            elif provider_id == "WU":
                station_id = st.session_state.get("active_station", "")
                api_key = st.session_state.get("active_key", "")
                calibration_station = str(st.session_state.get("wu_station_calibration_station", "")).strip().upper()
                if calibration_station == str(station_id).strip().upper():
                    station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
                else:
                    station_calibration = default_wu_calibration()
                with st.spinner("Obteniendo datos horarios de 7 días..."):
                    hourly7d_raw = fetch_hourly_7day_session_cached(station_id, api_key)
                hourly7d = apply_wu_series_calibration(hourly7d_raw, station_calibration)
                data_source_label = t("trends.sources.wu_synoptic")
            elif provider_id == "METEOFRANCE":
                station_id = str(
                    st.session_state.get("meteofrance_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip()
                meteofrance_service = _get_meteofrance_service()
                with st.spinner("Obteniendo serie sinóptica reciente de Meteo-France..."):
                    hourly7d = meteofrance_service.fetch_meteofrance_recent_synoptic_series(
                        station_id,
                        meteofrance_service.METEOFRANCE_API_KEY,
                        days_back=7,
                        step_hours=3,
                    )
                data_source_label = t("trends.sources.meteofrance_synoptic")
            elif provider_id == "METEOCAT":
                station_code = str(
                    st.session_state.get("meteocat_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip().upper()
                with st.spinner("Obteniendo serie reciente de Meteocat..."):
                    hourly7d = _get_meteocat_service().fetch_meteocat_recent_synoptic_series(
                        station_code,
                        days_back=7,
                    )
                data_source_label = t("trends.sources.meteocat_synoptic")
            elif provider_id == "METEOGALICIA":
                station_id = str(
                    st.session_state.get("meteogalicia_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip()
                with st.spinner("Obteniendo serie reciente de MeteoGalicia..."):
                    hourly7d = _get_meteogalicia_service().fetch_meteogalicia_recent_synoptic_series(
                        station_id,
                        days_back=7,
                        step_hours=3,
                    )
                data_source_label = t("trends.sources.meteogalicia_synoptic")
            elif provider_id == "EUSKALMET":
                hourly7d = {
                    "epochs": [],
                    "temps": [],
                    "humidities": [],
                    "pressures": [],
                    "has_data": False,
                }
                data_source_label = t("trends.sources.euskalmet_synoptic")
            else:
                hourly7d = {
                    "epochs": st.session_state.get("trend_hourly_epochs", []),
                    "temps": st.session_state.get("trend_hourly_temps", []),
                    "humidities": st.session_state.get("trend_hourly_humidities", []),
                    "pressures": st.session_state.get("trend_hourly_pressures", []),
                    "has_data": False,
                }
                hourly7d["has_data"] = len(hourly7d["epochs"]) > 0
                data_source_label = t("trends.sources.generic_synoptic", provider=provider_id)

            if not hourly7d.get("has_data", False):
                if provider_id == "AEMET":
                    st.warning(t("trends.warnings.aemet_weekly_unavailable"))
                    st.caption(t("trends.warnings.aemet_weekly_caption"))
                elif provider_id == "EUSKALMET":
                    _render_neutral_info_note(
                        t("trends.notes.provider_insufficient_data"),
                        title=t("trends.notes.provider_coverage_title"),
                    )
                else:
                    st.warning(t("trends.warnings.series_unavailable"))
                logger.warning(f"Sin datos horarios para tendencia sinóptica ({provider_id})")
            else:
                if provider_id == "METEOGALICIA":
                    _eps = hourly7d.get("epochs", [])
                    if _eps:
                        synoptic_span_h = (max(_eps) - min(_eps)) / 3600.0
                        if synoptic_span_h < 6.0 * 24.0:
                            _render_neutral_info_note(
                                t("trends.notes.meteogalicia_max_coverage"),
                                title=t("trends.notes.provider_coverage_title"),
                            )
                epochs_7d = hourly7d.get("epochs", [])
                temps_7d = hourly7d.get("temps", [])
                humidities_7d = hourly7d.get("humidities", [])
                dewpts_7d = hourly7d.get("dewpts", [])
                pressures_7d = hourly7d.get("pressures", [])

                # WU devuelve presiones MSL → convertir a absoluta
                if provider_id == "WU":
                    _z7 = st.session_state.get("station_elevation", 0)
                    _f7 = math.exp(-_z7 / 8000.0)
                    pressures_7d = [
                        p * _f7 if not is_nan(p) else float("nan")
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
                            rh = 100.0 * e_td / e_s_t if e_s_t > 0 else float("nan")
                            humidities_7d.append(rh)

                dt_list = []
                temp_list = []
                rh_list = []
                p_list = []

                for i, epoch in enumerate(epochs_7d):
                    dt_list.append(datetime.fromtimestamp(epoch))
                    temp_list.append(float(temps_7d[i]) if i < len(temps_7d) else float("nan"))
                    rh_list.append(float(humidities_7d[i]) if i < len(humidities_7d) else float("nan"))
                    p_list.append(float(pressures_7d[i]) if i < len(pressures_7d) else float("nan"))

                df_trends = pd.DataFrame({
                    "dt": dt_list,
                    "temp": temp_list,
                    "rh": rh_list,
                    "p": p_list,
                }).sort_values("dt")

                if df_trends.empty:
                    st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                    logger.warning(f"Tendencia sinóptica {provider_id}: DataFrame vacío")
                else:
                    df_trends["dt"] = pd.to_datetime(df_trends["dt"])
                    df_trends["dt"] = df_trends["dt"].dt.floor("3h")
                    df_trends = df_trends.groupby("dt", as_index=False).last()
                    grid = pd.to_datetime(df_trends["dt"].values)

                    day_start = df_trends["dt"].min()
                    day_end = df_trends["dt"].max()

                    series_step_min = max(180, _infer_series_step_minutes(df_trends["dt"]))
                    interval_theta_e = max(180, series_step_min)
                    interval_e = max(180, series_step_min)
                    interval_p = 180
                    dataset_ready = True

        if dataset_ready:
            if data_source_label:
                if periodo == "synoptic" and "3 h" not in data_source_label and "3h" not in data_source_label:
                    data_source_label = t("trends.sources.sampling_suffix_3h", source=data_source_label)
                st.caption(t("trends.source_label", source=data_source_label))

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
            if not has_pressure_for_theta_e or not has_humidity_series or not has_barometer_series:
                if not has_pressure_for_theta_e and not has_humidity_series:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                        t("trends.calculations.vapor_pressure"),
                    ]
                elif not has_pressure_for_theta_e:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                    ]
                elif not has_humidity_series:
                    missing_trend_calculations = [
                        t("trends.calculations.theta_e"),
                        t("trends.calculations.vapor_pressure"),
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
                    theta_e_list = []
                    for _, row in df_trends.iterrows():
                        p_theta = row.get("theta_e_pressure", row.get("p"))
                        if not (pd.isna(row["temp"]) or pd.isna(row["rh"]) or pd.isna(p_theta)):
                            theta_e = equivalent_potential_temperature(row["temp"], row["rh"], p_theta)
                            theta_e_list.append(theta_e)
                        else:
                            theta_e_list.append(np.nan)

                    df_trends["theta_e"] = theta_e_list
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

                        fig_theta_e.update_layout(
                            title=dict(text=t("trends.charts.theta_e_title"),
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text=t("common.hour"), font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text=f"dθe/dt ({temp_unit_txt}/h)", font=dict(color=text_color)), range=y_range_theta_e,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            hovermode="x unified", height=400, margin=dict(l=60, r=40, t=60, b=60),
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

                        _plotly_chart_stretch(fig_theta_e, key=f"theta_e_graph_{theme_mode}_{periodo}")
                except Exception as err:
                    st.error(t("trends.errors.theta_e", error=str(err)))
                    logger.error(f"Error tendencia θe: {repr(err)}")

            # --- GRÁFICO 2: Tendencia de e (presión de vapor) ---
            if has_humidity_series:
                try:
                    from models.trends import vapor_pressure

                    e_list = []
                    for _, row in df_trends.iterrows():
                        if not (pd.isna(row["temp"]) or pd.isna(row["rh"])):
                            e = vapor_pressure(row["temp"], row["rh"])
                            e_list.append(e)
                        else:
                            e_list.append(np.nan)

                    df_trends["e"] = e_list
                    trend_e = calculate_trend(
                        np.asarray(df_trends["e"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_e,
                    )
                    trend_e_display = np.asarray(
                        [
                            convert_pressure(value, pressure_unit_pref) if not np.isnan(value) else np.nan
                            for value in trend_e
                        ],
                        dtype=np.float64,
                    )

                    valid_trends_e = trend_e_display[~np.isnan(trend_e_display)]
                    if len(valid_trends_e) == 0:
                        st.warning(t("trends.warnings.no_vapor_pressure"))
                    else:
                        max_abs_e = max(abs(valid_trends_e.min()), abs(valid_trends_e.max()))
                        y_range_e = [-max_abs_e * 1.1, max_abs_e * 1.1]

                        fig_e = go.Figure()
                        fig_e.add_trace(go.Scatter(
                            x=trend_times, y=trend_e_display, mode="lines+markers", name="de/dt",
                            line=dict(color="rgb(107, 170, 255)", width=2.5),
                            marker=dict(size=5, color="rgb(107, 170, 255)"), connectgaps=True
                        ))
                        fig_e.add_vline(
                            x=now_local,
                            line_width=1.2,
                            line_dash="dot",
                            line_color=now_line_color,
                            opacity=0.85,
                        )
                        fig_e.add_hline(y=0, line_width=1.2, line_dash="dash", opacity=0.75, line_color=zero_line_color)

                        fig_e.update_layout(
                            title=dict(text=t("trends.charts.vapor_pressure_title"),
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text=t("common.hour"), font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text=f"de/dt ({pressure_unit_txt}/h)", font=dict(color=text_color)), range=y_range_e,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            hovermode="x unified", height=400, margin=dict(l=60, r=40, t=60, b=60),
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

                        _plotly_chart_stretch(fig_e, key=f"e_graph_{theme_mode}_{periodo}")
                except Exception as err:
                    st.error(t("trends.errors.vapor_pressure", error=str(err)))
                    logger.error(f"Error tendencia e: {repr(err)}")

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

                            fig_uv.update_layout(
                                title=dict(
                                    text=t("trends.charts.uv_title"),
                                    x=0.5,
                                    xanchor="center",
                                    font=dict(size=18, color=text_color),
                                ),
                                showlegend=True,
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom",
                                    y=1.02,
                                    xanchor="center",
                                    x=0.5,
                                ),
                                xaxis=dict(
                                    title=dict(text=t("common.hour"), font=dict(color=text_color)),
                                    type="date",
                                    range=[day_start, day_end],
                                    tickformat=tickformat,
                                    dtick=dtick_ms,
                                    gridcolor=grid_color,
                                    showgrid=True,
                                    tickfont=dict(color=text_color),
                                ),
                                yaxis=dict(
                                    title=dict(text=wind_unit_txt, font=dict(color=text_color)),
                                    range=y_range_uv,
                                    gridcolor=grid_color,
                                    showgrid=True,
                                    tickfont=dict(color=text_color),
                                ),
                                plot_bgcolor="rgba(0,0,0,0)",
                                paper_bgcolor="rgba(0,0,0,0)",
                                hovermode="x unified",
                                height=400,
                                margin=dict(l=60, r=40, t=90, b=60),
                                font=dict(
                                    family='system-ui, -apple-system, "Segoe UI", Roboto, Arial',
                                    color=text_color,
                                ),
                                annotations=[dict(
                                    text="meteolabx.com",
                                    xref="paper", yref="paper",
                                    x=0.98, y=0.02,
                                    xanchor="right", yanchor="bottom",
                                    showarrow=False,
                                    font=dict(size=10, color="rgba(128,128,128,0.5)")
                                )],
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

                    if periodo == "today" and provider_id == "METEOFRANCE":
                        station_id = str(
                            st.session_state.get("meteofrance_station_id", "")
                            or st.session_state.get("provider_station_id", "")
                        ).strip()
                        if station_id:
                            meteofrance_service = _get_meteofrance_service()
                            pressure_payload = meteofrance_service.fetch_meteofrance_today_pressure_series_with_lookback(
                                station_id,
                                meteofrance_service.METEOFRANCE_API_KEY,
                                hours_before_start=3,
                            )
                            ext_epochs = pressure_payload.get("epochs", [])
                            ext_pressures = pressure_payload.get("pressures_abs", [])
                            if pressure_payload.get("has_data") and len(ext_epochs) == len(ext_pressures):
                                df_pressure_ext = pd.DataFrame({
                                    "dt": [datetime.fromtimestamp(ep) for ep in ext_epochs],
                                    "p": ext_pressures,
                                }).sort_values("dt")
                                if not df_pressure_ext.empty:
                                    ext_times = pd.to_datetime(df_pressure_ext["dt"])
                                    ext_step_min = max(60, _infer_series_step_minutes(ext_times))
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
                                    if bool(today_mask.any()):
                                        pressure_trend_times = ext_times[today_mask].reset_index(drop=True)
                                        pressure_trend_values = np.asarray(ext_trend_p[today_mask.to_numpy()], dtype=np.float64)
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

                        fig_p.update_layout(
                            title=dict(text=t("trends.charts.pressure_title"),
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text=t("common.hour"), font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text=f"dp/dt ({pressure_unit_txt}/h)", font=dict(color=text_color)), range=y_range_p,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                            hovermode="x unified", height=400, margin=dict(l=60, r=40, t=60, b=60),
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

                        bindHoverSync();

                        if (!host.__mlbxHoverSyncObserver) {{
                            host.__mlbxHoverSyncObserver = new host.MutationObserver(() => bindHoverSync());
                            host.__mlbxHoverSyncObserver.observe(doc.body, {{ childList: true, subtree: true }});
                        }}
                    }})();
                    </script>
                    """,
                    height=0,
                    width=0,
                )

# ============================================================
# TAB 3: HISTORICO
# ============================================================

elif active_tab == "historical":
    section_title(t("historical.section_title"))

    if not connected:
        st.info(t("historical.connect_prompt"))
    else:
        provider_id = str(st.session_state.get("connection_type", "WU")).strip().upper() or "WU"
        if provider_id == "NWS":
            _render_neutral_info_note(t("historical.notes.nws_unavailable"))
        elif provider_id == "EUSKALMET":
            _render_neutral_info_note(t("historical.notes.euskalmet_unavailable"))
        elif provider_id not in ("WU", "METEOCAT", "AEMET", "FROST", "METEOFRANCE", "METEOGALICIA"):
            st.info(t("historical.notes.implemented_providers"))
        else:
            if provider_id == "WU":
                station_id = str(
                    st.session_state.get("active_station", "")
                    or st.session_state.get("wu_connected_station", "")
                ).strip()
                api_key = str(
                    st.session_state.get("active_key", "")
                    or st.session_state.get("wu_connected_api_key", "")
                ).strip()
            elif provider_id == "METEOCAT":
                station_id = str(
                    st.session_state.get("meteocat_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip().upper()
                api_key = None
                series_start_iso = _get_meteocat_service().get_meteocat_station_series_start_date(station_id)
                if series_start_iso:
                    try:
                        start_txt = datetime.fromisoformat(series_start_iso).strftime("%d/%m/%Y")
                    except Exception:
                        start_txt = str(series_start_iso)
                    st.caption(
                        t(
                            "historical.notes.series_start",
                            provider="Meteocat",
                            value=start_txt,
                        )
                    )
                else:
                    st.caption(t("historical.notes.series_start_unavailable", provider="Meteocat"))
            elif provider_id == "AEMET":
                station_id = str(
                    st.session_state.get("aemet_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip().upper()
                api_key = _get_aemet_service().AEMET_API_KEY
            elif provider_id == "FROST":
                station_id = str(
                    st.session_state.get("frost_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip().upper()
                api_key = None
            elif provider_id == "METEOFRANCE":
                station_id = str(
                    st.session_state.get("meteofrance_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip()
                meteofrance_service = _get_meteofrance_service()
                api_key = meteofrance_service.METEOFRANCE_API_KEY
                series_start_iso = meteofrance_service.get_meteofrance_station_series_start_date(station_id)
                if series_start_iso:
                    st.caption(
                        t(
                            "historical.notes.series_start",
                            provider="Meteo-France",
                            value=series_start_iso,
                        )
                    )
                else:
                    st.caption(t("historical.notes.series_start_unavailable", provider="Meteo-France"))
            else:
                # METEOGALICIA
                station_id = str(
                    st.session_state.get("meteogalicia_station_id", "")
                    or st.session_state.get("provider_station_id", "")
                ).strip()
                api_key = None

            if provider_id == "WU" and (not station_id or not api_key):
                st.warning(t("historical.errors.missing_wu_credentials"))
            elif provider_id == "METEOCAT" and not station_id:
                st.warning(t("historical.errors.missing_meteocat_station"))
            elif provider_id == "AEMET" and not station_id:
                st.warning(t("historical.errors.missing_aemet_station"))
            elif provider_id == "FROST" and not station_id:
                st.warning(t("historical.errors.missing_frost_station"))
            elif provider_id == "METEOFRANCE" and not station_id:
                st.warning(t("historical.errors.missing_meteofrance_station"))
            elif provider_id == "METEOGALICIA" and not station_id:
                st.warning(t("historical.errors.missing_meteogalicia_station"))
            else:
                import pandas as pd
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots
                climograms_service = _get_climograms_service()

                now_local = datetime.now()
                min_year = 1990
                year_floor = max(min_year, now_local.year - 35)
                year_options = list(range(now_local.year, year_floor - 1, -1))
                default_year = now_local.year
                summary_mode_options = ["monthly", "annual"]
                legacy_summary_mode_aliases = {"Mensual": "monthly", "Anual": "annual"}
                current_summary_mode = legacy_summary_mode_aliases.get(
                    str(st.session_state.get("climo_summary_mode", "")).strip(),
                    str(st.session_state.get("climo_summary_mode", "")).strip(),
                )
                if current_summary_mode not in summary_mode_options:
                    current_summary_mode = summary_mode_options[0]
                st.session_state["climo_summary_mode"] = current_summary_mode

                summary_mode = st.radio(
                    t("historical.summary.label"),
                    summary_mode_options,
                    horizontal=True,
                    format_func=lambda mode: t(f"historical.summary.options.{mode}"),
                    key="climo_summary_mode",
                )

                selected_months = []
                selected_years = []
                frost_selected_period = ""
                frost_selected_periods = []
                frost_period_options = {"monthly": [], "annual": []}

                if provider_id == "FROST":
                    frost_service = _get_frost_service()
                    frost_period_options = frost_service.get_frost_climo_period_options(
                        station_id=station_id,
                        client_id=frost_service.FROST_CLIENT_ID,
                        client_secret=frost_service.FROST_CLIENT_SECRET,
                    )

                if provider_id == "FROST":
                    if summary_mode == "monthly":
                        monthly_periods = frost_period_options.get("monthly", [])
                        default_period = monthly_periods[-1] if monthly_periods else None
                        period_col, month_col = st.columns(2)
                        with period_col:
                            frost_selected_period = st.selectbox(
                                t("historical.inputs.climate_period"),
                                options=monthly_periods,
                                index=(len(monthly_periods) - 1) if monthly_periods else None,
                                key="frost_climo_period_monthly_select",
                            ) if monthly_periods else ""
                        with month_col:
                            selected_months = st.multiselect(
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
                                    period=frost_selected_period or default_period,
                                    months=len(selected_months),
                                )
                            )
                    else:
                        annual_periods = frost_period_options.get("annual", [])
                        frost_selected_periods = st.multiselect(
                            t("historical.inputs.climate_periods"),
                            options=annual_periods,
                            default=annual_periods[-1:] if annual_periods else [],
                            key="frost_climo_periods_annual_select",
                        )
                        if frost_selected_periods:
                            st.caption(
                                t(
                                    "historical.caption.frost_periods_summary",
                                    periods=", ".join(frost_selected_periods),
                                )
                            )
                else:
                    if summary_mode == "monthly":
                        month_col, year_col = st.columns(2)
                        with month_col:
                            selected_months = st.multiselect(
                                t("historical.inputs.months"),
                                options=list(range(1, 13)),
                                default=[now_local.month],
                                format_func=lambda m: month_name(int(m)),
                                key="climo_months_select",
                            )
                        with year_col:
                            selected_years = st.multiselect(
                                t("historical.inputs.years"),
                                options=year_options,
                                default=[default_year],
                                key="climo_years_monthly_select",
                            )
                    else:
                        selected_years = st.multiselect(
                            t("historical.inputs.years"),
                            options=year_options,
                            default=[default_year],
                            key="climo_years_annual_select",
                        )

                max_monthly_blocks = 12
                if provider_id != "FROST" and summary_mode == "monthly":
                    monthly_blocks = len(selected_months) * len(selected_years)
                    if monthly_blocks > max_monthly_blocks:
                        st.warning(
                            t(
                                "historical.warnings.max_monthly_blocks",
                                max_blocks=max_monthly_blocks,
                                selected_blocks=monthly_blocks,
                            )
                        )

                historical_ready = False
                periods = []

                if provider_id == "FROST":
                    if not frost_period_options.get("monthly") and not frost_period_options.get("annual"):
                        _render_neutral_info_note(t("historical.notes.frost_unavailable"))
                    elif summary_mode == "monthly" and (not frost_selected_period or not selected_months):
                        st.info(t("historical.info.select_frost_period_and_month"))
                    elif summary_mode == "annual" and not frost_selected_periods:
                        st.info(t("historical.info.select_frost_period"))
                    else:
                        historical_ready = True
                elif not selected_years or (summary_mode == "monthly" and not selected_months):
                    if summary_mode == "monthly":
                        st.info(t("historical.info.select_month_and_year"))
                    else:
                        st.info(t("historical.info.select_year"))
                elif summary_mode == "monthly" and (len(selected_months) * len(selected_years) > max_monthly_blocks):
                    st.stop()
                else:
                    periods = climograms_service.build_period_specs(summary_mode, selected_years, selected_months)
                    if not periods:
                        st.warning(t("historical.warnings.invalid_period"))
                    else:
                        total_days_requested = sum((period.end - period.start).days + 1 for period in periods)
                        st.caption(
                            t(
                                "historical.caption.period_summary",
                                period_range=climograms_service.describe_period_range(periods),
                                blocks=len(periods),
                                days=total_days_requested,
                            )
                        )
                        historical_ready = True

                if historical_ready:
                        daily_df = None
                        extremes_overrides = None
                        if provider_id == "WU":
                            provider_label = "WU"
                        elif provider_id == "FROST":
                            provider_label = "Frost"
                        elif provider_id == "AEMET":
                            provider_label = "AEMET"
                        elif provider_id == "METEOFRANCE":
                            provider_label = "Meteo-France"
                        elif provider_id == "METEOGALICIA":
                            provider_label = "MeteoGalicia"
                        else:
                            provider_label = "Meteocat"
                        with st.spinner(t("historical.spinner.loading", provider=provider_label)):
                            try:
                                if provider_id == "WU":
                                    daily_df = climograms_service.fetch_wu_daily_history_for_periods(
                                        station_id=station_id,
                                        api_key=api_key,
                                        periods=periods,
                                    )
                                elif provider_id == "FROST":
                                    frost_service = _get_frost_service()
                                    if summary_mode == "monthly":
                                        daily_df = frost_service.fetch_frost_climo_monthly_for_period(
                                            station_id=station_id,
                                            period=frost_selected_period,
                                            months=selected_months,
                                            client_id=frost_service.FROST_CLIENT_ID,
                                            client_secret=frost_service.FROST_CLIENT_SECRET,
                                        )
                                    else:
                                        daily_df = frost_service.fetch_frost_climo_yearly_for_periods(
                                            station_id=station_id,
                                            periods=frost_selected_periods,
                                            client_id=frost_service.FROST_CLIENT_ID,
                                            client_secret=frost_service.FROST_CLIENT_SECRET,
                                        )
                                elif provider_id == "AEMET":
                                    aemet_service = _get_aemet_service()
                                    if summary_mode == "monthly":
                                        daily_df = aemet_service.fetch_aemet_climo_daily_for_periods(
                                            idema=station_id,
                                            periods=periods,
                                            api_key=api_key,
                                        )
                                    elif len(selected_years) == 1:
                                        daily_df = aemet_service.fetch_aemet_climo_monthly_for_year(
                                            idema=station_id,
                                            year=int(selected_years[0]),
                                            api_key=api_key,
                                        )
                                    else:
                                        daily_df = aemet_service.fetch_aemet_climo_yearly_for_years(
                                            idema=station_id,
                                            years=[int(year) for year in selected_years],
                                            api_key=api_key,
                                        )
                                elif provider_id == "METEOFRANCE":
                                    meteofrance_service = _get_meteofrance_service()
                                    if summary_mode == "monthly":
                                        daily_df = meteofrance_service.fetch_meteofrance_climo_daily_for_periods(
                                            station_id=station_id,
                                            periods=periods,
                                            api_key=api_key,
                                        )
                                    elif len(selected_years) == 1:
                                        daily_df = meteofrance_service.fetch_meteofrance_climo_monthly_for_year(
                                            station_id=station_id,
                                            year=int(selected_years[0]),
                                            api_key=api_key,
                                        )
                                    else:
                                        daily_df = meteofrance_service.fetch_meteofrance_climo_yearly_for_years(
                                            station_id=station_id,
                                            years=[int(year) for year in selected_years],
                                            api_key=api_key,
                                        )
                                elif provider_id == "METEOGALICIA":
                                    meteogalicia_service = _get_meteogalicia_service()
                                    if summary_mode == "monthly":
                                        daily_df = meteogalicia_service.fetch_mgalicia_climo_daily_for_periods(
                                            station_id=station_id,
                                            periods=periods,
                                        )
                                    elif len(selected_years) == 1:
                                        daily_df = meteogalicia_service.fetch_mgalicia_climo_monthly_for_year(
                                            station_id=station_id,
                                            year=int(selected_years[0]),
                                        )
                                    else:
                                        daily_df = meteogalicia_service.fetch_mgalicia_climo_yearly_for_years(
                                            station_id=station_id,
                                            years=[int(year) for year in selected_years],
                                        )
                                else:
                                    meteocat_service = _get_meteocat_service()
                                    if summary_mode == "annual" and len(selected_years) > 1:
                                        daily_df = meteocat_service.fetch_meteocat_annual_history_for_years(
                                            station_code=station_id,
                                            years=[int(year) for year in selected_years],
                                        )
                                    elif summary_mode == "annual" and len(selected_years) == 1:
                                        selected_year = int(selected_years[0])
                                        daily_df = meteocat_service.fetch_meteocat_monthly_history_for_year(
                                            station_code=station_id,
                                            year=selected_year,
                                        )
                                        extremes_overrides = meteocat_service.fetch_meteocat_daily_extremes_for_year(
                                            station_code=station_id,
                                            year=selected_year,
                                        )
                                    elif summary_mode == "monthly":
                                        if len(periods) == 1:
                                            daily_df = meteocat_service.fetch_meteocat_daily_history_for_periods(
                                                station_code=station_id,
                                                periods=periods,
                                            )
                                        else:
                                            daily_df = meteocat_service.fetch_meteocat_monthly_history_for_periods(
                                                station_code=station_id,
                                                periods=periods,
                                            )
                                        extremes_overrides = meteocat_service.fetch_meteocat_daily_extremes_for_periods(
                                            station_code=station_id,
                                            periods=periods,
                                        )
                                    else:
                                        daily_df = meteocat_service.fetch_meteocat_daily_history_for_periods(
                                            station_code=station_id,
                                            periods=periods,
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
                                import traceback
                                traceback.print_exc()
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
                                    if chart_granularity == "daily":
                                        x_title = t("historical.chart.x.day")
                                        title_scope = t("historical.chart.scope.daily")
                                    elif chart_granularity == "monthly":
                                        x_title = (
                                            t("historical.chart.x.month")
                                            if provider_id != "FROST"
                                            else t("historical.chart.x.month")
                                        )
                                        title_scope = (
                                            t("historical.chart.scope.monthly")
                                            if provider_id != "FROST"
                                            else t("historical.chart.scope.monthly_normals")
                                        )
                                    else:
                                        x_title = (
                                            t("historical.chart.x.year")
                                            if provider_id != "FROST"
                                            else t("historical.chart.x.climate_period")
                                        )
                                        title_scope = (
                                            t("historical.chart.scope.yearly")
                                            if provider_id != "FROST"
                                            else t("historical.chart.scope.climate_periods")
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

                                    if chart_granularity == "daily":
                                        table_scope = t("historical.table.scope.day")
                                        table_period_col = t("historical.table.period_col.day")
                                    elif chart_granularity == "monthly":
                                        table_scope = t("historical.table.scope.month")
                                        table_period_col = t("historical.table.period_col.month")
                                    else:
                                        table_scope = (
                                            t("historical.table.scope.year")
                                            if provider_id != "FROST"
                                            else t("historical.table.scope.climate_period")
                                        )
                                        table_period_col = (
                                            t("historical.table.period_col.year")
                                            if provider_id != "FROST"
                                            else t("historical.table.period_col.climate_period")
                                        )

                                    units_df = climograms_service.build_units_table(
                                        daily_df,
                                        chart_granularity,
                                        unit_preferences=unit_preferences,
                                    )
                                    table_df = units_df[
                                        ["label", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"]
                                    ].copy()
                                    table_df = table_df.rename(
                                        columns={
                                            "label": table_period_col,
                                            "temp_abs_max": f"{t('historical.table.columns.temp_abs_max')} ({temp_unit_txt})",
                                            "temp_abs_min": f"{t('historical.table.columns.temp_abs_min')} ({temp_unit_txt})",
                                            "temp_mean": f"{t('historical.table.columns.temp_mean')} ({temp_unit_txt})",
                                            "precip_total": f"{t('historical.table.columns.precip')} ({precip_unit_txt})",
                                        }
                                    )
                                    for col_name in [
                                        f"{t('historical.table.columns.temp_abs_max')} ({temp_unit_txt})",
                                        f"{t('historical.table.columns.temp_abs_min')} ({temp_unit_txt})",
                                        f"{t('historical.table.columns.temp_mean')} ({temp_unit_txt})",
                                        f"{t('historical.table.columns.precip')} ({precip_unit_txt})",
                                    ]:
                                        table_df[col_name] = pd.to_numeric(table_df[col_name], errors="coerce")
                                        table_df[col_name] = table_df[col_name].apply(
                                            lambda value: "—" if pd.isna(value) else f"{float(value):.1f}"
                                        )

                                    st.markdown(f"### {t('historical.sections.data_by', scope=table_scope)}")
                                    _render_theme_table(table_df)


# ============================================================
# TAB 4: DIVULGACIÓN
# ============================================================

elif active_tab == "info":
    st.info(t("info.coming_soon"))
    st.markdown(t("info.description"))

# ============================================================
# TAB 5: MAPA
# ============================================================

elif active_tab == "map":
    import pydeck as pdk

    section_title(t("map.section_title"))

    def _safe_float(value, default=None):
        try:
            number = float(value)
            if math.isnan(number):
                return default
            return number
        except Exception:
            return default

    def _first_valid_float(*values, default):
        for value in values:
            parsed = _safe_float(value, default=None)
            if parsed is not None:
                return parsed
        return float(default)

    def _map_default_coords():
        lat = _first_valid_float(
            st.session_state.get("provider_station_lat"),
            st.session_state.get("aemet_station_lat"),
            st.session_state.get("station_lat"),
            default=40.4168,
        )
        lon = _first_valid_float(
            st.session_state.get("provider_station_lon"),
            st.session_state.get("aemet_station_lon"),
            st.session_state.get("station_lon"),
            default=-3.7038,
        )
        return float(lat), float(lon)

    def _normalize_coords(lat: float, lon: float):
        lat = float(lat)
        lon = float(lon)
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon, False
        if -90.0 <= lon <= 90.0 and -180.0 <= lat <= 180.0:
            return lon, lat, True
        return lat, lon, False

    def _zoom_for_max_distance(max_distance_km: float) -> float:
        if max_distance_km <= 5:
            return 10.8
        if max_distance_km <= 15:
            return 9.5
        if max_distance_km <= 35:
            return 8.3
        if max_distance_km <= 80:
            return 7.3
        if max_distance_km <= 180:
            return 6.3
        return 5.5

    def _is_us_map_center(lat: float, lon: float) -> bool:
        # Caja amplia para EEUU + Alaska/territorios en longitudes oeste.
        return 17.0 <= float(lat) <= 72.5 and -178.0 <= float(lon) <= -52.0

    def _is_iberia_map_center(lat: float, lon: float) -> bool:
        return 27.0 <= float(lat) <= 45.5 and -19.5 <= float(lon) <= 5.5

    def _is_france_map_center(lat: float, lon: float) -> bool:
        return 41.0 <= float(lat) <= 51.8 and -5.8 <= float(lon) <= 10.2

    def _is_norway_map_center(lat: float, lon: float) -> bool:
        return 57.0 <= float(lat) <= 72.5 and 2.0 <= float(lon) <= 32.5

    def _provider_is_near_center(provider_id: str, lat: float, lon: float) -> bool:
        pid = str(provider_id or "").strip().upper()
        if pid == "NWS":
            return _is_us_map_center(lat, lon)
        if pid == "FROST":
            return _is_norway_map_center(lat, lon)
        if pid == "METEOFRANCE":
            return _is_iberia_map_center(lat, lon) or _is_france_map_center(lat, lon)
        if pid in {"AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "POEM"}:
            return _is_iberia_map_center(lat, lon)
        return True

    def _provider_region(provider_id: str) -> str:
        pid = str(provider_id or "").strip().upper()
        if pid == "NWS":
            return "US"
        if pid == "FROST":
            return "NORWAY"
        if pid == "METEOFRANCE":
            return "FRANCE"
        if pid in {"AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "POEM"}:
            return "IBERIA"
        return "GLOBAL"

    if "map_geo_request_id" not in st.session_state:
        st.session_state["map_geo_request_id"] = 10000
    if "map_geo_pending" not in st.session_state:
        st.session_state["map_geo_pending"] = False
    if "map_geo_last_error" not in st.session_state:
        st.session_state["map_geo_last_error"] = ""
    if "map_geo_debug_msg" not in st.session_state:
        st.session_state["map_geo_debug_msg"] = ""

    default_lat, default_lon = _map_default_coords()
    if "map_search_lat" not in st.session_state or _safe_float(st.session_state.get("map_search_lat")) is None:
        st.session_state["map_search_lat"] = default_lat
    if "map_search_lon" not in st.session_state or _safe_float(st.session_state.get("map_search_lon")) is None:
        st.session_state["map_search_lon"] = default_lon
    if "map_provider_filter_near" not in st.session_state:
        st.session_state["map_provider_filter_near"] = []
    if "map_provider_filter_far" not in st.session_state:
        st.session_state["map_provider_filter_far"] = []

    browser_geo_result = None
    if st.session_state.get("map_geo_pending"):
        browser_geo_result = get_browser_geolocation(
            request_id=st.session_state["map_geo_request_id"],
            timeout_ms=12000,
            high_accuracy=True,
        )

    if st.session_state.get("map_geo_pending") and isinstance(browser_geo_result, dict):
        st.session_state["map_geo_pending"] = False
        if browser_geo_result.get("ok"):
            lat = browser_geo_result.get("lat")
            lon = browser_geo_result.get("lon")
            if lat is not None and lon is not None:
                lat, lon, swapped = _normalize_coords(lat, lon)
                st.session_state["map_search_lat"] = lat
                st.session_state["map_search_lon"] = lon
                acc = browser_geo_result.get("accuracy_m")
                if isinstance(acc, (int, float)):
                    st.session_state["map_geo_debug_msg"] = t("map.geo_detected_accuracy", accuracy=acc)
                else:
                    st.session_state["map_geo_debug_msg"] = t("map.geo_detected")
                if swapped:
                    st.session_state["map_geo_debug_msg"] += t("map.coords_swapped")
                st.session_state["map_geo_last_error"] = ""
                st.rerun()

        error_message = browser_geo_result.get("error_message") or t("map.geo_error_default")
        st.session_state["map_geo_last_error"] = str(error_message)
        st.session_state["map_geo_debug_msg"] = ""

    search_lat = float(st.session_state.get("map_search_lat"))
    search_lon = float(st.session_state.get("map_search_lon"))
    all_provider_options = ["AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE", "METEOGALICIA", "NWS", "POEM"]
    near_provider_options = [
        provider_id
        for provider_id in all_provider_options
        if _provider_is_near_center(provider_id, search_lat, search_lon)
    ]
    far_provider_options = [
        provider_id
        for provider_id in all_provider_options
        if provider_id not in near_provider_options
    ]

    selected_near_state = [
        provider_id
        for provider_id in st.session_state.get("map_provider_filter_near", [])
        if provider_id in near_provider_options
    ]
    if selected_near_state != st.session_state.get("map_provider_filter_near", []):
        st.session_state["map_provider_filter_near"] = selected_near_state
    if not selected_near_state:
        st.session_state["map_provider_filter_near"] = list(near_provider_options)

    selected_far_state = [
        provider_id
        for provider_id in st.session_state.get("map_provider_filter_far", [])
        if provider_id in far_provider_options
    ]
    if selected_far_state != st.session_state.get("map_provider_filter_far", []):
        st.session_state["map_provider_filter_far"] = selected_far_state

    controls_col, filters_col = st.columns([1.1, 1], gap="large")
    with controls_col:
        st.markdown(f"#### {t('map.location_title')}")
        if st.button(t("map.use_my_location"), type="primary", width="stretch"):
            st.session_state["map_geo_request_id"] += 1
            st.session_state["map_geo_pending"] = True
            st.session_state["map_geo_last_error"] = ""
            st.session_state["map_geo_debug_msg"] = "Solicitando ubicación al navegador..."
            st.rerun()

        if st.session_state.get("map_geo_pending"):
            st.caption(t("map.waiting_geolocation"))

        geo_last_error = st.session_state.get("map_geo_last_error", "").strip()
        if geo_last_error:
            st.warning(t("map.gps_unavailable"))
            st.caption(t("map.browser_detail", detail=geo_last_error))

        geo_debug_msg = st.session_state.get("map_geo_debug_msg", "")
        if geo_debug_msg:
            st.caption(geo_debug_msg)
        st.caption(
            t(
                "map.center_current",
                lat=float(st.session_state.get("map_search_lat")),
                lon=float(st.session_state.get("map_search_lon")),
            )
        )

    with filters_col:
        st.markdown(f"#### {t('map.filters_title')}")
        st.multiselect(
            t("map.nearby_providers"),
            options=near_provider_options,
            key="map_provider_filter_near",
        )
        if far_provider_options:
            st.multiselect(
                t("map.far_providers"),
                options=far_provider_options,
                key="map_provider_filter_far",
            )
        st.caption(t("map.filters_caption"))

    selected_near = set(st.session_state.get("map_provider_filter_near", []))
    selected_far = set(st.session_state.get("map_provider_filter_far", []))
    provider_filter = selected_near.union(selected_far)
    effective_provider_ids = sorted(provider_filter)

    map_max_results = 20000
    if "NWS" in effective_provider_ids:
        map_max_results = 90000

    nearest = []
    if effective_provider_ids:
        nearest = _cached_map_search_nearby_stations(
            float(search_lat),
            float(search_lon),
            int(map_max_results),
            tuple(effective_provider_ids),
        )
        nearest = [s for s in nearest if s.provider_id in provider_filter]
    visible_station_count = len(nearest)
    visible_provider_count = len({s.provider_id for s in nearest})

    with controls_col:
        metric_col1, metric_col2 = st.columns(2)
        metric_col1.metric(t("map.visible_stations"), visible_station_count)
        metric_col2.metric(t("map.providers"), visible_provider_count)

    if not nearest:
        st.warning(t("map.no_stations"))
    else:
        def _station_locality(station):
            meta = station.metadata if isinstance(station.metadata, dict) else {}
            if station.provider_id == "METEOCAT":
                municipi = meta.get("municipi")
                if isinstance(municipi, dict):
                    town = str(municipi.get("nom", "")).strip()
                    if town:
                        return town
            if station.provider_id == "EUSKALMET":
                municipality = meta.get("municipality")
                if isinstance(municipality, dict):
                    town = str(municipality.get("SPANISH", "")).strip() or str(municipality.get("BASQUE", "")).strip()
                    if town:
                        return town.replace("[eu] ", "").strip()
            if station.provider_id == "METEOFRANCE":
                pack = str(meta.get("pack", "")).strip()
                if pack:
                    return pack
            if station.provider_id == "FROST":
                municipality = str(meta.get("municipality", "")).strip()
                if municipality:
                    return municipality
            if station.provider_id == "METEOGALICIA":
                town = str(meta.get("concello", "")).strip()
                if town:
                    return town
            if station.provider_id == "NWS":
                tz_name = str(meta.get("tz", "")).strip()
                if tz_name:
                    return tz_name
            if station.provider_id == "POEM":
                station_type = str(meta.get("tipo", "")).strip()
                if station_type:
                    return station_type
            provincia = meta.get("provincia")
            if isinstance(provincia, dict):
                province_name = str(provincia.get("nom", "")).strip()
                if province_name:
                    return province_name
            province_txt = str(meta.get("provincia", "")).strip()
            if province_txt:
                return province_txt
            return str(station.name).strip()

        provider_colors = {
            "AEMET": [255, 75, 75],
            "METEOCAT": [58, 145, 255],
            "EUSKALMET": [55, 198, 124],
            "FROST": [78, 180, 218],
            "METEOFRANCE": [74, 124, 255],
            "METEOGALICIA": [255, 184, 64],
            "NWS": [178, 122, 255],
            "POEM": [14, 188, 212],
        }
        points = []
        for station in nearest:
            points.append(
                {
                    "lat": float(station.lat),
                    "lon": float(station.lon),
                    "name": station.name,
                    "provider": station.provider_name,
                    "provider_id": station.provider_id,
                    "station_id": station.station_id,
                    "distance_km": float(station.distance_km),
                    "distance_txt": f"{station.distance_km:.1f} km",
                    "locality": _station_locality(station),
                    "elevation_m": float(station.elevation_m),
                    "alt_txt": f"{station.elevation_m:.0f} m",
                    "station_tz": str((station.metadata or {}).get("tz", "")).strip() if isinstance(station.metadata, dict) else "",
                    "color": provider_colors.get(station.provider_id, [180, 180, 180]),
                    "radius": 170,
                }
            )

        def _connect_station_from_map(selected_station: dict) -> bool:
            provider_id = str(selected_station.get("provider_id", "")).strip().upper()
            station_id = str(selected_station.get("station_id", "")).strip()
            if not provider_id or not station_id:
                return False

            if provider_id == "AEMET":
                try:
                    _get_aemet_service().clear_aemet_runtime_cache()
                except Exception:
                    pass

            station_name = str(selected_station.get("name", "")).strip() or station_id
            lat = _safe_float(selected_station.get("lat"))
            lon = _safe_float(selected_station.get("lon"))
            elevation_m = _safe_float(selected_station.get("elevation_m"), default=0.0)
            station_tz = str(selected_station.get("station_tz", "")).strip()

            st.session_state["connection_type"] = provider_id
            st.session_state["provider_station_id"] = station_id
            st.session_state["provider_station_name"] = station_name
            st.session_state["provider_station_lat"] = lat
            st.session_state["provider_station_lon"] = lon
            st.session_state["provider_station_alt"] = elevation_m
            st.session_state["provider_station_tz"] = station_tz

            if provider_id == "AEMET":
                st.session_state["aemet_station_id"] = station_id
                st.session_state["aemet_station_name"] = station_name
                st.session_state["aemet_station_lat"] = lat
                st.session_state["aemet_station_lon"] = lon
                st.session_state["aemet_station_alt"] = elevation_m
            elif provider_id == "METEOCAT":
                st.session_state["meteocat_station_id"] = station_id
                st.session_state["meteocat_station_name"] = station_name
                st.session_state["meteocat_station_lat"] = lat
                st.session_state["meteocat_station_lon"] = lon
                st.session_state["meteocat_station_alt"] = elevation_m
            elif provider_id == "EUSKALMET":
                st.session_state["euskalmet_station_id"] = station_id
                st.session_state["euskalmet_station_name"] = station_name
                st.session_state["euskalmet_station_lat"] = lat
                st.session_state["euskalmet_station_lon"] = lon
                st.session_state["euskalmet_station_alt"] = elevation_m
            elif provider_id == "METEOFRANCE":
                st.session_state["meteofrance_station_id"] = station_id
                st.session_state["meteofrance_station_name"] = station_name
                st.session_state["meteofrance_station_lat"] = lat
                st.session_state["meteofrance_station_lon"] = lon
                st.session_state["meteofrance_station_alt"] = elevation_m
            elif provider_id == "FROST":
                st.session_state["frost_station_id"] = station_id
                st.session_state["frost_station_name"] = station_name
                st.session_state["frost_station_lat"] = lat
                st.session_state["frost_station_lon"] = lon
                st.session_state["frost_station_alt"] = elevation_m
            elif provider_id == "METEOGALICIA":
                st.session_state["meteogalicia_station_id"] = station_id
                st.session_state["meteogalicia_station_name"] = station_name
                st.session_state["meteogalicia_station_lat"] = lat
                st.session_state["meteogalicia_station_lon"] = lon
                st.session_state["meteogalicia_station_alt"] = elevation_m
            elif provider_id == "NWS":
                st.session_state["nws_station_id"] = station_id
                st.session_state["nws_station_name"] = station_name
                st.session_state["nws_station_lat"] = lat
                st.session_state["nws_station_lon"] = lon
                st.session_state["nws_station_alt"] = elevation_m
            elif provider_id == "POEM":
                st.session_state["poem_station_id"] = station_id
                st.session_state["poem_station_name"] = station_name
                st.session_state["poem_station_lat"] = lat
                st.session_state["poem_station_lon"] = lon
                st.session_state["poem_station_alt"] = elevation_m
            else:
                return False

            st.session_state["connected"] = True
            st.session_state["_pending_active_tab"] = "observation"
            st.session_state["map_selected_station"] = dict(selected_station)
            return True

        def _set_provider_autoconnect_from_map(selected_station: dict) -> bool:
            provider_id = str(selected_station.get("provider_id", "")).strip().upper()
            station_id = str(selected_station.get("station_id", "")).strip()
            if not provider_id or not station_id:
                return False

            station_name = str(selected_station.get("name", "")).strip() or station_id
            lat = _safe_float(selected_station.get("lat"), default=None)
            lon = _safe_float(selected_station.get("lon"), default=None)
            elevation_m = _safe_float(selected_station.get("elevation_m"), default=None)

            set_stored_autoconnect_target(
                {
                    "kind": "PROVIDER",
                    "provider_id": provider_id,
                    "station_id": station_id,
                    "station_name": station_name,
                    "lat": lat,
                    "lon": lon,
                    "elevation_m": elevation_m,
                }
            )
            set_local_storage(LS_AUTOCONNECT, "1", "save")
            # Evita autoconectar inmediatamente en esta sesión.
            st.session_state["_autoconnect_attempted"] = True
            return True

        def _reset_map_autoconnect_toggle_state() -> None:
            for state_key in list(st.session_state.keys()):
                if state_key.startswith("map_autoconnect_toggle_"):
                    del st.session_state[state_key]

        points_sorted = sorted(points, key=lambda p: float(p["distance_km"]))
        zoom_reference = points_sorted[: min(len(points_sorted), 2000)]
        max_distance = max((p["distance_km"] for p in zoom_reference), default=250.0)

        points_for_layer = list(points_sorted)

        map_style = (
            "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
            if dark else
            "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
        )
        map_tooltip_bg = "rgba(18, 18, 18, 0.92)" if dark else "rgba(255, 255, 255, 0.96)"
        map_tooltip_text = "rgba(255, 255, 255, 0.96)" if dark else "rgba(15, 18, 25, 0.96)"
        map_tooltip_border = "1px solid rgba(255,255,255,0.10)" if dark else "1px solid rgba(15,18,25,0.12)"
        map_tooltip_shadow = "0 10px 24px rgba(0,0,0,0.28)" if dark else "0 10px 24px rgba(0,0,0,0.12)"

        map_layers = [
            pdk.Layer(
                "ScatterplotLayer",
                id="stations-layer",
                data=points_for_layer,
                pickable=True,
                auto_highlight=True,
                filled=True,
                stroked=True,
                get_position="[lon, lat]",
                get_fill_color="color",
                get_line_color=[16, 20, 28, 140],
                line_width_min_pixels=1,
                get_radius="radius",
                radius_min_pixels=4,
                radius_max_pixels=24,
            ),
        ]
        map_layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                id="center-layer",
                data=[{"lat": search_lat, "lon": search_lon}],
                pickable=False,
                filled=True,
                stroked=True,
                get_position="[lon, lat]",
                get_fill_color=[255, 255, 255, 230],
                get_line_color=[25, 25, 25, 230],
                get_radius=220,
                radius_min_pixels=6,
                radius_max_pixels=10,
            )
        )

        deck = pdk.Deck(
            map_style=map_style,
            initial_view_state=pdk.ViewState(
                latitude=search_lat,
                longitude=search_lon,
                zoom=_zoom_for_max_distance(max_distance),
                pitch=0,
            ),
            layers=map_layers,
            tooltip={
                "html": "<b>{name}</b><br/>{provider} · ID {station_id}<br/>Distancia: {distance_txt}<br/>Altitud: {alt_txt}",
                "style": {
                    "backgroundColor": map_tooltip_bg,
                    "color": map_tooltip_text,
                    "fontSize": "12px",
                    "border": map_tooltip_border,
                    "borderRadius": "10px",
                    "boxShadow": map_tooltip_shadow,
                    "padding": "10px 12px",
                },
            },
        )

        deck_event = None
        try:
            deck_event = _pydeck_chart_stretch(
                deck,
                key=f"map_stations_chart_{theme_mode}",
                height=900,
            )
        except Exception as map_err:
            st.warning(f"No se pudo renderizar el mapa ({map_err}). Mostrando tabla de estaciones.")
        st.markdown("<div style='height:0.35rem;'></div>", unsafe_allow_html=True)

        selected_station = st.session_state.get("map_selected_station")
        selection_state = {}
        try:
            if hasattr(deck_event, "get"):
                selection_state = deck_event.get("selection", {}) or {}
            elif hasattr(deck_event, "selection"):
                selection_state = getattr(deck_event, "selection", {}) or {}
        except Exception:
            selection_state = {}
        try:
            selected_objects = selection_state.get("objects", {}) if hasattr(selection_state, "get") else {}
        except Exception:
            selected_objects = {}
        if isinstance(selected_objects, dict):
            selected_in_layer = selected_objects.get("stations-layer", [])
            if isinstance(selected_in_layer, list) and selected_in_layer:
                selected_station = selected_in_layer[0]
                st.session_state["map_selected_station"] = dict(selected_station)

        st.markdown(f"#### {t('map.selected_station')}")
        if isinstance(selected_station, dict):
            def _meta_chip(value: str) -> str:
                return f"<span class='mlbx-map-chip'>{html.escape(str(value))}</span>"

            selected_name = str(selected_station.get("name", "Estación"))
            selected_provider = str(selected_station.get("provider", "Proveedor"))
            selected_station_id = str(selected_station.get("station_id", "—"))
            selected_locality = str(selected_station.get("locality", "—"))
            selected_alt = _safe_float(selected_station.get("elevation_m"), default=None)
            selected_dist = _safe_float(selected_station.get("distance_km"), default=None)
            selected_lat = _safe_float(selected_station.get("lat"), default=None)
            selected_lon = _safe_float(selected_station.get("lon"), default=None)
            selected_alt_txt = "—" if selected_alt is None else f"{selected_alt:.0f} m"
            selected_dist_txt = "—" if selected_dist is None else f"{selected_dist:.1f} km"
            selected_coords_txt = (
                "—"
                if selected_lat is None or selected_lon is None
                else f"{selected_lat:.4f}, {selected_lon:.4f}"
            )

            info_col, action_col = st.columns([0.78, 0.22], gap="small")
            with info_col:
                st.markdown(
                    html_clean(
                        f"""
                        <div style="color: var(--text); font-size: 1.05rem; font-weight: 700; margin-bottom: 0.3rem;">
                            {html.escape(selected_name)} · {html.escape(selected_provider)}
                        </div>
                        <div class="mlbx-map-meta">
                            <span class="mlbx-map-meta-item">ID: {_meta_chip(selected_station_id)}</span>
                            <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.locality'))}: {_meta_chip(selected_locality)}</span>
                            <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.altitude').replace(' (m)', ''))}: {_meta_chip(selected_alt_txt)}</span>
                            <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.distance').replace(' (km)', ''))}: {_meta_chip(selected_dist_txt)}</span>
                            <span class="mlbx-map-meta-item">Lat/Lon: {_meta_chip(selected_coords_txt)}</span>
                        </div>
                        """
                    ),
                    unsafe_allow_html=True,
                )
                saved_autoconnect = bool(get_stored_autoconnect())
                saved_target = get_stored_autoconnect_target() or {}
                is_target_station = bool(
                    saved_autoconnect
                    and str(saved_target.get("kind", "")).strip().upper() == "PROVIDER"
                    and str(saved_target.get("provider_id", "")).strip().upper() == str(selected_station.get("provider_id", "")).strip().upper()
                    and str(saved_target.get("station_id", "")).strip() == selected_station_id
                )
                map_toggle_key = f"map_autoconnect_toggle_{selected_provider}_{selected_station_id}"
                if map_toggle_key not in st.session_state:
                    st.session_state[map_toggle_key] = is_target_station
                map_toggle_enabled = st.toggle(
                    t("map.autoconnect"),
                    value=bool(st.session_state.get(map_toggle_key, False)),
                    key=map_toggle_key,
                )
                if map_toggle_enabled and not is_target_station:
                    if _set_provider_autoconnect_from_map(selected_station):
                        _reset_map_autoconnect_toggle_state()
                        st.success(t("map.autoconnect_saved", station=selected_name))
                        st.rerun()
                    st.error(t("map.autoconnect_save_error"))
                elif (not map_toggle_enabled) and is_target_station:
                    set_local_storage(LS_AUTOCONNECT, "0", "save")
                    set_stored_autoconnect_target(None)
                    st.session_state["_autoconnect_attempted"] = True
                    _reset_map_autoconnect_toggle_state()
                    st.info(t("map.autoconnect_disabled"))
                    st.rerun()
            with action_col:
                connect_key = f"map_connect_btn_{selected_provider}_{selected_station_id}"
                if st.button(t("sidebar.buttons.connect"), key=connect_key, type="primary", width="stretch"):
                    if _connect_station_from_map(selected_station):
                        st.success(t("map.connect_success", station=selected_name))
                        st.rerun()
                    else:
                        st.error(t("map.connect_error"))
        else:
            st.caption(t("map.select_station_hint"))

# ============================================================
# AUTOREFRESH SOLO EN OBSERVACIÓN
# ============================================================
# Autorefresh solo se activa cuando el tab activo es Observación

if st.session_state.get("connected", False):
    if active_tab == "observation":
        refresh_interval = _provider_refresh_seconds()
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_interval * 1000, key="refresh_data")

# ============================================================
# FOOTER
# ============================================================

st.markdown(
    html_clean(
        """
        <style>
        .mlb-footer{
            margin-top: 1.25rem;
            padding-top: 0.8rem;
            border-top: 1px solid var(--line);
            color: var(--muted);
            font-size: 0.92rem;
        }
        .mlb-footer-top{
            display: flex;
            align-items: center;
            gap: 0.65rem;
            flex-wrap: wrap;
        }
        .mlb-footer-news details{
            display: inline-block;
        }
        .mlb-footer-news summary{
            list-style: none;
            cursor: pointer;
            color: #2f9cff;
            text-decoration: underline;
            text-underline-offset: 2px;
        }
        .mlb-footer-news summary::-webkit-details-marker{
            display: none;
        }
        .mlb-footer-box{
            margin-top: 0.6rem;
            padding: 0.8rem 0.95rem;
            border-radius: 10px;
            border: 1px solid var(--line);
            background: rgba(66, 133, 244, 0.08);
            color: var(--text);
            max-width: 920px;
        }
        .mlb-footer-box h3{
            margin: 0.55rem 0 0.3rem 0;
            font-size: 1.05rem;
        }
        .mlb-footer-box h3:first-child{
            margin-top: 0;
        }
        .mlb-footer-box ul{
            margin: 0.12rem 0 0.35rem 1.1rem;
            padding: 0;
        }
        .mlb-footer-bottom{
            margin-top: 0.52rem;
            font-size: 0.86rem;
            opacity: 0.92;
        }
        </style>
        <div class="mlb-footer">
          <div class="mlb-footer-top">
            <span><b>MeteoLabX · Versión 0.9.1</b></span>
            <span class="mlb-footer-news">
              <details>
                <summary>Novedades</summary>
                <div class="mlb-footer-box">
                  <h2 style="margin:0 0 0.6rem 0;">0.9.1</h2>
                  <h3>Mejoras</h3>
                  <ul>
                    <li>Nueva tarjeta de <b>dosis eritemática</b>.</li>
                    <li>Mejoras internas y de <b>rendimiento</b>.</li>
                    <li>Optimización del <b>arranque</b> de la app.</li>
                    <li>Mejoras en la visualización y nitidez de los <b>iconos</b>.</li>
                    <li>Pequeñas mejoras de la <b>interfaz</b>.</li>
                  </ul>
                  <h3>Correcciones</h3>
                  <ul>
                    <li>Se corrigen los errores relacionados con el cambio de <b>tema</b>.</li>
                    <li>Se corrige un error que podía mostrar incorrectamente la <b>hora local</b>.</li>
                    <li>Se corrige la adaptación visual de <b>tablas, gráficos y controles del mapa</b> al tema activo.</li>
                  </ul>
                </div>
              </details>
            </span>
          </div>
          <div class="mlb-footer-bottom">Fuentes: WU · AEMET · Meteocat · Euskalmet · Frost · Meteo-France · MeteoGalicia · NWS · POEM · No afiliado · © 2026</div>
        </div>
        """
    ),
    unsafe_allow_html=True
)

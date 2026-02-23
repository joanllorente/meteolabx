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
import inspect
import html
from typing import Optional
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES, LS_AUTOCONNECT
from utils import html_clean, is_nan, es_datetime_from_epoch, age_string, fmt_hpa
from utils.storage import (
    set_local_storage,
    set_stored_autoconnect_target,
    get_stored_autoconnect,
    get_stored_autoconnect_target,
)
from api import WuError, fetch_wu_current_session_cached, fetch_daily_timeseries, fetch_hourly_7day_session_cached
from models import (
    e_s, vapor_pressure, dewpoint_from_vapor_pressure,
    mixing_ratio, specific_humidity, absolute_humidity,
    potential_temperature, virtual_temperature, equivalent_temperature, equivalent_potential_temperature,
    wet_bulb_celsius, msl_to_absolute, air_density, lcl_height,
    sky_clarity_label, uv_index_label, water_balance, water_balance_label
)
from services import (
    rain_rates_from_total, rain_intensity_label,
    init_pressure_history, push_pressure, pressure_trend_3h
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, render_sidebar
)

# Imports de AEMET
from services.aemet import (
    get_aemet_data,
    is_aemet_connection,
    get_aemet_daily_charts,
    fetch_aemet_all24h_station_series,
    fetch_aemet_hourly_7day_series,
)
from services.meteocat import (
    get_meteocat_data,
    is_meteocat_connection,
    fetch_meteocat_station_day,
    extract_meteocat_daily_timeseries,
)
from services.euskalmet import (
    get_euskalmet_data,
    is_euskalmet_connection,
)
from services.meteogalicia import (
    get_meteogalicia_data,
    is_meteogalicia_connection,
)
from services.nws import (
    get_nws_data,
    is_nws_connection,
)
from components.station_selector import render_station_selector
from components.browser_geolocation import get_browser_geolocation
from providers import search_nearby_stations

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _plotly_chart_stretch(fig, key: str, config: Optional[dict] = None):
    """Renderiza Plotly con compatibilidad entre APIs antiguas/nuevas de Streamlit."""
    cfg = config if isinstance(config, dict) else {}
    params = inspect.signature(st.plotly_chart).parameters
    if "width" in params:
        st.plotly_chart(fig, width="stretch", key=key, config=cfg)
    else:
        st.plotly_chart(fig, use_container_width=True, key=key, config=cfg)


def _pydeck_chart_stretch(deck, key: str, height: int = 900):
    """Renderiza pydeck de forma compatible entre versiones de Streamlit."""
    params = inspect.signature(st.pydeck_chart).parameters
    kwargs = {"height": int(height), "key": key}
    if "on_select" in params:
        kwargs["on_select"] = "rerun"
    if "selection_mode" in params:
        kwargs["selection_mode"] = "single-object"

    if "use_container_width" in params:
        return st.pydeck_chart(deck, use_container_width=True, **kwargs)
    if "width" in params:
        return st.pydeck_chart(deck, width=1200, **kwargs)
    return st.pydeck_chart(deck, **kwargs)


# ============================================================
# SIDEBAR Y TEMA
# ============================================================

theme_mode, dark = render_sidebar()

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

st.markdown(f"""
<style>
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

/* Radios del tema: forzar colores para que cambien al alternar claro/oscuro */
[data-testid="stSidebar"] input[type="radio"] {{
    accent-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] input[type="checkbox"] {{
    accent-color: #ff4b4b !important;
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
</style>
""", unsafe_allow_html=True)


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

[data-testid="stMainBlockContainer"] [data-baseweb="popover"] [role="listbox"] {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
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
<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png?v=3">
<meta name="theme-color" content="#2384ff">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png?v=3">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png?v=3">

<script>
(function () {
  const doc = window.parent?.document || document;
  const head = doc.head || document.head;
  if (!head) return;

  function upsertLink(rel, href, sizes) {
    let el = head.querySelector(`link[rel="${rel}"]${sizes ? `[sizes="${sizes}"]` : ""}`);
    if (!el) {
      el = doc.createElement("link");
      el.setAttribute("rel", rel);
      if (sizes) el.setAttribute("sizes", sizes);
      head.appendChild(el);
    }
    el.setAttribute("href", href);
  }

  upsertLink("apple-touch-icon", "/static/apple-touch-icon.png?v=3", "180x180");
  upsertLink("icon", "/favicon-32x32.png?v=3", "32x32");
  upsertLink("icon", "/favicon-16x16.png?v=3", "16x16");
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

  .icon-img{
    width: 54px;
    height: 54px;
    display:block;
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
        "METEOGALICIA": 600,  # MeteoGalicia ofrece estado y serie horaria reciente
        "NWS": 600,  # NWS suele actualizar en intervalo subhorario según estación
        "WU": REFRESH_SECONDS,
    }
    return int(defaults.get(provider_id, REFRESH_SECONDS))


def _disconnect_active_station() -> None:
    """Desconecta la estación activa (mismo criterio que el botón del sidebar)."""
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
            or state_key.startswith("meteogalicia_")
            or state_key.startswith("nws_")
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


header_refresh_seconds = _provider_refresh_seconds() if st.session_state.get("connected", False) else REFRESH_SECONDS
header_refresh_label = (
    f"{header_refresh_seconds // 60} min"
    if header_refresh_seconds % 60 == 0 and header_refresh_seconds >= 60
    else f"{header_refresh_seconds}s"
)

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 7</span></h1>
      <div class="meta">
        Versión beta — la interfaz y las funciones pueden cambiar ·
        Tema: {"Oscuro" if dark else "Claro"} · Refresh: {header_refresh_label}
      </div>
    </div>
    <div class="header-sub station-count">37000 estaciones disponibles</div>
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
    if is_aemet_connection():
        # ========== DATOS DE AEMET ==========
        
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
        ) = get_aemet_daily_charts()
        has_chart_data = len(chart_epochs) > 0
        
        print(f"🔍 [DEBUG] get_aemet_daily_charts() devolvió: {len(chart_epochs)} epochs")
        print(f"🔍 [DEBUG] has_chart_data = {has_chart_data}")
        
        # Obtener dato actual del endpoint normal (puede ser antiguo)
        base = get_aemet_data()
        
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de AEMET por ahora. Intenta de nuevo en unos minutos.")
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
            st.session_state["chart_pressures"] = chart_pressures
            st.session_state["chart_winds"] = chart_winds
            st.session_state["chart_gusts"] = chart_gusts
            st.session_state["chart_wind_dirs"] = chart_wind_dirs
        else:
            print(f"⚠️ [DEBUG] No hay datos de gráficos - usando datos del endpoint normal")
            # Evitar extremos desfasados cuando no hay serie diezminutal válida
            base["temp_max"] = None
            base["temp_min"] = None
            base["gust_max"] = None
        
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
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])
            
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
        
        # Radiación (no disponible en AEMET)
        solar_rad = float("nan")
        uv = float("nan")
        et0 = float("nan")
        clarity = float("nan")
        balance = float("nan")
        has_radiation = False

    elif is_euskalmet_connection():
        # ========== DATOS DE EUSKALMET ==========
        base = get_euskalmet_data()
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

        st.session_state["last_update_time"] = time.time()
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))

        z = base.get("elevation", st.session_state.get("euskalmet_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "EUSKALMET"

        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(f"⚠️ Datos de Euskalmet con {data_age_minutes:.0f} minutos de antigüedad.")

        # Lluvia: acumulada diaria basada en serie de precipitación.
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
        inst_label = rain_intensity_label(inst_mm_h)

        p_abs = float(base.get("p_abs_hpa", float("nan")))
        p_msl = float(base.get("p_hpa", float("nan")))
        provider_for_pressure = st.session_state.get("connection_type", "EUSKALMET")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

        if not is_nan(p_abs):
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if not is_nan(p_msl):
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago"),
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"

        # Termodinámica
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

        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])
            base["Td"] = Td_calc
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

        solar_rad = base.get("solar_radiation", float("nan"))
        uv = base.get("uv", float("nan"))
        has_radiation = not is_nan(solar_rad) or not is_nan(uv)

        if has_radiation:
            from models.radiation import penman_monteith_et0, sky_clarity_index
            lat = base.get("lat", float("nan"))
            lon = base.get("lon", float("nan"))
            wind_speed = base.get("wind", 2.0)
            if is_nan(wind_speed):
                wind_speed = 2.0
            if wind_speed < 0.1:
                wind_speed = 0.1
            et0 = penman_monteith_et0(
                solar_rad,
                base["Tc"],
                base["RH"],
                wind_speed,
                lat,
                z,
                base["epoch"],
            )
            clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)
            balance = water_balance(base["precip_total"], et0)
        else:
            et0 = float("nan")
            clarity = float("nan")
            balance = float("nan")

        # Series para gráficos desde el servicio Euskalmet.
        series = base.get("_series", {}) if isinstance(base.get("_series"), dict) else {}
        chart_epochs = series.get("epochs", [])
        chart_temps = series.get("temps", [])
        chart_humidities = series.get("humidities", [])
        chart_pressures = series.get("pressures_abs", [])
        chart_winds = series.get("winds", [])
        chart_gusts = series.get("gusts", [])
        chart_wind_dirs = series.get("wind_dirs", [])
        chart_solar_radiations = series.get("solar_radiations", [])
        chart_dewpts = []
        has_chart_data = series.get("has_data", False)

        # Tendencia de presión 3h desde serie (absoluta -> msl).
        if has_chart_data and len(chart_epochs) == len(chart_pressures):
            press_valid = []
            for ep, p_abs_series in zip(chart_epochs, chart_pressures):
                if not is_nan(p_abs_series):
                    press_valid.append((int(ep), float(p_abs_series)))
            if len(press_valid) >= 2:
                press_valid.sort(key=lambda x: x[0])
                ep_now, p_abs_now = press_valid[-1]
                target_ep = ep_now - (3 * 3600)
                ep_3h, p_abs_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))
                import math
                msl_factor = math.exp(z / 8000.0)
                p_now_msl = p_abs_now * msl_factor
                p_3h_msl = p_abs_3h * msl_factor
                dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                    p_now=p_now_msl,
                    epoch_now=ep_now,
                    p_3h_ago=p_3h_msl,
                    epoch_3h_ago=ep_3h,
                )

        st.session_state["chart_epochs"] = chart_epochs
        st.session_state["chart_temps"] = chart_temps
        st.session_state["chart_humidities"] = chart_humidities
        st.session_state["chart_dewpts"] = chart_dewpts
        st.session_state["chart_pressures"] = chart_pressures
        st.session_state["chart_solar_radiations"] = chart_solar_radiations
        st.session_state["chart_winds"] = chart_winds
        st.session_state["chart_gusts"] = chart_gusts
        st.session_state["chart_wind_dirs"] = chart_wind_dirs
        st.session_state["has_chart_data"] = has_chart_data

    elif is_meteocat_connection():
        # ========== DATOS DE METEOCAT ==========
        base = get_meteocat_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de Meteocat por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        # Guardar timestamp de última actualización exitosa
        st.session_state["last_update_time"] = time.time()

        # Guardar latitud y longitud para cálculos de radiación
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))

        # Altitud Meteocat (catálogo de estación)
        z = base.get("elevation", st.session_state.get("meteocat_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "METEOCAT"

        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(f"⚠️ Datos de Meteocat con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
            logger.warning(f"Datos Meteocat antiguos: {data_age_minutes:.1f} minutos")

        # Lluvia
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
        inst_label = rain_intensity_label(inst_mm_h)

        # Presión Meteocat: 34 se trata como absoluta (estación).
        p_abs = float(base.get("p_abs_hpa", float("nan")))
        p_msl = float(base.get("p_hpa", float("nan")))

        provider_for_pressure = st.session_state.get("connection_type", "METEOCAT")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

        if not is_nan(p_abs):
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if not is_nan(p_msl):
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago"),
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"

        # Termodinámica
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

        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])
            base["Td"] = Td_calc

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

        # Radiación
        solar_rad = base.get("solar_radiation", float("nan"))
        uv = base.get("uv", float("nan"))
        has_radiation = not is_nan(solar_rad) or not is_nan(uv)

        if has_radiation:
            from models.radiation import penman_monteith_et0, sky_clarity_index

            lat = base.get("lat", float("nan"))
            lon = base.get("lon", float("nan"))
            wind_speed = base.get("wind", 2.0)
            if is_nan(wind_speed):
                wind_speed = 2.0
            if wind_speed < 0.1:
                wind_speed = 0.1

            et0 = penman_monteith_et0(
                solar_rad,
                base["Tc"],
                base["RH"],
                wind_speed,
                lat,
                z,
                base["epoch"],
            )
            clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)
            balance = water_balance(base["precip_total"], et0)
        else:
            et0 = float("nan")
            clarity = float("nan")
            balance = float("nan")

        # Serie del día para gráficos y derivados.
        station_code = str(base.get("station_code", "")).strip()
        if station_code:
            now_local_cat = datetime.now()
            day_payload = fetch_meteocat_station_day(
                station_code,
                now_local_cat.year,
                now_local_cat.month,
                now_local_cat.day,
            )
            if day_payload.get("ok"):
                ts = extract_meteocat_daily_timeseries(day_payload.get("variables", {}))
                chart_epochs = ts.get("epochs", [])
                chart_temps = ts.get("temps", [])
                chart_humidities = ts.get("humidities", [])
                chart_pressures = ts.get("pressures_abs", [])  # Meteocat 34: absoluta
                chart_winds = ts.get("winds", [])
                chart_gusts = ts.get("gusts", [])
                chart_wind_dirs = ts.get("wind_dirs", [])
                chart_solar_radiations = ts.get("solar_radiations", [])
                chart_dewpts = []
                has_chart_data = ts.get("has_data", False)
            else:
                chart_epochs = []
                chart_temps = []
                chart_humidities = []
                chart_dewpts = []
                chart_pressures = []
                chart_solar_radiations = []
                chart_winds = []
                chart_gusts = []
                chart_wind_dirs = []
                has_chart_data = False
        else:
            chart_epochs = []
            chart_temps = []
            chart_humidities = []
            chart_dewpts = []
            chart_pressures = []
            chart_solar_radiations = []
            chart_winds = []
            chart_gusts = []
            chart_wind_dirs = []
            has_chart_data = False

        # Tendencia de presión 3h usando serie diaria Meteocat (34 = presión absoluta).
        if has_chart_data and len(chart_epochs) == len(chart_pressures):
            press_valid = []
            for ep, p_abs_series in zip(chart_epochs, chart_pressures):
                if not is_nan(p_abs_series):
                    press_valid.append((int(ep), float(p_abs_series)))

            if len(press_valid) >= 2:
                press_valid.sort(key=lambda x: x[0])
                ep_now, p_abs_now = press_valid[-1]
                target_ep = ep_now - (3 * 3600)
                ep_3h, p_abs_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))

                # Pasar a MSL con el mismo factor para mantener coherencia entre puntos.
                import math
                msl_factor = math.exp(z / 8000.0)
                p_now_msl = p_abs_now * msl_factor
                p_3h_msl = p_abs_3h * msl_factor

                dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                    p_now=p_now_msl,
                    epoch_now=ep_now,
                    p_3h_ago=p_3h_msl,
                    epoch_3h_ago=ep_3h,
                )

                logger.info(
                    "[METEOCAT] Tendencia presión 3h desde serie diaria: "
                    f"t_now={ep_now}, t_old={ep_3h}, p_now={p_now_msl:.2f}, p_old={p_3h_msl:.2f}"
                )

        st.session_state["chart_epochs"] = chart_epochs
        st.session_state["chart_temps"] = chart_temps
        st.session_state["chart_humidities"] = chart_humidities
        st.session_state["chart_dewpts"] = chart_dewpts
        st.session_state["chart_pressures"] = chart_pressures
        st.session_state["chart_solar_radiations"] = chart_solar_radiations
        st.session_state["chart_winds"] = chart_winds
        st.session_state["chart_gusts"] = chart_gusts
        st.session_state["chart_wind_dirs"] = chart_wind_dirs
        st.session_state["has_chart_data"] = has_chart_data

    elif is_meteogalicia_connection():
        # ========== DATOS DE METEOGALICIA ==========
        base = get_meteogalicia_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de MeteoGalicia por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        st.session_state["last_update_time"] = time.time()
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))

        z = base.get("elevation", st.session_state.get("meteogalicia_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "METEOGALICIA"

        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(
                f"⚠️ Datos de MeteoGalicia con {data_age_minutes:.0f} minutos de antigüedad. "
                "La estación puede no estar reportando."
            )
            logger.warning(f"Datos MeteoGalicia antiguos: {data_age_minutes:.1f} minutos")

        # Lluvia
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
        inst_label = rain_intensity_label(inst_mm_h)

        # Presion (serie horaria usada como referencia de estacion)
        p_abs = float(base.get("p_abs_hpa", float("nan")))
        p_msl = float(base.get("p_hpa", float("nan")))
        provider_for_pressure = st.session_state.get("connection_type", "METEOGALICIA")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

        if not is_nan(p_abs):
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if not is_nan(p_msl):
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago"),
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"

        # Termodinamica
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

        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])
            base["Td"] = Td_calc

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

        # Radiacion (si hay sensores en la estacion)
        solar_rad = base.get("solar_radiation", float("nan"))
        uv = base.get("uv", float("nan"))
        has_radiation = not is_nan(solar_rad) or not is_nan(uv)

        if has_radiation:
            from models.radiation import penman_monteith_et0, sky_clarity_index

            lat = base.get("lat", float("nan"))
            lon = base.get("lon", float("nan"))
            wind_speed = base.get("wind", 2.0)
            if is_nan(wind_speed):
                wind_speed = 2.0
            if wind_speed < 0.1:
                wind_speed = 0.1

            et0 = penman_monteith_et0(
                solar_rad,
                base["Tc"],
                base["RH"],
                wind_speed,
                lat,
                z,
                base["epoch"],
            )
            clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)
            balance = water_balance(base["precip_total"], et0)
        else:
            et0 = float("nan")
            clarity = float("nan")
            balance = float("nan")

        # Series para graficos desde el servicio MeteoGalicia.
        series = base.get("_series", {}) if isinstance(base.get("_series"), dict) else {}
        chart_epochs = series.get("epochs", [])
        chart_temps = series.get("temps", [])
        chart_humidities = series.get("humidities", [])
        chart_pressures = series.get("pressures_abs", [])
        chart_winds = series.get("winds", [])
        chart_gusts = series.get("gusts", [])
        chart_wind_dirs = series.get("wind_dirs", [])
        chart_solar_radiations = series.get("solar_radiations", [])
        chart_dewpts = []
        has_chart_data = series.get("has_data", False)

        st.session_state["chart_epochs"] = chart_epochs
        st.session_state["chart_temps"] = chart_temps
        st.session_state["chart_humidities"] = chart_humidities
        st.session_state["chart_dewpts"] = chart_dewpts
        st.session_state["chart_pressures"] = chart_pressures
        st.session_state["chart_solar_radiations"] = chart_solar_radiations
        st.session_state["chart_winds"] = chart_winds
        st.session_state["chart_gusts"] = chart_gusts
        st.session_state["chart_wind_dirs"] = chart_wind_dirs
        st.session_state["has_chart_data"] = has_chart_data

        # Reusar la serie horaria para tendencia sinonptica generica.
        st.session_state["trend_hourly_epochs"] = chart_epochs
        st.session_state["trend_hourly_temps"] = chart_temps
        st.session_state["trend_hourly_humidities"] = chart_humidities
        st.session_state["trend_hourly_pressures"] = chart_pressures

    elif is_nws_connection():
        # ========== DATOS DE NWS ==========
        base = get_nws_data()
        if base is None:
            st.warning("⚠️ No se pudieron obtener datos de NWS por ahora. Intenta de nuevo en unos minutos.")
            st.stop()

        st.session_state["last_update_time"] = time.time()
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))

        z = base.get("elevation", st.session_state.get("nws_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "NWS"

        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(
                f"⚠️ Datos de NWS con {data_age_minutes:.0f} minutos de antigüedad. "
                "La estación puede no estar reportando."
            )
            logger.warning(f"Datos NWS antiguos: {data_age_minutes:.1f} minutos")

        # Lluvia
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
        inst_label = rain_intensity_label(inst_mm_h)

        # Presion
        p_abs = float(base.get("p_abs_hpa", float("nan")))
        p_msl = float(base.get("p_hpa", float("nan")))
        provider_for_pressure = st.session_state.get("connection_type", "NWS")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

        if not is_nan(p_abs):
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if not is_nan(p_msl):
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago"),
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"

        # Termodinamica
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

        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])
            base["Td"] = Td_calc

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

        # Radiacion (NWS observaciones no exponen este campo en este panel)
        solar_rad = float("nan")
        uv = float("nan")
        has_radiation = False
        et0 = float("nan")
        clarity = float("nan")
        balance = float("nan")

        # Series para graficos desde el servicio NWS.
        series = base.get("_series", {}) if isinstance(base.get("_series"), dict) else {}
        chart_epochs = series.get("epochs", [])
        chart_temps = series.get("temps", [])
        chart_humidities = series.get("humidities", [])
        chart_pressures = series.get("pressures_abs", [])
        chart_winds = series.get("winds", [])
        chart_gusts = series.get("gusts", [])
        chart_wind_dirs = series.get("wind_dirs", [])
        chart_solar_radiations = series.get("solar_radiations", [])
        chart_dewpts = []
        has_chart_data = series.get("has_data", False)

        st.session_state["chart_epochs"] = chart_epochs
        st.session_state["chart_temps"] = chart_temps
        st.session_state["chart_humidities"] = chart_humidities
        st.session_state["chart_dewpts"] = chart_dewpts
        st.session_state["chart_pressures"] = chart_pressures
        st.session_state["chart_solar_radiations"] = chart_solar_radiations
        st.session_state["chart_winds"] = chart_winds
        st.session_state["chart_gusts"] = chart_gusts
        st.session_state["chart_wind_dirs"] = chart_wind_dirs
        st.session_state["has_chart_data"] = has_chart_data

        # Serie semanal para tendencia sinoptica.
        trend_series = base.get("_series_7d", {}) if isinstance(base.get("_series_7d"), dict) else {}
        if trend_series.get("has_data"):
            st.session_state["trend_hourly_epochs"] = trend_series.get("epochs", [])
            st.session_state["trend_hourly_temps"] = trend_series.get("temps", [])
            st.session_state["trend_hourly_humidities"] = trend_series.get("humidities", [])
            st.session_state["trend_hourly_pressures"] = trend_series.get("pressures_abs", [])
        else:
            st.session_state["trend_hourly_epochs"] = chart_epochs
            st.session_state["trend_hourly_temps"] = chart_temps
            st.session_state["trend_hourly_humidities"] = chart_humidities
            st.session_state["trend_hourly_pressures"] = chart_pressures

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
            # Obtener datos de WU (con cache)
            base = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)

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
            Tw = wet_bulb_celsius(base["Tc"], base["RH"])  # Bulbo húmedo (Stull)
            rho = air_density(p_abs, Tv)  # Densidad del aire
            rho_v_gm3 = absolute_humidity(e, base["Tc"])  # Humedad absoluta
            lcl = lcl_height(base["Tc"], Td_calc)  # Altura LCL

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
                from models.radiation import sky_clarity_index
                lon = base.get("lon", float("nan"))
                clarity = sky_clarity_index(solar_rad, lat, z, now_ts, lon)

                # ET0 y balance mostrados en UI se recalculan como acumulado "hoy"
                # usando la serie /all/1day tras cargar los puntos temporales.
                et0 = float("nan")
                balance = float("nan")

                # Logging seguro (manejar NaN)
                solar_str = f"{solar_rad:.0f}" if not is_nan(solar_rad) else "N/A"
                uv_str = f"{uv:.1f}" if not is_nan(uv) else "N/A"

                logger.info(f"   Radiación: Solar={solar_str} W/m², UV={uv_str}")


            # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
            timeseries = fetch_daily_timeseries(station_id, api_key)
            chart_epochs = timeseries.get("epochs", [])
            chart_temps = timeseries.get("temps", [])
            chart_humidities = timeseries.get("humidities", [])
            chart_dewpts = timeseries.get("dewpts", [])
            chart_pressures = timeseries.get("pressures", [])
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

# Mostrar metadata si está conectado (común para AEMET y WU)
if connected:
    station_tz_name = str(
        base.get("station_tz", st.session_state.get("provider_station_tz", ""))
    ).strip()
    station_time_txt = ""
    if station_tz_name:
        try:
            from zoneinfo import ZoneInfo

            dt_station = datetime.fromtimestamp(int(base["epoch"]), ZoneInfo(station_tz_name))
            station_time_txt = f" · Hora huso estación ({station_tz_name}): {dt_station.strftime('%d-%m-%Y %H:%M:%S')}"
        except Exception:
            station_time_txt = ""

    st.markdown(
        html_clean(
            f"<div class='meta'>Último dato (local): {es_datetime_from_epoch(base['epoch'])}{station_time_txt} · Edad: {age_string(base['epoch'])}</div>"
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

tab_options = ["Observación", "Tendencias", "Climogramas", "Divulgación", "Mapa"]

# Aplicar navegación diferida ANTES de instanciar el widget de tabs.
pending_tab = st.session_state.get("_pending_active_tab")
if isinstance(pending_tab, str) and pending_tab in tab_options:
    st.session_state["active_tab"] = pending_tab
if "_pending_active_tab" in st.session_state:
    del st.session_state["_pending_active_tab"]

# Inicializar tab activo una sola vez y dejar que el propio widget
# gestione su estado en reruns (evita casos de "doble clic" al cambiar pestaña).
if st.session_state.get("active_tab") not in tab_options:
    st.session_state["active_tab"] = tab_options[0]

# Radio buttons estilizados como tabs con underline
active_tab = st.radio(
    "Navegación",
    tab_options,
    horizontal=True,
    key="active_tab",
    label_visibility="collapsed"
)

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# TAB 1: OBSERVACIÓN
if active_tab == "Observación":
    section_title("Observados")

    def invalid_num(x):
        return x is None or is_nan(x)

    # Preparar valores
    temp_val = "—" if invalid_num(base.get("Tc")) else f"{base['Tc']:.1f}"
    rh_val = "—" if invalid_num(base.get("RH")) else f"{base['RH']:.0f}"
    td_val = "—" if invalid_num(base.get("Td")) else f"{base['Td']:.1f}"
    wind_val = "—" if invalid_num(base.get("wind")) else f"{base['wind']:.1f}"
    precip_total_str = "—" if invalid_num(base.get("precip_total")) else f"{base['precip_total']:.1f}"
    p_abs_str = str(p_abs_disp)

    # Viento
    deg = base["wind_dir_deg"]
    wind = base["wind"]
    if invalid_num(wind) or wind == 0.0 or invalid_num(deg):
        wind_dir_str = "—"
    else:
        short = wind_dir_text(deg)
        wind_dir_str = f"{short} ({deg:.0f}°)"

    gust_str = "—" if invalid_num(base.get("gust")) else f"{base['gust']:.1f}"

    # Lluvia
    def fmt_rate(x):
        from utils import is_nan as check_nan
        return "—" if check_nan(x) else f"{x:.1f} mm/h"

    # Temperatura
    fl_str = "—" if is_nan(base["feels_like"]) else f"{base['feels_like']:.1f} °C"
    hi_str = "—" if is_nan(base["heat_index"]) else f"{base['heat_index']:.1f} °C"

    # Rocío
    try:
        e_vapor_val = float(e)
    except Exception:
        e_vapor_val = float("nan")
    e_vapor_str = "—" if is_nan(e_vapor_val) else f"{e_vapor_val:.1f}"

    # Extremos
    temp_side = ""
    tmax = base.get("temp_max")
    tmin = base.get("temp_min")
    if tmax is not None and tmin is not None and not is_nan(tmax) and not is_nan(tmin):
        temp_side = f"<div class='max'>▲ {tmax:.1f}</div><div class='min'>▼ {tmin:.1f}</div>"

    rh_side = ""
    rhmax = base.get("rh_max")
    rhmin = base.get("rh_min")
    if rhmax is not None and rhmin is not None and not is_nan(rhmax) and not is_nan(rhmin):
        rh_side = f"<div class='max'>▲ {rhmax:.0f}</div><div class='min'>▼ {rhmin:.0f}</div>"

    wind_side = ""
    gmax = base.get("gust_max")
    if gmax is not None and not is_nan(gmax):
        wind_side = f"<div class='max'>▲ {gmax:.1f}</div>"

    # Usar la función card() pero asegurarnos de que se renderice correctamente
    from components.icons import icon_img

    cards_basic = [
        card("Temperatura", temp_val, "°C", 
             icon_kind="temp", 
             subtitle_html=f"<div>Feels like: <b>{fl_str}</b></div><div>Heat index: <b>{hi_str}</b></div>", 
             side_html=temp_side, 
             uid="b1", dark=dark),
        card("Humedad relativa", rh_val, "%", 
             icon_kind="rh", 
             side_html=rh_side, 
             uid="b2", dark=dark),
        card("Punto de rocío", td_val, "°C", 
             icon_kind="dew", 
             subtitle_html=f"<div>Presión de vapor: <b>{e_vapor_str} hPa</b></div>", 
             uid="b3", dark=dark),
        card("Presión", p_abs_str, "hPa", 
             icon_kind="press", 
             subtitle_html=f"<div>Tendencia: <b>{p_arrow} {p_label}</b></div><div>Δ3h: <b>{fmt_hpa(dp3, 1)} hPa</b></div><div>MSL: <b>{p_msl_disp} hPa</b></div>", 
             uid="b4", dark=dark),
        card("Viento", wind_val, "km/h", 
             icon_kind="wind", 
             subtitle_html=f"<div>Racha: <b>{gust_str}</b></div><div>Dirección: <b>{wind_dir_str}</b></div>", 
             side_html=wind_side, 
             uid="b5", dark=dark),
        card("Precipitación hoy", precip_total_str, "mm", 
             icon_kind="rain", 
             subtitle_html=f"<div>Instantánea: <b>{fmt_rate(inst_mm_h)}</b></div><div style='font-size:0.9rem; opacity:0.85;'>{inst_label}</div><div style='margin-top:6px; font-size:0.8rem; opacity:0.6;'>1 min: {fmt_rate(r1_mm_h)} · 5 min: {fmt_rate(r5_mm_h)}</div>", 
             uid="b6", dark=dark),
    ]
    render_grid(cards_basic, cols=3, extra_class="grid-basic")

    # ============================================================
    # NIVEL 2 — TERMODINÁMICA
    # ============================================================
    section_title("Termodinámica")

    q_val = "—" if is_nan(q_gkg) else f"{q_gkg:.2f}"
    rho_v_val = "—" if is_nan(rho_v_gm3) else f"{rho_v_gm3:.1f}"
    tw_val = "—" if is_nan(Tw) else f"{Tw:.1f}"
    tv_val = "—" if is_nan(Tv) else f"{Tv:.1f}"
    te_val = "—" if is_nan(Te) else f"{Te:.1f}"
    theta_val = "—" if is_nan(theta) else f"{theta:.1f}"
    rho_val = "—" if is_nan(rho) else f"{rho:.3f}"
    lcl_val = "—" if is_nan(lcl) else f"{lcl:.0f}"

    cards_derived = [
        card("Humedad específica", q_val, "g/kg", icon_kind="rh", uid="d1", dark=dark),
        card("Humedad absoluta", rho_v_val, "g/m³", icon_kind="dew", uid="d7a", dark=dark),
        card("Temp. bulbo húmedo", tw_val, "°C", icon_kind="dew", uid="tw", dark=dark),
        card("Temp. virtual", tv_val, "°C", icon_kind="wind", uid="d3", dark=dark),
        card("Temp. equivalente", te_val, "°C", icon_kind="rain", uid="d4", dark=dark),
        card("Temp. potencial", theta_val, "°C", icon_kind="temp", uid="d2", dark=dark),
        card("Densidad del aire", rho_val, "kg/m³", icon_kind="press", uid="d5", dark=dark),
        card("Base nube LCL", lcl_val, "m", icon_kind="dew", uid="d6", dark=dark),
    ]
    render_grid(cards_derived, cols=4)

    # ============================================================
    # NIVEL 3 — RADIACIÓN (solo si la estación tiene sensores)
    # ============================================================

    # Mostrar sección solo si no está conectado (modo demo) O si tiene sensores de radiación
    if not connected or has_radiation:
        section_title("Radiación")

        def _solar_energy_today_wh_m2() -> float:
            import math
            epochs = st.session_state.get("chart_epochs", [])
            solars = st.session_state.get("chart_solar_radiations", [])
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

        # Formatear valores
        solar_val = "—" if is_nan(solar_rad) else f"{solar_rad:.0f}"
        uv_val = "—" if is_nan(uv) else f"{uv:.1f}"
        et0_val = "—" if is_nan(et0) else f"{et0:.2f}"
        clarity_val = "—" if is_nan(clarity) else f"{clarity * 100:.0f}"
        balance_val = "—" if is_nan(balance) else f"{balance:.1f}"
        energy_today_wh_m2 = _solar_energy_today_wh_m2()
        if is_nan(energy_today_wh_m2):
            energy_today_txt = "—"
        else:
            energy_today_mj_m2 = energy_today_wh_m2 * 0.0036
            energy_today_txt = f"{energy_today_mj_m2:.2f} MJ/m²"
        solar_sub = f"<div>Energía hoy: <b>{energy_today_txt}</b></div>"

        # Subtítulos
        erythema_mw_m2 = float("nan") if is_nan(uv) else (25.0 * uv)
        erythema_txt = "—" if is_nan(erythema_mw_m2) else f"{erythema_mw_m2:.1f} mW/m²"
        uv_sub = f"<div>Irradiancia eritematosa: <b>{erythema_txt}</b></div>"

        et0_sub = "<div style='font-size:0.8rem; opacity:0.65; margin-top:2px;'>FAO-56 Penman-Monteith</div>"

        from models.radiation import is_nighttime, sunrise_sunset_label

        clarity_label = sky_clarity_label(clarity)
        try:
            lat_for_clarity = base.get("lat", float("nan"))
            lon_for_clarity = base.get("lon", float("nan"))
            epoch_for_clarity = base.get("epoch", 0) if connected else int(time.time())
            if epoch_for_clarity and not is_nan(lat_for_clarity) and is_nighttime(float(lat_for_clarity), float(epoch_for_clarity), float(lon_for_clarity)):
                clarity_label = "Noche"
        except Exception:
            pass
        try:
            epoch_for_clarity = base.get("epoch", 0) if connected else int(time.time())
            lat_for_clarity = base.get("lat", float("nan"))
            lon_for_clarity = base.get("lon", float("nan"))
            orto_ocaso_txt = sunrise_sunset_label(float(lat_for_clarity), float(lon_for_clarity), float(epoch_for_clarity))
        except Exception:
            orto_ocaso_txt = "Orto — · Ocaso —"
        clarity_sub = (
            f"<div style='font-size:0.85rem; opacity:0.75;'>{clarity_label}</div>"
            f"<div>{orto_ocaso_txt}</div>"
        )

        balance_label = water_balance_label(balance)
        balance_sub = f"<div style='font-size:0.85rem; opacity:0.75; margin-top:2px;'>{balance_label}</div>"

        # Grid de 4 columnas (como termodinámica)
        # Primera fila: Solar, UV, ET0, Clarity
        cards_radiation_row1 = [
            card("Radiación solar", solar_val, "W/m²", icon_kind="solar", subtitle_html=solar_sub, uid="r1", dark=dark),
            card("Índice UV", uv_val, "", icon_kind="uv", subtitle_html=uv_sub, uid="r2", dark=dark),
            card("Evapotranspiración hoy", et0_val, "mm", icon_kind="et0", subtitle_html=et0_sub, uid="r3", dark=dark),
            card("Claridad del cielo", clarity_val, "%", icon_kind="clarity", subtitle_html=clarity_sub, uid="r4", dark=dark),
        ]
        render_grid(cards_radiation_row1, cols=4)

        # Segunda fila: Balance (con espacio superior)
        cards_radiation_row2 = [
            card("Balance hídrico hoy", balance_val, "mm", icon_kind="balance", subtitle_html=balance_sub, uid="r5", dark=dark),
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
        section_title("Gráficos")

        from datetime import datetime, timedelta
        import pandas as pd
        import plotly.graph_objects as go
        
        # Obtener datos de gráficos del session_state
        chart_epochs = st.session_state.get("chart_epochs", [])
        chart_temps = st.session_state.get("chart_temps", [])
        chart_humidities = st.session_state.get("chart_humidities", [])
        chart_pressures = st.session_state.get("chart_pressures", [])
        chart_winds = st.session_state.get("chart_winds", [])
        
        print(f"🔍 [DEBUG Gráficos] Obtenidos del session_state:")
        print(f"   - chart_epochs: {len(chart_epochs)} elementos")
        print(f"   - chart_temps: {len(chart_temps)} elementos")  
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
        step_minutes = 5
        df_obs["dt"] = pd.to_datetime(df_obs["dt"]).dt.floor(f"{step_minutes}min")

        # Si hay duplicados (varios puntos en el mismo tick), nos quedamos con el último
        df_obs = df_obs.groupby("dt", as_index=False)["temp"].last().sort_values("dt")
        print(f"🔍 [DEBUG] Después de groupby: {len(df_obs)} filas")

        # --- 2) Crear malla completa con rango específico por proveedor
        now_local = datetime.now()
        connection_type = st.session_state.get("connection_type", "")

        grid_inclusive = "both"

        if connection_type == "AEMET":
            # AEMET: mantener comportamiento actual (rango real devuelto por la API)
            if len(df_obs) > 0:
                data_start = df_obs["dt"].min()
                data_end = df_obs["dt"].max()
                print(f"🔍 [DEBUG] [AEMET] Rango de datos: {data_start} → {data_end}")
            else:
                data_end = now_local
                data_start = data_end - timedelta(hours=24)
                print("⚠️ [DEBUG] [AEMET] Sin datos - fallback a últimas 24h")
        else:
            # WU: HOY completo (00:00-23:59), aunque aún no existan datos en horas futuras.
            # Esto mantiene el marco fijo del día y evita que el gráfico se "monte" con el tiempo.
            day_start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            data_start = day_start_today
            data_end = day_start_today + timedelta(days=1)
            grid_inclusive = "left"  # no incluir 24:00 del día siguiente
            print(f"🔍 [DEBUG] [WU] Ventana HOY: {data_start} → {data_end} (left-inclusive)")

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
        print(f"🔍 [DEBUG] Serie reindexada: {len(y)} puntos, {y.notna().sum()} válidos")

        # --- 4) Rango Y con padding inteligente
        y_valid = y.dropna()
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
        st.markdown("### Temperatura")
        
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
            y=y.values,              # <- pasar valores explícitos evita rarezas
            mode="lines",
            name="Temperatura",
            line=dict(color="rgb(255, 107, 107)", width=3),
            connectgaps=True,  # ✅ Conectar a través de NaN (importante para datos AEMET cada 10 min)
            fill="tozeroy",
            fillcolor="rgba(255, 107, 107, 0.1)"
        ))
        
        print(f"✅ [DEBUG] Gráfico creado - trazas: {len(fig.data)}")

        fig.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)

        fig.update_layout(
            title=dict(
                text=("Temperatura de Hoy" if connection_type != "AEMET" else "Temperatura del Día"),
                x=0.5,
                xanchor="center",
                font=dict(size=18, color=text_color)
            ),
            xaxis=dict(
                title=dict(text="Hora", font=dict(color=text_color)),
                type="date",
                range=[day_start, day_end],
                tickformat="%H:%M",
                dtick=60 * 60 * 1000,   # 1h
                gridcolor=grid_color,
                showgrid=True,
                tickfont=dict(color=text_color)
            ),
            yaxis=dict(
                title=dict(text="Temperatura (°C)", font=dict(color=text_color)),
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

            if (not is_aemet_connection()) and len(humidities_valid) >= 10:
                st.markdown("### Presión de Vapor")

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
                    df_vapor["dt"] = pd.to_datetime(df_vapor["dt"]).dt.floor("5min")
                    df_vapor = df_vapor.groupby("dt", as_index=False).last()

                    s_e = pd.Series(df_vapor["e"].values, index=pd.to_datetime(df_vapor["dt"]))
                    s_e_s = pd.Series(df_vapor["e_s"].values, index=pd.to_datetime(df_vapor["dt"]))
                    y_e = s_e.reindex(grid)
                    y_e_s = s_e_s.reindex(grid)

                    fig_vapor = go.Figure()
                    fig_vapor.add_trace(go.Scatter(
                        x=grid,
                        y=y_e.values,
                        mode="lines",
                        name="e (Presión de vapor)",
                        line=dict(color="rgb(52, 152, 219)", width=3),
                        connectgaps=True,
                    ))
                    fig_vapor.add_trace(go.Scatter(
                        x=grid,
                        y=y_e_s.values,
                        mode="lines",
                        name="e_s (Presión de saturación)",
                        line=dict(color="rgb(231, 76, 60)", width=2, dash="dash"),
                        connectgaps=True,
                    ))
                    fig_vapor.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                    fig_vapor.update_layout(
                        title=dict(
                            text="Presión de Vapor y Saturación",
                            x=0.5,
                            xanchor="center",
                            font=dict(size=18, color=text_color)
                        ),
                        xaxis=dict(
                            title=dict(text="Hora", font=dict(color=text_color)),
                            type="date",
                            range=[day_start, day_end],
                            tickformat="%H:%M",
                            dtick=60 * 60 * 1000,
                            showgrid=True,
                            gridcolor=grid_color,
                            tickfont=dict(color=text_color)
                        ),
                        yaxis=dict(
                            title=dict(text="Presión de vapor (hPa)", font=dict(color=text_color)),
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
            else:
                if not is_aemet_connection():
                    st.info("ℹ️ Gráfico de presión de vapor no disponible: faltan datos de HR en la serie")

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

            if len(wind_times) >= 3:
                st.markdown("### Viento y Rachas Hoy")

                df_wind = pd.DataFrame({
                    "dt": wind_times,
                    "wind": wind_vals,
                    "gust": gust_vals,
                    "dir": dir_vals,
                }).sort_values("dt")

                df_wind["dt"] = pd.to_datetime(df_wind["dt"]).dt.floor("5min")
                df_wind = df_wind.groupby("dt", as_index=False).last()

                # Limitar análisis de viento/rosa al mismo rango mostrado en el gráfico de "Hoy".
                range_start = pd.Timestamp(day_start)
                range_end = pd.Timestamp(day_end)
                df_wind_view = df_wind[(df_wind["dt"] >= range_start) & (df_wind["dt"] < range_end)].copy()

                if df_wind_view.empty:
                    st.info("ℹ️ No hay datos de viento en la ventana temporal mostrada (hoy).")
                    df_wind_view = pd.DataFrame(columns=["dt", "wind", "gust", "dir"])

                # Algunas estaciones AEMET no reportan VV útil (todo 0) pero sí rachas.
                wind_non_nan = [float(v) for v in df_wind_view["wind"].tolist() if not is_nan(v)]
                gust_non_nan = [float(v) for v in df_wind_view["gust"].tolist() if not is_nan(v)]
                vv_all_zero = (len(wind_non_nan) > 0) and (max(abs(v) for v in wind_non_nan) < 0.1)
                gust_has_signal = (len(gust_non_nan) > 0) and (max(gust_non_nan) >= 1.0)
                if is_aemet_connection() and vv_all_zero and gust_has_signal:
                    df_wind_view["wind"] = float("nan")
                    st.caption("ℹ️ Esta estación no está devolviendo viento medio (VV) útil; se muestran rachas y la rosa usa racha+dirección.")

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

                fig_wind = go.Figure()
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_wind.values,
                    mode="lines",
                    name="Viento",
                    line=dict(color="rgb(74, 201, 240)", width=2.8),
                    connectgaps=True,
                ))
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_gust.values,
                    mode="lines",
                    name="Racha",
                    line=dict(color="rgb(255, 170, 65)", width=2.4, dash="dot"),
                    connectgaps=True,
                ))
                fig_wind.add_trace(go.Scatter(
                    x=grid,
                    y=y_dir.values,
                    mode="markers",
                    name="Dirección",
                    marker=dict(color="rgba(120, 170, 255, 0.85)", size=5),
                    yaxis="y2",
                    hovertemplate="Dirección: %{y:.0f}°<extra></extra>",
                ))
                fig_wind.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                fig_wind.update_layout(
                    title=dict(text="Viento y Rachas Hoy", x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                    xaxis=dict(
                        title=dict(text="Hora", font=dict(color=text_color)),
                        type="date",
                        range=[day_start, day_end],
                        tickformat="%H:%M",
                        dtick=60 * 60 * 1000,
                        showgrid=True,
                        gridcolor=grid_color,
                        tickfont=dict(color=text_color),
                    ),
                    yaxis=dict(
                        title=dict(text="km/h", font=dict(color=text_color)),
                        showgrid=True,
                        gridcolor=grid_color,
                        tickfont=dict(color=text_color),
                        rangemode="tozero",
                    ),
                    yaxis2=dict(
                        title=dict(text="Dirección", font=dict(color=text_color)),
                        overlaying="y",
                        side="right",
                        range=[0, 360],
                        tickvals=[0, 45, 90, 135, 180, 225, 270, 315, 360],
                        ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"],
                        tickfont=dict(color=text_color),
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
                    st.markdown("### Rosa de Viento")

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
                            name="Frecuencia",
                        ))

                        radial_max = max(10.0, math.ceil(max(r_pct) / 5.0) * 5.0)

                        fig_rose.update_layout(
                            title=dict(text="Rosa de viento (% por rumbo)", x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
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

                        st.markdown(f"**Muestras:** {total_samples}")
                        st.markdown(f"**Calma (<1.0 km/h):** {calm_pct:.1f}% ({calm})")
                        if dominant_dir is not None:
                            st.markdown(f"**Dominante:** **{dominant_dir} ({dom_pct:.1f}%)**")
                        else:
                            st.markdown("**Dominante:** —")

                        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                        left_col, right_col = st.columns(2, gap="small")
                        left_dirs = sectors16[:8]
                        right_dirs = sectors16[8:]

                        with left_col:
                            for s in left_dirs:
                                txt = f"{s}: {dir_pcts[s]:.1f}% ({counts[s]})"
                                if s == dominant_dir:
                                    txt = f"**{txt}**"
                                st.markdown(txt)

                        with right_col:
                            for s in right_dirs:
                                txt = f"{s}: {dir_pcts[s]:.1f}% ({counts[s]})"
                                if s == dominant_dir:
                                    txt = f"**{txt}**"
                                st.markdown(txt)
                else:
                    dir_non_nan = sum(1 for v in df_wind["dir"].tolist() if not is_nan(v))
                    st.warning(
                        "⚠️ Rosa de viento no disponible: no hay muestras válidas de dirección en este periodo "
                        f"(dirección válida: {dir_non_nan}, muestras en calma: {calm})."
                    )

    if connected and not has_chart_data:
        section_title("Gráficos")
        if is_aemet_connection():
            st.warning("⚠️ Esta estación no está devolviendo ahora una serie diezminutal válida, por eso no se puede dibujar el gráfico.")
        else:
            st.info("ℹ️ No hay serie temporal disponible para dibujar el gráfico en este momento.")

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "Tendencias":
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
        st.info("👈 Conecta una estación para ver las tendencias")
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

        st.markdown("### Tendencias")

        periodo = st.selectbox(
            "Periodo",
            ["Hoy", "Tendencia sinóptica"],
            key="periodo_tendencias"
        )

        dataset_ready = False
        data_source_label = ""
        has_barometer_series = True

        if periodo == "Hoy":
            st.markdown("Derivadas discretas calculadas según la resolución temporal de cada proveedor.")

            if provider_id == "AEMET":
                idema = st.session_state.get("aemet_station_id", "")
                if not idema:
                    st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                    logger.warning("Tendencias AEMET 20 min: falta idema")
                else:
                    with st.spinner("Obteniendo serie de las últimas 24h de AEMET..."):
                        series_24h = fetch_aemet_all24h_station_series(idema)

                    if not series_24h.get("has_data", False):
                        st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                        logger.warning("Tendencias AEMET 20 min: sin serie all24h")
                    else:
                        dt_list = [datetime.fromtimestamp(ep) for ep in series_24h["epochs"]]
                        temp_list = [float(v) for v in series_24h["temps"]]
                        rh_list = [float(v) for v in series_24h["humidities"]]
                        p_list = [float(v) for v in series_24h["pressures"]]

                        df_trends = pd.DataFrame({
                            "dt": dt_list,
                            "temp": temp_list,
                            "rh": rh_list,
                            "p": p_list,
                        }).sort_values("dt")

                        if df_trends.empty:
                            st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                            logger.warning("Tendencias AEMET 20 min: DataFrame vacío")
                        else:
                            df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("10min")
                            df_trends = df_trends.groupby("dt", as_index=False).last()

                            day_start = df_trends["dt"].min()
                            day_end = df_trends["dt"].max()
                            grid = pd.date_range(start=day_start, end=day_end, freq="10min")

                            interval_theta_e = 20
                            interval_e = 20
                            interval_p = 180
                            dataset_ready = True
                            data_source_label = "AEMET /observacion/convencional/todas (24h)"

            else:
                chart_epochs = st.session_state.get("chart_epochs", [])
                chart_temps = st.session_state.get("chart_temps", [])
                chart_humidities = st.session_state.get("chart_humidities", [])
                chart_pressures = st.session_state.get("chart_pressures", [])

                if len(chart_epochs) == 0:
                    st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
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
                        st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                        logger.warning("Tendencias 20 min: DataFrame vacío")
                    else:
                        df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("5min")
                        df_trends = df_trends.groupby("dt", as_index=False).last()

                        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                        day_end = day_start + timedelta(days=1)
                        grid = pd.date_range(start=day_start, end=day_end, freq="5min", inclusive="left")

                        interval_theta_e = 20
                        interval_e = 20
                        interval_p = 180
                        dataset_ready = True
                        data_source_label = "Serie local del proveedor (20 min)"

        else:
            st.markdown("Derivadas discretas calculadas en intervalos de 3 horas")

            if provider_id == "AEMET":
                idema = st.session_state.get("aemet_station_id", "")
                if not idema:
                    hourly7d = {"has_data": False, "epochs": [], "temps": [], "humidities": [], "pressures": []}
                else:
                    with st.spinner("Obteniendo datos horarios de 7 días (AEMET)..."):
                        hourly7d = fetch_aemet_hourly_7day_series(idema)
                data_source_label = "AEMET horarios 7 días"
            elif provider_id == "WU":
                station_id = st.session_state.get("active_station", "")
                api_key = st.session_state.get("active_key", "")
                with st.spinner("Obteniendo datos horarios de 7 días..."):
                    hourly7d = fetch_hourly_7day_session_cached(station_id, api_key)
                data_source_label = "WU hourly/7day"
            else:
                hourly7d = {
                    "epochs": st.session_state.get("trend_hourly_epochs", []),
                    "temps": st.session_state.get("trend_hourly_temps", []),
                    "humidities": st.session_state.get("trend_hourly_humidities", []),
                    "pressures": st.session_state.get("trend_hourly_pressures", []),
                    "has_data": False,
                }
                hourly7d["has_data"] = len(hourly7d["epochs"]) > 0
                data_source_label = f"{provider_id} (serie horaria genérica)"

            if not hourly7d.get("has_data", False):
                if provider_id == "AEMET":
                    st.warning("⚠️ AEMET no está devolviendo ahora mismo serie horaria de 7 días para esta estación.")
                    st.caption("Endpoint horarios 7 días sin datos o sin cobertura para esta estación en este momento.")
                else:
                    st.warning("⚠️ La estación no está devolviendo serie de datos actualmente.")
                logger.warning(f"Sin datos horarios para tendencia sinóptica ({provider_id})")
            else:
                epochs_7d = hourly7d.get("epochs", [])
                temps_7d = hourly7d.get("temps", [])
                humidities_7d = hourly7d.get("humidities", [])
                dewpts_7d = hourly7d.get("dewpts", [])
                pressures_7d = hourly7d.get("pressures", [])

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
                    grid = pd.to_datetime(df_trends["dt"].values)

                    day_start = df_trends["dt"].min()
                    day_end = df_trends["dt"].max()

                    interval_theta_e = 180
                    interval_e = 180
                    interval_p = 180
                    dataset_ready = True

        if dataset_ready:
            if data_source_label:
                st.caption(f"Fuente de tendencias: {data_source_label}")

            barometer_values = pd.to_numeric(df_trends.get("p"), errors="coerce")
            barometer_valid_count = int(barometer_values.notna().sum())
            has_barometer_series = barometer_valid_count >= 2
            if not has_barometer_series:
                logger.warning(f"Tendencias: estación sin barómetro ({provider_id})")

            humidity_values = pd.to_numeric(df_trends.get("rh"), errors="coerce")
            humidity_valid_count = int(humidity_values.notna().sum())
            has_humidity_series = humidity_valid_count >= 2
            if not has_humidity_series:
                logger.warning(f"Tendencias: estación sin histórico de humedad ({provider_id})")

            if periodo == "Hoy":
                dtick_ms = 60 * 60 * 1000
                tickformat = "%H:%M"
            else:
                dtick_ms = 12 * 60 * 60 * 1000
                tickformat = "%d/%m %H:%M"

            trend_times = pd.to_datetime(df_trends["dt"])

            # --- GRÁFICO 1: Tendencia de θe ---
            if has_barometer_series and has_humidity_series:
                try:
                    theta_e_list = []
                    for _, row in df_trends.iterrows():
                        if not (math.isnan(row["temp"]) or math.isnan(row["rh"]) or math.isnan(row["p"])):
                            theta_e = equivalent_potential_temperature(row["temp"], row["rh"], row["p"])
                            theta_e_list.append(theta_e)
                        else:
                            theta_e_list.append(np.nan)

                    df_trends["theta_e"] = theta_e_list
                    trend_theta_e = calculate_trend(
                        np.asarray(df_trends["theta_e"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_theta_e,
                    )

                    valid_trends = trend_theta_e[~np.isnan(trend_theta_e)]
                    if len(valid_trends) == 0:
                        st.warning("⚠️ No hay datos suficientes para calcular tendencia de θe en este periodo.")
                    else:
                        max_abs = max(abs(valid_trends.min()), abs(valid_trends.max()))
                        y_range_theta_e = [-max_abs * 1.1, max_abs * 1.1]

                        fig_theta_e = go.Figure()
                        fig_theta_e.add_trace(go.Scatter(
                            x=trend_times, y=trend_theta_e, mode="lines+markers", name="dθe/dt",
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
                            title=dict(text="Tendencia de Temperatura Potencial Equivalente (θe)",
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text="Hora", font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text="dθe/dt (K/h)", font=dict(color=text_color)), range=y_range_theta_e,
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
                    st.error("Error al calcular tendencia de θe: " + str(err))
                    logger.error(f"Error tendencia θe: {repr(err)}")
            else:
                if not has_barometer_series and not has_humidity_series:
                    st.warning("⚠️ No se puede calcular tendencia de θe: la estación no tiene barómetro ni higrómetro.")
                elif not has_barometer_series:
                    st.warning("⚠️ No se puede calcular tendencia de θe: la estación no tiene barómetro.")
                elif not has_humidity_series:
                    st.warning("⚠️ No se puede calcular tendencia de θe: la estación no tiene higrómetro.")

            # --- GRÁFICO 2: Tendencia de e (presión de vapor) ---
            if has_humidity_series:
                try:
                    from models.trends import vapor_pressure

                    e_list = []
                    for _, row in df_trends.iterrows():
                        if not (math.isnan(row["temp"]) or math.isnan(row["rh"])):
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

                    valid_trends_e = trend_e[~np.isnan(trend_e)]
                    if len(valid_trends_e) == 0:
                        st.warning("⚠️ No hay datos suficientes para calcular tendencia de e en este periodo.")
                    else:
                        max_abs_e = max(abs(valid_trends_e.min()), abs(valid_trends_e.max()))
                        y_range_e = [-max_abs_e * 1.1, max_abs_e * 1.1]

                        fig_e = go.Figure()
                        fig_e.add_trace(go.Scatter(
                            x=trend_times, y=trend_e, mode="lines+markers", name="de/dt",
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
                            title=dict(text="Tendencia de Presión de Vapor (e)",
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text="Hora", font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text="de/dt (hPa/h)", font=dict(color=text_color)), range=y_range_e,
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
                    st.error("Error al calcular tendencia de e: " + str(err))
                    logger.error(f"Error tendencia e: {repr(err)}")
            else:
                st.warning("⚠️ No se puede calcular tendencia de presión de vapor (e): la estación no tiene higrómetro.")

            # --- GRÁFICO 2.5 (solo Hoy): componentes zonal/meridional del viento ---
            if periodo == "Hoy":
                try:
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

                        uv_valid = np.concatenate([
                            np.asarray(y_u.values, dtype=np.float64),
                            np.asarray(y_v.values, dtype=np.float64),
                        ])
                        uv_valid = uv_valid[~np.isnan(uv_valid)]
                        if len(uv_valid) > 0:
                            max_abs_uv = float(max(abs(uv_valid.min()), abs(uv_valid.max())))
                            if max_abs_uv < 0.5:
                                max_abs_uv = 0.5
                            y_range_uv = [-max_abs_uv * 1.1, max_abs_uv * 1.1]

                            fig_uv = go.Figure()
                            fig_uv.add_trace(go.Scatter(
                                x=grid, y=y_u.values, mode="lines+markers", name="u (zonal)",
                                line=dict(color="rgb(255, 148, 82)", width=2.2),
                                marker=dict(size=4, color="rgb(255, 148, 82)"), connectgaps=True
                            ))
                            fig_uv.add_trace(go.Scatter(
                                x=grid, y=y_v.values, mode="lines+markers", name="v (meridional)",
                                line=dict(color="rgb(96, 196, 129)", width=2.2),
                                marker=dict(size=4, color="rgb(96, 196, 129)"), connectgaps=True
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
                                    text="Componentes del viento",
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
                                    title=dict(text="Hora", font=dict(color=text_color)),
                                    type="date",
                                    range=[day_start, day_end],
                                    tickformat=tickformat,
                                    dtick=dtick_ms,
                                    gridcolor=grid_color,
                                    showgrid=True,
                                    tickfont=dict(color=text_color),
                                ),
                                yaxis=dict(
                                    title=dict(text="Velocidad (km/h)", font=dict(color=text_color)),
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
                            st.info("ℹ️ No hay datos válidos de viento para calcular componentes u/v hoy.")
                    else:
                        st.info("ℹ️ No hay suficientes datos de viento/dirección para dibujar componentes u/v hoy.")
                except Exception as err:
                    st.error("Error al calcular componentes del viento (u/v): " + str(err))
                    logger.error(f"Error componentes viento u/v: {repr(err)}")

            # --- GRÁFICO 3: Tendencia de presión ---
            if has_barometer_series:
                try:
                    trend_p = calculate_trend(
                        np.asarray(df_trends["p"].values, dtype=np.float64),
                        trend_times,
                        interval_minutes=interval_p,
                    )

                    valid_trends_p = trend_p[~np.isnan(trend_p)]
                    if len(valid_trends_p) == 0:
                        st.warning("⚠️ No hay datos suficientes para calcular tendencia de presión en este periodo.")
                    else:
                        max_abs_p = max(abs(valid_trends_p.min()), abs(valid_trends_p.max()))
                        y_range_p = [-max_abs_p * 1.1, max_abs_p * 1.1]

                        fig_p = go.Figure()
                        fig_p.add_trace(go.Scatter(
                            x=trend_times, y=trend_p, mode="lines+markers", name="dp/dt",
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
                            title=dict(text="Tendencia de Presión Absoluta (intervalo 3h)",
                                      x=0.5, xanchor="center", font=dict(size=18, color=text_color)),
                            xaxis=dict(title=dict(text="Hora", font=dict(color=text_color)), type="date", range=[day_start, day_end],
                                      tickformat=tickformat, dtick=dtick_ms,
                                      gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                            yaxis=dict(title=dict(text="dp/dt (hPa/h)", font=dict(color=text_color)), range=y_range_p,
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
                    st.error("Error al calcular tendencia de presión: " + str(err))
                    logger.error(f"Error tendencia presión: {repr(err)}")

            # Sincronizar línea vertical de hover entre gráficos separados (solo Hoy).
            if periodo == "Hoy":
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
# TAB 3: CLIMOGRAMAS
# ============================================================

elif active_tab == "Climogramas":
    st.info("Sección en desarrollo - Próximamente")
    st.markdown("Esta sección mostrará climogramas y estadísticas climáticas.")


# ============================================================
# TAB 4: DIVULGACIÓN
# ============================================================

elif active_tab == "Divulgación":
    st.info("Sección en desarrollo - Próximamente")
    st.markdown("Esta sección contendrá material divulgativo sobre meteorología.")

# ============================================================
# TAB 5: MAPA
# ============================================================

elif active_tab == "Mapa":
    import pydeck as pdk

    section_title("Mapa de estaciones")

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
    if "map_provider_filter" not in st.session_state:
        st.session_state["map_provider_filter"] = ["AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "NWS"]

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
                    st.session_state["map_geo_debug_msg"] = f"Ubicación detectada (±{acc:.0f} m)."
                else:
                    st.session_state["map_geo_debug_msg"] = "Ubicación detectada."
                if swapped:
                    st.session_state["map_geo_debug_msg"] += " Se corrigieron coordenadas invertidas."
                st.session_state["map_geo_last_error"] = ""
                st.rerun()

        error_message = browser_geo_result.get("error_message") or "No se pudo obtener la ubicación del navegador."
        st.session_state["map_geo_last_error"] = str(error_message)
        st.session_state["map_geo_debug_msg"] = ""

    controls_col, filters_col = st.columns([1.1, 1], gap="large")
    with controls_col:
        st.markdown("#### Ubicación")
        if st.button("📍 Usar mi ubicación actual", type="primary", width="stretch"):
            st.session_state["map_geo_request_id"] += 1
            st.session_state["map_geo_pending"] = True
            st.session_state["map_geo_last_error"] = ""
            st.session_state["map_geo_debug_msg"] = "Solicitando ubicación al navegador..."
            st.rerun()

        if st.session_state.get("map_geo_pending"):
            st.caption("Esperando permiso o respuesta de geolocalización...")

        geo_last_error = st.session_state.get("map_geo_last_error", "").strip()
        if geo_last_error:
            st.warning("No se pudo leer la ubicación GPS del navegador. Revisa los permisos del sitio.")
            st.caption(f"Detalle navegador: {geo_last_error}")

        geo_debug_msg = st.session_state.get("map_geo_debug_msg", "")
        if geo_debug_msg:
            st.caption(geo_debug_msg)
        st.caption(
            f"Centro actual: {float(st.session_state.get('map_search_lat')):.4f}, "
            f"{float(st.session_state.get('map_search_lon')):.4f}"
        )

    with filters_col:
        st.markdown("#### Filtros")
        st.multiselect(
            "Proveedores",
            options=["AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "NWS"],
            key="map_provider_filter",
        )
        st.caption("El mapa muestra todas las estaciones disponibles para los proveedores seleccionados.")

    search_lat = float(st.session_state.get("map_search_lat"))
    search_lon = float(st.session_state.get("map_search_lon"))
    provider_filter = set(st.session_state.get("map_provider_filter", []))

    center_in_us = _is_us_map_center(search_lat, search_lon)
    map_max_results = 20000
    if "NWS" in provider_filter:
        # Cargar todo NWS para no recortar por distancia cuando el centro no está en USA.
        map_max_results = 90000

    nearest = search_nearby_stations(search_lat, search_lon, max_results=map_max_results)
    nearest = [s for s in nearest if s.provider_id in provider_filter]

    if not nearest:
        st.warning("No hay estaciones disponibles para los filtros actuales.")
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
            if station.provider_id == "METEOGALICIA":
                town = str(meta.get("concello", "")).strip()
                if town:
                    return town
            if station.provider_id == "NWS":
                tz_name = str(meta.get("tz", "")).strip()
                if tz_name:
                    return tz_name
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
            "METEOGALICIA": [255, 184, 64],
            "NWS": [178, 122, 255],
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
            else:
                return False

            st.session_state["connected"] = True
            st.session_state["_pending_active_tab"] = "Observación"
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
                    "backgroundColor": "rgba(18, 18, 18, 0.92)",
                    "color": "white",
                    "fontSize": "12px",
                },
            },
        )

        metric_col1, metric_col2 = st.columns(2)
        metric_col1.metric("Estaciones visibles", len(points))
        metric_col2.metric("Proveedores", len({p["provider_id"] for p in points}))

        deck_event = None
        try:
            deck_event = _pydeck_chart_stretch(
                deck,
                key="map_stations_chart",
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

        st.markdown("#### Estación seleccionada")
        if isinstance(selected_station, dict):
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
                    f"**{selected_name}** · {selected_provider}  \n"
                    f"ID: `{selected_station_id}` · Ciudad/Pueblo: `{selected_locality}` · "
                    f"Altitud: `{selected_alt_txt}` · Distancia: `{selected_dist_txt}` · "
                    f"Lat/Lon: `{selected_coords_txt}`"
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
                map_toggle_enabled = st.checkbox("Conectar automáticamente al iniciar", key=map_toggle_key)
                if map_toggle_enabled and not is_target_station:
                    if _set_provider_autoconnect_from_map(selected_station):
                        _reset_map_autoconnect_toggle_state()
                        st.success(f"Auto-conexión guardada para {selected_name}.")
                        st.rerun()
                    st.error("No se pudo guardar la auto-conexión para la estación seleccionada.")
                elif (not map_toggle_enabled) and is_target_station:
                    set_local_storage(LS_AUTOCONNECT, "0", "save")
                    set_stored_autoconnect_target(None)
                    st.session_state["_autoconnect_attempted"] = True
                    _reset_map_autoconnect_toggle_state()
                    st.info("Auto-conexión desactivada en este dispositivo.")
                    st.rerun()
            with action_col:
                connect_key = f"map_connect_btn_{selected_provider}_{selected_station_id}"
                if st.button("Conectar", key=connect_key, type="primary", width="stretch"):
                    if _connect_station_from_map(selected_station):
                        st.success(f"Conectado a {selected_name}. Ya puedes ver los datos en Observación.")
                        st.rerun()
                    else:
                        st.error("No se pudo conectar la estación seleccionada.")
        else:
            st.caption("Haz clic sobre una estación del mapa para ver sus detalles y conectarla.")

        table_limit = 100
        stations_rows = [
            {
                "Estación": p["name"],
                "Proveedor": p["provider"],
                "ID": p["station_id"],
                "Ciudad/Pueblo": p["locality"],
                "Altitud (m)": round(float(p["elevation_m"]), 0),
                "Distancia (km)": round(float(p["distance_km"]), 2),
                "Lat": round(float(p["lat"]), 5),
                "Lon": round(float(p["lon"]), 5),
            }
            for p in points_sorted
        ][:table_limit]
        st.caption(f"Mostrando las {len(stations_rows)} estaciones más cercanas en la tabla.")
        table_head_bg = "#0f1728" if dark else "#e9edf5"
        table_row_bg = "#081121" if dark else "#ffffff"
        table_row_alt_bg = "#0d1729" if dark else "#f7f9fc"
        table_border = "rgba(255,255,255,0.10)" if dark else "rgba(15,18,25,0.14)"
        table_text = "rgba(245,248,255,0.95)" if dark else "rgba(15,18,25,0.92)"
        table_muted = "rgba(210,220,235,0.88)" if dark else "rgba(70,80,96,0.88)"

        table_rows_html = ""
        for row in stations_rows:
            table_rows_html += (
                "<tr>"
                f"<td>{html.escape(str(row['Estación']))}</td>"
                f"<td>{html.escape(str(row['Proveedor']))}</td>"
                f"<td>{html.escape(str(row['ID']))}</td>"
                f"<td>{html.escape(str(row['Ciudad/Pueblo']))}</td>"
                f"<td>{html.escape(str(row['Altitud (m)']))}</td>"
                f"<td>{html.escape(str(row['Distancia (km)']))}</td>"
                f"<td>{html.escape(str(row['Lat']))}</td>"
                f"<td>{html.escape(str(row['Lon']))}</td>"
                "</tr>"
            )

        st.markdown(
            f"""
            <style>
            .mlx-map-table-wrap {{
                width: 100%;
                border: 1px solid {table_border};
                border-radius: 10px;
                overflow: auto;
                max-height: 430px;
                background: {table_row_bg};
            }}
            .mlx-map-table {{
                width: 100%;
                border-collapse: collapse;
                color: {table_text};
                font-size: 0.95rem;
            }}
            .mlx-map-table thead th {{
                position: sticky;
                top: 0;
                z-index: 2;
                background: {table_head_bg};
                color: {table_muted};
                text-align: left;
                padding: 10px 8px;
                border-bottom: 1px solid {table_border};
                border-right: 1px solid {table_border};
                font-weight: 600;
            }}
            .mlx-map-table thead th:last-child {{
                border-right: none;
            }}
            .mlx-map-table tbody tr:nth-child(odd) {{
                background: {table_row_bg};
            }}
            .mlx-map-table tbody tr:nth-child(even) {{
                background: {table_row_alt_bg};
            }}
            .mlx-map-table td {{
                padding: 9px 8px;
                border-bottom: 1px solid {table_border};
                border-right: 1px solid {table_border};
                white-space: nowrap;
            }}
            .mlx-map-table td:last-child {{
                border-right: none;
            }}
            </style>
            <div class="mlx-map-table-wrap">
                <table class="mlx-map-table">
                    <thead>
                        <tr>
                            <th>Estación</th>
                            <th>Proveedor</th>
                            <th>ID</th>
                            <th>Ciudad/Pueblo</th>
                            <th>Altitud (m)</th>
                            <th>Distancia (km)</th>
                            <th>Lat</th>
                            <th>Lon</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows_html}
                    </tbody>
                </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ============================================================
# AUTOREFRESH SOLO EN OBSERVACIÓN
# ============================================================
# Autorefresh solo se activa cuando el tab activo es Observación

if st.session_state.get("connected", False):
    if active_tab == "Observación":
        refresh_interval = _provider_refresh_seconds()
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
            <span><b>MeteoLabX · Versión 0.7.1</b></span>
            <span class="mlb-footer-news">
              <details>
                <summary>Novedades</summary>
                <div class="mlb-footer-box">
                  <h2 style="margin:0 0 0.6rem 0;">0.7.1</h2>
                  <h3>Mejoras</h3>
                  <ul>
                    <li>Añadido soporte para estaciones de <b>MeteoGalicia</b> y del <b>NWS (EE. UU.)</b>.</li>
                    <li>Añadido gráfico de <b>componentes del viento</b>.</li>
                    <li>Nueva sección <b>Mapa</b>, con acceso a más de <b>30.000 estaciones</b> disponibles.</li>
                    <li>Añadida la opción de <b>conectarse automáticamente a una estación al iniciar</b>.</li>
                    <li>Mejorado el cálculo de <b>θe</b>: ahora utiliza la formulación de <b>Bolton (1980)</b>.</li>
                    <li>La búsqueda manual ahora permite encontrar <b>cualquier localización</b> mediante <b>Nominatim</b>.</li>
                  </ul>
                  <h3>Correcciones</h3>
                  <ul>
                    <li>Corregido el aislamiento de <b>credenciales y auto-conexión</b> entre navegadores/dispositivos.</li>
                    <li>Corregido el error que obligaba a hacer <b>doble clic en las pestañas</b>.</li>
                    <li>Solucionados problemas relacionados con los <b>temas</b>.</li>
                    <li>Corregido un problema con valores de <b>irradiancia negativa</b> en estaciones de <b>Meteocat</b>.</li>
                  </ul>
                </div>
              </details>
            </span>
          </div>
          <div class="mlb-footer-bottom">Fuentes: WU · AEMET · Meteocat · Euskalmet · MeteoGalicia · NWS · No afiliado · © 2026</div>
        </div>
        """
    ),
    unsafe_allow_html=True
)

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
from streamlit_autorefresh import st_autorefresh

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES
from utils import html_clean, is_nan, es_datetime_from_epoch, age_string, fmt_hpa
from utils.storage import localS
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
from services.aemet import get_aemet_data, is_aemet_connection, get_aemet_daily_charts
from components.aemet_selector import render_aemet_selector, show_aemet_connection_status

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# SIDEBAR Y TEMA
# ============================================================

theme_mode, dark = render_sidebar(localS)

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

st.markdown(f"""
<style>
/* Forzar tema de sidebar */
[data-testid="stSidebar"] {{
    background-color: {sidebar_bg} !important;
}}

[data-testid="stSidebar"] * {{
    color: {sidebar_text} !important;
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

[data-testid="stSidebar"] input {{
    color: {sidebar_text} !important;
    background-color: {'#ffffff' if not dark else '#0e1117'} !important;
}}

/* Contenedor de inputs en sidebar (incluye zona del ojo y +/-) */
[data-testid="stSidebar"] [data-baseweb="input"] {{
    background-color: {'#ffffff' if not dark else '#0e1117'} !important;
    border-color: {button_border} !important;
}}

/* Botón del ojo de la API key (evitar cuadro negro) */
[data-testid="stSidebar"] [data-testid="stTextInput"] button {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 0 !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover {{
    background: {'#f3f5fa' if not dark else '#141821'} !important;
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
[data-testid="stSidebar"] button[kind="primary"],
[data-testid="stSidebar"] button[kind="secondary"] {{
    background-color: {sidebar_bg} !important;
    color: {sidebar_text} !important;
    border: {button_border_width} solid {button_border} !important;
}}

[data-testid="stSidebar"] button[kind="primary"]:hover,
[data-testid="stSidebar"] button[kind="secondary"]:hover {{
    background-color: {'#e8ecf3' if not dark else '#1f2229'} !important;
    border-color: {'rgba(100, 100, 100, 0.9)' if not dark else 'rgba(150, 150, 150, 0.9)'} !important;
}}

/* Checkbox */
[data-testid="stSidebar"] [data-testid="stCheckbox"] {{
    color: {sidebar_text} !important;
}}

/* Radios del tema: forzar colores para que cambien al alternar claro/oscuro */
[data-testid="stSidebar"] input[type="radio"] {{
    accent-color: #ff4b4b !important;
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
    background: {'rgba(255,255,255,0.45)' if not dark else 'rgba(22,25,31,0.45)'} !important;
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
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<meta name="theme-color" content="#2384ff">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">

<style>
  .block-container { 
    padding-top: 1.2rem; 
    max-width: 1200px;
  }

  .header{
    display:flex; 
    align-items:baseline; 
    justify-content:space-between;
    margin-bottom: 0.4rem;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .header h1{ 
    margin:0; 
    font-size:2.0rem; 
    color:var(--text); 
  }
  .meta{ 
    color:var(--muted); 
    font-size:0.95rem; 
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

  .subtitle div{ white-space: nowrap; }
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

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>🛰️ MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 5</span></h1>
      <div class="meta">
        Versión beta — la interfaz y las funciones pueden cambiar ·
        Tema: {"Oscuro" if dark else "Claro"} · Refresh: {REFRESH_SECONDS}s
      </div>
    </div>
    """),
    unsafe_allow_html=True
)


# ============================================================
# COMPROBACIÓN DE CONEXIÓN
# ============================================================

connected = st.session_state.get("connected", False)

if connected:
    provider_id = st.session_state.get("connection_type", "")
    if provider_id == "AEMET":
        station_name = st.session_state.get("aemet_station_name", "Estación AEMET")
    elif provider_id == "WU":
        station_name = st.session_state.get("active_station", "Estación WU")
    else:
        station_name = st.session_state.get("provider_station_name", "Estación")

    badge_bg = "rgba(56, 92, 132, 0.35)" if dark else "rgba(51, 126, 215, 0.12)"
    badge_border = "rgba(92, 158, 230, 0.45)" if dark else "rgba(51, 126, 215, 0.28)"
    badge_text = "rgba(142, 201, 255, 0.96)" if dark else "rgba(34, 93, 170, 0.96)"

    st.markdown(
        html_clean(
            f"""
            <div style="
                margin: 0.2rem 0 0.75rem 0;
                display: inline-block;
                padding: 0.42rem 0.72rem;
                border-radius: 999px;
                border: 1px solid {badge_border};
                background: {badge_bg};
                color: {badge_text};
                font-size: 0.88rem;
                font-weight: 600;
            ">
                📡 {provider_id} · {station_name}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

if not connected:
    st.markdown(
        html_clean(
            """
            <div style="
                margin: 0.35rem 0 0.7rem 0;
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
    render_aemet_selector()



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
        
        # Lluvia
        inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
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
        
        p_abs_disp = "—" if is_nan(p_abs) else int(round(p_abs))
        p_msl_disp = "—" if is_nan(p_msl) else int(round(p_msl))
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
        
    else:
        # ========== DATOS DE WEATHER UNDERGROUND ==========
        station_id = st.session_state.get("active_station", "").strip()
        api_key = st.session_state.get("active_key", "").strip()

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
            p_abs_disp = int(round(p_abs))
            p_msl_disp = int(round(p_msl))

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
                clarity = sky_clarity_index(solar_rad, lat, z, now_ts)

                # Balance hídrico
                balance = water_balance(base["precip_total"], et0)

                # Logging seguro (manejar NaN)
                solar_str = f"{solar_rad:.0f}" if not is_nan(solar_rad) else "N/A"
                uv_str = f"{uv:.1f}" if not is_nan(uv) else "N/A"
                et0_str = f"{et0:.2f}" if not is_nan(et0) else "N/A"
                balance_str = f"{balance:.2f}" if not is_nan(balance) else "N/A"

                logger.info(f"   Radiación: Solar={solar_str} W/m², UV={uv_str}")
                logger.info(f"   ET0={et0_str} mm/día, Balance={balance_str} mm")


            # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
            timeseries = fetch_daily_timeseries(station_id, api_key)
            chart_epochs = timeseries.get("epochs", [])
            chart_temps = timeseries.get("temps", [])
            chart_humidities = timeseries.get("humidities", [])
            chart_dewpts = timeseries.get("dewpts", [])
            chart_pressures = timeseries.get("pressures", [])
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

            # Guardar en session_state para acceso desde otras tabs
            st.session_state["chart_epochs"] = chart_epochs
            st.session_state["chart_temps"] = chart_temps
            st.session_state["chart_humidities"] = chart_humidities
            st.session_state["chart_dewpts"] = chart_dewpts
            st.session_state["chart_pressures"] = chart_pressures
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
    st.markdown(
        html_clean(
            f"<div class='meta'>Último dato (local): {es_datetime_from_epoch(base['epoch'])} · Edad: {age_string(base['epoch'])}</div>"
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

# Preservar tab activo al cambiar tema
if "preserved_tab" not in st.session_state:
    st.session_state["preserved_tab"] = 0

tab_options = ["📊 Observación", "📈 Tendencias", "🌡️ Climogramas", "📚 Divulgación"]

# Radio buttons estilizados como tabs con underline
active_tab = st.radio(
    "Navegación",
    tab_options,
    horizontal=True,
    index=st.session_state["preserved_tab"],
    key="active_tab",
    label_visibility="collapsed"
)

# Guardar tab actual para preservarlo en reruns
st.session_state["preserved_tab"] = tab_options.index(active_tab)

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# TAB 1: OBSERVACIÓN
if active_tab == "📊 Observación":
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

        # Formatear valores
        solar_val = "—" if is_nan(solar_rad) else f"{solar_rad:.0f}"
        uv_val = "—" if is_nan(uv) else f"{uv:.1f}"
        et0_val = "—" if is_nan(et0) else f"{et0:.2f}"
        clarity_val = "—" if is_nan(clarity) else f"{clarity * 100:.0f}"
        balance_val = "—" if is_nan(balance) else f"{balance:.1f}"

        # Subtítulos
        uv_label = uv_index_label(uv)
        uv_sub = f"<div style='font-size:0.85rem; opacity:0.75;'>{uv_label}</div>"

        et0_sub = "<div style='font-size:0.8rem; opacity:0.65; margin-top:2px;'>FAO-56 Penman-Monteith</div>"

        clarity_label = sky_clarity_label(clarity)
        clarity_sub = f"<div style='font-size:0.85rem; opacity:0.75;'>{clarity_label}</div>"

        balance_label = water_balance_label(balance)
        balance_sub = f"<div style='font-size:0.85rem; opacity:0.75; margin-top:2px;'>{balance_label}</div>"

        # Grid de 4 columnas (como termodinámica)
        # Primera fila: Solar, UV, ET0, Clarity
        cards_radiation_row1 = [
            card("Radiación solar", solar_val, "W/m²", icon_kind="solar", uid="r1", dark=dark),
            card("Índice UV", uv_val, "", icon_kind="uv", subtitle_html=uv_sub, uid="r2", dark=dark),
            card("Evapotranspiración", et0_val, "mm/día", icon_kind="et0", subtitle_html=et0_sub, uid="r3", dark=dark),
            card("Claridad del cielo", clarity_val, "%", icon_kind="clarity", subtitle_html=clarity_sub, uid="r4", dark=dark),
        ]
        render_grid(cards_radiation_row1, cols=4)

        # Segunda fila: Balance (con espacio superior)
        cards_radiation_row2 = [
            card("Balance hídrico", balance_val, "mm", icon_kind="balance", subtitle_html=balance_sub, uid="r5", dark=dark),
        ]
        render_grid(cards_radiation_row2, cols=4, extra_class="grid-row-spacing")

    # ============================================================
    # NIVEL 4 — GRÁFICOS
    # ============================================================
    
    # Definir colores según tema (disponible para todos los gráficos)
    if dark:
        text_color = "rgba(255, 255, 255, 0.92)"
        grid_color = "rgba(255, 255, 255, 0.15)"
    else:
        text_color = "rgba(15, 18, 25, 0.92)"
        grid_color = "rgba(18, 18, 18, 0.12)"

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
        st.markdown("### 🌡️ Temperatura")
        
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

        
        st.plotly_chart(fig, use_container_width=True, key=f"temp_graph_{theme_mode}")

        # Gráfico de presión de vapor solo para WU (AEMET no ofrece HR diezminutal fiable)
        if not is_aemet_connection():
            humidities_valid = [h for h in chart_humidities if not is_nan(h)]

            if len(humidities_valid) >= 10:
                st.markdown("### 💧 Presión de Vapor")

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
                        connectgaps=False,
                    ))
                    fig_vapor.add_trace(go.Scatter(
                        x=grid,
                        y=y_e_s.values,
                        mode="lines",
                        name="e_s (Presión de saturación)",
                        line=dict(color="rgb(231, 76, 60)", width=2, dash="dash"),
                        connectgaps=False,
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
                    st.plotly_chart(
                        fig_vapor,
                        use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"vapor_graph_{theme_mode}"
                    )
            else:
                st.info("ℹ️ Gráfico de presión de vapor no disponible: faltan datos de HR en la serie")

    if connected and not has_chart_data:
        section_title("Gráficos")
        if is_aemet_connection():
            st.warning("⚠️ Esta estación no está devolviendo ahora una serie diezminutal válida, por eso no se puede dibujar el gráfico.")
        else:
            st.info("ℹ️ No hay serie temporal disponible para dibujar el gráfico en este momento.")

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "📈 Tendencias":
    # Definir colores según tema
    if dark:
        text_color = "rgba(255, 255, 255, 0.92)"
        grid_color = "rgba(255, 255, 255, 0.15)"
    else:
        text_color = "rgba(15, 18, 25, 0.92)"
        grid_color = "rgba(18, 18, 18, 0.12)"
    
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
        
        # Definir now_local para líneas verticales en gráficos
        now_local = datetime.now()

        st.markdown("### 📈 Tendencias")

        # Selector de periodo
        periodo = st.selectbox(
            "Periodo",
            ["Hoy (20 min)", "Tendencia sinóptica"],
            key="periodo_tendencias"
        )

        # Verificar datos según el periodo
        can_show_trends = False
        if periodo == "Hoy (20 min)":
            if not has_chart_data:
                st.info("No hay datos suficientes para calcular tendencias de hoy")
            else:
                can_show_trends = True
        else:  # Tendencia sinóptica
            can_show_trends = True  # Intentará obtener datos más adelante

        if can_show_trends:

            if periodo == "Hoy (20 min)":
                st.markdown("Derivadas discretas calculadas en intervalos de 20 minutos")

                # ========== DATOS DE HOY (all1day - 5min) ==========
                # Obtener datos desde session_state
                chart_epochs = st.session_state.get("chart_epochs", [])
                chart_temps = st.session_state.get("chart_temps", [])
                chart_humidities = st.session_state.get("chart_humidities", [])
                chart_pressures = st.session_state.get("chart_pressures", [])

                # Preparar datos
                dt_list = []
                temp_list = []
                rh_list = []
                p_list = []

                for i, (epoch, temp) in enumerate(zip(chart_epochs, chart_temps)):
                    dt = datetime.fromtimestamp(epoch)
                    dt_list.append(dt)
                    temp_list.append(float(temp))

                    rh = chart_humidities[i] if i < len(chart_humidities) else float("nan")
                    p = chart_pressures[i] if i < len(chart_pressures) else float("nan")

                    rh_list.append(float(rh))
                    p_list.append(float(p))

                df_trends = pd.DataFrame({
                    "dt": dt_list,
                    "temp": temp_list,
                    "rh": rh_list,
                    "p": p_list
                }).sort_values("dt")

                # Alinear a rejilla de 5 minutos
                df_trends["dt"] = pd.to_datetime(df_trends["dt"]).dt.floor("5min")
                df_trends = df_trends.groupby("dt", as_index=False).last()

                # Malla completa del día
                now_local = datetime.now()
                day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)

                grid = pd.date_range(start=day_start, end=day_end, freq="5min", inclusive="left")

                # Intervalos
                interval_theta_e = 20  # minutos
                interval_e = 20  # minutos (presión de vapor)
                interval_p = 180  # 3 horas

            else:  # Tendencia sinóptica
                st.markdown("Derivadas discretas calculadas en intervalos de 3 horas")

                # ========== DATOS DE 7 DÍAS (hourly/7day) ==========
                station_id = st.session_state.get("active_station", "")
                api_key = st.session_state.get("active_key", "")

                with st.spinner("Obteniendo datos horarios de 7 días..."):
                    hourly7d = fetch_hourly_7day_session_cached(station_id, api_key)

                if not hourly7d.get("has_data", False):
                    st.warning("No hay datos horarios disponibles para mostrar tendencias sinópticas.")
                    logger.warning("Sin datos horarios para tendencia sinóptica")
                else:
                    logger.info(f"Tendencia sinóptica: {len(hourly7d.get('epochs', []))} puntos de datos")
                    # Preparar datos
                    epochs_7d = hourly7d["epochs"]
                    temps_7d = hourly7d["temps"]
                    humidities_7d = hourly7d.get("humidities", [])
                    dewpts_7d = hourly7d.get("dewpts", [])
                    pressures_7d = hourly7d["pressures"]
                    
                    # FALLBACK: Si no hay humidities, calcularlas desde T y Td
                    # (esto no debería ser necesario normalmente)
                    if len(humidities_7d) == 0 or all(is_nan(h) for h in humidities_7d):
                        logger.warning("⚠️  API 7d no devolvió humedad - usando fallback desde T y Td")
                        humidities_7d = []
                        for temp, td in zip(temps_7d, dewpts_7d):
                            if is_nan(temp) or is_nan(td):
                                humidities_7d.append(float("nan"))
                            else:
                                # RH = 100 * e(Td) / e_s(T)
                                e_td = e_s(td)
                                e_s_t = e_s(temp)
                                rh = 100.0 * e_td / e_s_t if e_s_t > 0 else float("nan")
                                humidities_7d.append(rh)

                    dt_list = []
                    temp_list = []
                    rh_list = []
                    p_list = []

                    for i, epoch in enumerate(epochs_7d):
                        dt = datetime.fromtimestamp(epoch)
                        dt_list.append(dt)
                        temp_list.append(float(temps_7d[i]))
                        rh_list.append(float(humidities_7d[i]))
                        p_list.append(float(pressures_7d[i]))

                    df_trends = pd.DataFrame({
                        "dt": dt_list,
                        "temp": temp_list,
                        "rh": rh_list,
                        "p": p_list
                    }).sort_values("dt")

                    # NO alinear a rejilla, ya son datos horarios exactos
                    df_trends["dt"] = pd.to_datetime(df_trends["dt"])

                    # Usar los timestamps exactos de los datos como grid
                    grid = pd.to_datetime(df_trends["dt"].values)

                    # Ventana de tiempo: desde el primer dato hasta el último
                    day_start = df_trends["dt"].min()
                    day_end = df_trends["dt"].max()

                    # Intervalos de 3 horas para análisis sinóptico
                    interval_theta_e = 180  # 3 horas
                    interval_e = 180  # 3 horas (presión de vapor)
                    interval_p = 180  # 3 horas

            # Formato de eje X según periodo
            if periodo == "Hoy (20 min)":
                dtick_ms = 60 * 60 * 1000  # Cada 1 hora
                tickformat = "%H:%M"
            else:  # Tendencia sinóptica
                dtick_ms = 12 * 60 * 60 * 1000  # Cada 12 horas
                tickformat = "%d/%m %H:%M"

            # --- GRÁFICO 1: Tendencia de θe ---
            try:
                theta_e_list = []
                for _, row in df_trends.iterrows():
                    if not (math.isnan(row["temp"]) or math.isnan(row["rh"]) or math.isnan(row["p"])):
                        theta_e = equivalent_potential_temperature(row["temp"], row["rh"], row["p"])
                        theta_e_list.append(theta_e)
                    else:
                        theta_e_list.append(np.nan)

                df_trends["theta_e"] = theta_e_list
                s_theta_e = pd.Series(df_trends["theta_e"].values, index=pd.to_datetime(df_trends["dt"]))
                y_theta_e = s_theta_e.reindex(grid)

                trend_theta_e = calculate_trend(y_theta_e.values, grid, interval_minutes=interval_theta_e)

                valid_trends = trend_theta_e[~np.isnan(trend_theta_e)]
                if len(valid_trends) > 0:
                    max_abs = max(abs(valid_trends.min()), abs(valid_trends.max()))
                    y_range_theta_e = [-max_abs * 1.1, max_abs * 1.1]
                else:
                    y_range_theta_e = [-1, 1]

                fig_theta_e = go.Figure()
                fig_theta_e.add_trace(go.Scatter(
                    x=grid, y=trend_theta_e, mode="lines", name="dθe/dt",
                    line=dict(color="rgb(255, 107, 107)", width=3), connectgaps=False
                ))
                fig_theta_e.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                fig_theta_e.add_hline(y=0, line_width=1, line_dash="dash", opacity=0.3, line_color=text_color)

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


                st.plotly_chart(fig_theta_e, use_container_width=True, key=f"theta_e_graph_{theme_mode}_{periodo}")
            except Exception as err:
                st.error("Error al calcular tendencia de θe: " + str(err))
                logger.error(f"Error tendencia θe: {repr(err)}")

            # --- GRÁFICO 2: Tendencia de e (presión de vapor) ---
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
                s_e = pd.Series(df_trends["e"].values, index=pd.to_datetime(df_trends["dt"]))
                y_e = s_e.reindex(grid)

                trend_e = calculate_trend(y_e.values, grid, interval_minutes=interval_e)

                valid_trends_e = trend_e[~np.isnan(trend_e)]
                if len(valid_trends_e) > 0:
                    max_abs_e = max(abs(valid_trends_e.min()), abs(valid_trends_e.max()))
                    y_range_e = [-max_abs_e * 1.1, max_abs_e * 1.1]
                else:
                    y_range_e = [-0.1, 0.1]

                fig_e = go.Figure()
                fig_e.add_trace(go.Scatter(
                    x=grid, y=trend_e, mode="lines", name="de/dt",
                    line=dict(color="rgb(107, 170, 255)", width=3), connectgaps=False
                ))
                fig_e.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                fig_e.add_hline(y=0, line_width=1, line_dash="dash", opacity=0.3, line_color=text_color)

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


                st.plotly_chart(fig_e, use_container_width=True, key=f"e_graph_{theme_mode}_{periodo}")
            except Exception as err:
                st.error("Error al calcular tendencia de e: " + str(err))
                logger.error(f"Error tendencia e: {repr(err)}")

            # --- GRÁFICO 3: Tendencia de presión ---
            try:
                s_p = pd.Series(df_trends["p"].values, index=pd.to_datetime(df_trends["dt"]))
                y_p = s_p.reindex(grid)

                trend_p = calculate_trend(y_p.values, grid, interval_minutes=interval_p)

                valid_trends_p = trend_p[~np.isnan(trend_p)]
                if len(valid_trends_p) > 0:
                    max_abs_p = max(abs(valid_trends_p.min()), abs(valid_trends_p.max()))
                    y_range_p = [-max_abs_p * 1.1, max_abs_p * 1.1]
                else:
                    y_range_p = [-1, 1]

                fig_p = go.Figure()
                fig_p.add_trace(go.Scatter(
                    x=grid, y=trend_p, mode="lines", name="dp/dt",
                    line=dict(color="rgb(150, 107, 255)", width=3), connectgaps=False
                ))
                fig_p.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
                fig_p.add_hline(y=0, line_width=1, line_dash="dash", opacity=0.3, line_color=text_color)

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


                st.plotly_chart(fig_p, use_container_width=True, key=f"p_graph_{theme_mode}_{periodo}")
            except Exception as err:
                st.error("Error al calcular tendencia de presión: " + str(err))
                logger.error(f"Error tendencia presión: {repr(err)}")


# ============================================================
# TAB 3: CLIMOGRAMAS
# ============================================================

elif active_tab == "🌡️ Climogramas":
    st.info("🌡️ Sección en desarrollo - Próximamente")
    st.markdown("Esta sección mostrará climogramas y estadísticas climáticas.")


# ============================================================
# TAB 4: DIVULGACIÓN
# ============================================================

elif active_tab == "📚 Divulgación":
    st.info("📚 Sección en desarrollo - Próximamente")
    st.markdown("Esta sección contendrá material divulgativo sobre meteorología.")

# ============================================================
# AUTOREFRESH SOLO EN OBSERVACIÓN
# ============================================================
# Autorefresh solo se activa cuando el tab activo es Observación

if st.session_state.get("connected", False):
    if active_tab == "📊 Observación":
        # Ajustar refresh según tipo de conexión
        if is_aemet_connection():
            # AEMET: refresh cada 30 minutos (datos se actualizan lentamente)
            refresh_interval = 1800  # 30 minutos en segundos
        else:
            # Weather Underground: refresh normal (30 segundos por defecto)
            refresh_interval = REFRESH_SECONDS
        
        st_autorefresh(interval=refresh_interval * 1000, key="refresh_data")

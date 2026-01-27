"""
MeteoLabx - Panel meteorológico avanzado
Aplicación principal
"""
import streamlit as st
import time
import logging
from streamlit_autorefresh import st_autorefresh

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES
from utils import html_clean, is_nan, es_datetime_from_epoch, age_string, fmt_hpa
from utils.storage import localS
from api import WuError, fetch_wu_current_session_cached
from models import (
    e_s, q_from_e, theta_celsius, Tv_celsius, Te_celsius,
    lcl_height, msl_to_absolute, air_density, absolute_humidity,
    wet_bulb_celsius
)
from services import (
    rain_rates_from_total, rain_intensity_label,
    init_pressure_history, push_pressure, pressure_trend_3h
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, wind_name_cat, render_sidebar
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================

st.set_page_config(page_title="MeteoLabx", layout="wide")


# ============================================================
# SIDEBAR Y TEMA
# ============================================================

theme_mode, dark = render_sidebar(localS)


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

# CSS de componentes
st.markdown(html_clean("""
<style>
  .block-container { padding-top: 1.2rem; max-width: 1200px; }

  .header{
    display:flex; align-items:baseline; justify-content:space-between;
    margin-bottom: 0.4rem;
  }
  .header h1{ margin:0; font-size:2.0rem; color:var(--text); }
  .meta{ color:var(--muted); font-size:0.95rem; }

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

  .grid-6{
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }

  .grid-4{
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .grid-3{
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (max-width: 1300px){
    .grid-6{ grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }

  @media (max-width: 1000px){
    .grid-3{ grid-template-columns: repeat(2, 1fr); }
  }

  @media (max-width: 900px){
    .grid-6{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .grid-4{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }

  @media (max-width: 600px){
    .grid-3{ grid-template-columns: 1fr; }
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
  .card:hover{ transform: translateY(-2px); }

  .card.card-h{
    flex-direction: row;
    align-items: flex-start;
    gap: 14px;
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
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--muted);
  line-height: 1.05;
  white-space: nowrap;
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

  .unit{
    margin-left: 6px;
    font-size: 1.0rem;
    color: var(--muted);
    font-weight: 600;
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

  .subtitle{
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.35;
  }

  .subtitle div{ white-space: nowrap; }
  .subtitle b{ color: var(--text); font-weight: 600; }
</style>
"""), unsafe_allow_html=True)


# ============================================================
# AUTO-REFRESH
# ============================================================

# Advertir si refresh es muy bajo
if REFRESH_SECONDS < MIN_REFRESH_SECONDS:
    st.warning(f"⚠️ Refresh configurado en {REFRESH_SECONDS}s. Mínimo recomendado: {MIN_REFRESH_SECONDS}s para evitar rate limiting.")

# Refresco de datos solo cuando está conectado
if st.session_state.get("connected", False):
    st_autorefresh(interval=REFRESH_SECONDS * 1000, key="refresh_data")

# Refresco rápido para la edad (no llama a WU)
st_autorefresh(interval=1000, key="refresh_age")


# ============================================================
# HEADER
# ============================================================

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>🛰️ MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 2</span></h1>
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

if not st.session_state.get("connected", False):
    st.info("Introduce Station ID, API key y altitud en la barra lateral y pulsa **Conectar**.")
    st.stop()


# ============================================================
# OBTENCIÓN Y PROCESAMIENTO DE DATOS
# ============================================================

station_id = st.session_state["active_station"]
api_key = st.session_state["active_key"]
z = float(st.session_state["active_z"])

try:
    # Obtener datos de WU (con cache)
    base = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)
    
    now_ts = time.time()
    
    # Advertir si los datos son muy antiguos
    data_age_minutes = (now_ts - base["epoch"]) / 60
    if data_age_minutes > MAX_DATA_AGE_MINUTES:
        st.warning(f"⚠️ Datos con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
        logger.warning(f"Datos antiguos: {data_age_minutes:.1f} minutos")

    # ========== LLUVIA ==========
    inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], now_ts)
    inst_label = rain_intensity_label(inst_mm_h)

    # ========== PRESIÓN ==========
    p_msl = float(base["p_hpa"])
    p_abs = msl_to_absolute(p_msl, z, base["Tc"])
    p_abs_disp = int(round(p_abs))
    p_msl_disp = int(round(p_msl))

    init_pressure_history()
    push_pressure(p_abs, base["epoch"])
    dp3, rate_h, p_label, p_arrow = pressure_trend_3h()

    press_sub = f"""
    <div>Tendencia: <b>{p_arrow} {p_label}</b></div>
    <div>Δ3h: <b>{fmt_hpa(dp3, 1)} hPa</b></div>
    <div>MSL: <b>{p_msl_disp} hPa</b></div>
    """

    # ========== TERMODINÁMICA ==========
    e = e_s(base["Td"])
    q = q_from_e(e, p_abs)
    q_gkg = q * 1000

    theta = theta_celsius(base["Tc"], p_abs)
    Tv = Tv_celsius(base["Tc"], q)
    Te = Te_celsius(base["Tc"], q)

    Tk = base["Tc"] + 273.15
    Tvk = Tv + 273.15

    # ========== BULBO HÚMEDO (aprox.) ==========
    Tw = wet_bulb_celsius(base["Tc"], base["RH"])

    # ========== DENSIDAD DEL AIRE ==========
    rho = air_density(p_abs, Tv)

    # ========== HUMEDAD ABSOLUTA ==========
    rho_v_gm3 = absolute_humidity(e, base["Tc"])

    # ========== LCL ==========
    lcl = lcl_height(base["Tc"], base["Td"])

    # ========== METADATA ==========
    st.markdown(
        html_clean(
            f"<div class='meta'>Último dato (local): {es_datetime_from_epoch(base['epoch'])} · Edad: {age_string(base['epoch'])}</div>"
        ),
        unsafe_allow_html=True
    )

    # ============================================================
    # NIVEL 1 — BÁSICOS
    # ============================================================
    section_title("Observados")

    deg = base["wind_dir_deg"]
    wind = base["wind"]

    if is_nan(wind) or wind == 0.0 or is_nan(deg):
        wind_dir_str = "—"
    else:
        short = wind_dir_text(deg)
        name = wind_name_cat(deg)
        wind_dir_str = f"{short} · {name} ({deg:.0f}°)"

    gust_str = "—" if is_nan(base["gust"]) else f"{base['gust']:.1f}"

    wind_sub = f"""
    <div>Racha: <b>{gust_str}</b></div>
    <div>Dirección: <b>{wind_dir_str}</b></div>
    """

    precip_total_str = "—" if is_nan(base["precip_total"]) else f"{base['precip_total']:.1f}"

    def fmt_rate(x):
        return "—" if is_nan(x) else f"{x:.1f} mm/h"

    rain_sub = f"""
    <div>Instantánea: <b>{fmt_rate(inst_mm_h)}</b></div>
    <div style="font-size:0.9rem; opacity:0.85;">
      {inst_label}
    </div>
    <div style="margin-top:6px; font-size:0.8rem; opacity:0.6;">
      1 min: {fmt_rate(r1_mm_h)} · 5 min: {fmt_rate(r5_mm_h)}
    </div>
    """

    fl_str = f"{base['feels_like']:.1f} °C" if not is_nan(base["feels_like"]) else "—"
    hi_str = f"{base['heat_index']:.1f} °C" if not is_nan(base["heat_index"]) else "Lo"

    temp_sub = f"""
    <div>Feels like: <b>{fl_str}</b></div>
    <div>Heat index: <b>{hi_str}</b></div>
    """

    # Extremos diarios (si WU los devuelve en current)
    temp_side = ""
    try:
        tmax = base.get("temp_max")
        tmin = base.get("temp_min")
        if (tmax is not None) and (tmin is not None) and (not is_nan(tmax)) and (not is_nan(tmin)):
            temp_side = (
                f"<div class='max'>▲ {tmax:.1f}</div>"
                f"<div class='min'>▼ {tmin:.1f}</div>"
            )
    except Exception:
        temp_side = ""

    rh_side = ""
    try:
        rhmax = base.get("rh_max")
        rhmin = base.get("rh_min")
        if (rhmax is not None) and (rhmin is not None) and (not is_nan(rhmax)) and (not is_nan(rhmin)):
            rh_side = (
                f"<div class='max'>▲ {rhmax:.0f}</div>"
                f"<div class='min'>▼ {rhmin:.0f}</div>"
            )
    except Exception:
        rh_side = ""

    wind_side = ""
    try:
        gmax = base.get("gust_max")
        if (gmax is not None) and (not is_nan(gmax)):
            wind_side = f"<div class='max'>▲ {gmax:.1f}</div>"
    except Exception:
        wind_side = ""



    dew_sub = f"""
    <div>Presión de vapor: <b>{e:.1f} hPa</b></div>
    """

    cards_basic = [
        card("Temperatura", f"{base['Tc']:.1f}", "°C", icon_kind="temp", subtitle_html=temp_sub, side_html=temp_side, uid="b1", dark=dark),
        card("Humedad relativa", f"{base['RH']:.0f}", "%", icon_kind="rh", side_html=rh_side, uid="b2", dark=dark),
        card("Punto de rocío", f"{base['Td']:.1f}", "°C", icon_kind="dew", subtitle_html=dew_sub, uid="b3", dark=dark),
        card("Presión", f"{p_abs_disp}", "hPa", icon_kind="press", subtitle_html=press_sub, uid="b4", dark=dark),
        card("Viento", "—" if is_nan(base["wind"]) else f"{base['wind']:.1f}", "km/h", icon_kind="wind", subtitle_html=wind_sub, side_html=wind_side, uid="b5", dark=dark),
        card("Precipitación hoy", precip_total_str, "mm", icon_kind="rain", subtitle_html=rain_sub, uid="b6", dark=dark),
    ]
    render_grid(cards_basic, cols=3, extra_class="grid-basic")

    # ============================================================
    # NIVEL 2 — DERIVADAS
    # ============================================================
    section_title("Termodinámica")

    cards_derived = [
        card("Humedad específica", f"{q_gkg:.2f}", "g/kg", icon_kind="rh", uid="d1", dark=dark),
        card("Humedad absoluta", f"{rho_v_gm3:.1f}", "g/m³", icon_kind="dew", uid="d7a", dark=dark),
	card("Temp. bulbo húmedo", f"{Tw:.1f}", "°C", icon_kind="dew", uid="tw", dark=dark),
	card("Temp. virtual", f"{Tv:.1f}", "°C", icon_kind="wind", uid="d3", dark=dark),
	card("Temp. equivalente", f"{Te:.1f}", "°C", icon_kind="rain", uid="d4", dark=dark),
        card("Temp. potencial", f"{theta:.1f}", "°C", icon_kind="temp", uid="d2", dark=dark),
        card("Densidad del aire", f"{rho:.3f}", "kg/m³", icon_kind="press", uid="d5", dark=dark),
        card("Base nube LCL", f"{lcl:.0f}", "m", icon_kind="dew", uid="d6", dark=dark),
    ]
    render_grid(cards_derived, cols=4)

except WuError as e:
    if e.kind == "unauthorized":
        st.error("API key inválida o sin permisos para este endpoint.")
    elif e.kind == "notfound":
        st.error("Station ID no encontrado.")
    elif e.kind == "ratelimit":
        st.error("Demasiadas peticiones (rate limit). Prueba a aumentar el refresh.")
    elif e.kind == "timeout":
        st.error("Timeout consultando Weather Underground.")
    elif e.kind == "network":
        st.error("Error de red consultando Weather Underground.")
    elif e.kind == "badjson":
        st.error("Respuesta inesperada de Weather Underground.")
    else:
        st.error("Error consultando Weather Underground.")
except Exception:
    st.error("Error inesperado en la aplicación.")

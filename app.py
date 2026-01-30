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
from api import WuError, fetch_wu_current_session_cached, fetch_daily_timeseries
from models import (
    e_s, q_from_e, theta_celsius, Tv_celsius, Te_celsius,
    lcl_height, msl_to_absolute, air_density, absolute_humidity,
    wet_bulb_celsius,
    priestley_taylor_et0, sky_clarity_index, sky_clarity_label,
    uv_index_label, water_balance, water_balance_label
)
from services import (
    rain_rates_from_total, rain_intensity_label,
    init_pressure_history, push_pressure, pressure_trend_3h
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, render_sidebar
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
      <h1>🛰️ MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 3</span></h1>
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

if not connected:
    st.info("👈 Introduce tu Station ID, API Key y altitud en la barra lateral para empezar")


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
        e = e_s(base["Td"])
        q = q_from_e(e, p_abs)
        q_gkg = q * 1000
        theta = theta_celsius(base["Tc"], p_abs)
        Tv = Tv_celsius(base["Tc"], q)
        Te = Te_celsius(base["Tc"], q)
        Tw = wet_bulb_celsius(base["Tc"], base["RH"])
        rho = air_density(p_abs, Tv)
        rho_v_gm3 = absolute_humidity(e, base["Tc"])
        lcl = lcl_height(base["Tc"], base["Td"])

        # ========== RADIACIÓN ==========
        solar_rad = base.get("solar_radiation", float("nan"))
        uv = base.get("uv", float("nan"))
        
        # Determinar si la estación tiene sensores de radiación
        has_radiation = not is_nan(solar_rad) or not is_nan(uv)
        
        if has_radiation:
            # ET0 por Priestley-Taylor
            et0 = priestley_taylor_et0(solar_rad, base["Tc"], base["RH"], p_abs)
            
            # Claridad del cielo (por ahora sin latitud)
            clarity = sky_clarity_index(solar_rad)
            
            # Balance hídrico
            balance = water_balance(base["precip_total"], et0)
            
            logger.info(f"   Radiación: Solar={solar_rad:.0f} W/m², UV={uv:.1f}")
            logger.info(f"   ET0={et0:.2f} mm/día, Balance={balance:.2f} mm")

        # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
        timeseries = fetch_daily_timeseries(station_id, api_key)
        chart_epochs = timeseries.get("epochs", [])
        chart_temps = timeseries.get("temps", [])
        has_chart_data = timeseries.get("has_data", False)
        
        if has_chart_data:
            logger.info(f"   Gráficos: {len(chart_epochs)} puntos de temperatura")

        # Mostrar metadata solo si hay datos
        st.markdown(
            html_clean(
                f"<div class='meta'>Último dato (local): {es_datetime_from_epoch(base['epoch'])} · Edad: {age_string(base['epoch'])}</div>"
            ),
            unsafe_allow_html=True
        )

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
        st.error(f"❌ Error inesperado: {err}")


# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

section_title("Observados")

# Preparar valores
temp_val = "—" if is_nan(base['Tc']) else f"{base['Tc']:.1f}"
rh_val = "—" if is_nan(base['RH']) else f"{base['RH']:.0f}"
td_val = "—" if is_nan(base['Td']) else f"{base['Td']:.1f}"
wind_val = "—" if is_nan(base["wind"]) else f"{base['wind']:.1f}"
precip_total_str = "—" if is_nan(base["precip_total"]) else f"{base['precip_total']:.1f}"
p_abs_str = str(p_abs_disp)

# Viento
deg = base["wind_dir_deg"]
wind = base["wind"]
if is_nan(wind) or wind == 0.0 or is_nan(deg):
    wind_dir_str = "—"
else:
    short = wind_dir_text(deg)
    wind_dir_str = f"{short} ({deg:.0f}°)"

gust_str = "—" if is_nan(base["gust"]) else f"{base['gust']:.1f}"

# Lluvia
def fmt_rate(x):
    from utils import is_nan as check_nan
    return "—" if check_nan(x) else f"{x:.1f} mm/h"

# Temperatura
fl_str = "—" if is_nan(base["feels_like"]) else f"{base['feels_like']:.1f} °C"
hi_str = "—" if is_nan(base["heat_index"]) else f"{base['heat_index']:.1f} °C"

# Rocío
e_vapor_str = "—" if is_nan(e) else f"{e:.1f}"

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
    
    et0_sub = "<div style='font-size:0.8rem; opacity:0.65; margin-top:2px;'>Priestley-Taylor</div>"
    
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

if connected and has_chart_data:
    section_title("Gráficos")
    
    # Convertir epochs a horas del día (formato HH:MM)
    from datetime import datetime
    times = []
    hours_only = []  # Para el eje X (solo horas enteras)
    for epoch in chart_epochs:
        dt = datetime.fromtimestamp(epoch)
        times.append(dt.strftime("%H:%M"))
        hours_only.append(dt.strftime("%H:00"))
    
    # Crear DataFrame para Plotly
    import pandas as pd
    df_chart = pd.DataFrame({
        "Hora": times,
        "Temperatura": chart_temps
    })
    
    # Calcular rango del eje Y (±4°C de min/max)
    temp_min = min(chart_temps)
    temp_max = max(chart_temps)
    y_min = temp_min - 4
    y_max = temp_max + 4
    
    # Crear gráfico con Plotly
    import plotly.graph_objects as go
    
    fig = go.Figure()
    
    # Línea de temperatura
    fig.add_trace(go.Scatter(
        x=df_chart["Hora"],
        y=df_chart["Temperatura"],
        mode='lines',
        name='Temperatura',
        line=dict(
            color='rgb(255, 107, 107)',
            width=3
        ),
        fill='tozeroy',
        fillcolor='rgba(255, 107, 107, 0.1)'
    ))
    
    # Determinar colores según tema
    if dark:
        text_color = 'rgba(255, 255, 255, 0.92)'
        grid_color = 'rgba(255, 255, 255, 0.15)'
    else:
        text_color = 'rgba(15, 18, 25, 0.92)'
        grid_color = 'rgba(18, 18, 18, 0.12)'
    
    # Layout del gráfico
    fig.update_layout(
        title={
            'text': 'Temperatura del Día',
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 18, 'color': text_color}
        },
        xaxis=dict(
            title='Hora',
            gridcolor=grid_color,
            showgrid=True,
            tickmode='linear',
            dtick=12,  # Mostrar etiqueta cada 12 puntos (≈1 hora si hay datos cada 5 min)
            tickfont=dict(color=text_color)
        ),
        yaxis=dict(
            title='Temperatura (°C)',
            gridcolor=grid_color,
            showgrid=True,
            range=[y_min, y_max],
            tickfont=dict(color=text_color)
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        hovermode='x unified',
        height=400,
        margin=dict(l=60, r=40, t=60, b=60),
        font=dict(
            family='system-ui, -apple-system, "Segoe UI", Roboto, Arial',
            color=text_color
        )
    )
    
    # Mostrar gráfico
    st.plotly_chart(fig, use_container_width=True)

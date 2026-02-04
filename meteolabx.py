"""
MeteoLabx - Panel meteorológico avanzado
Aplicación principal
"""
import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(
    page_title="MeteoLabX",
    page_icon="favicon.png",
    layout="wide"
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
# HEADER
# ============================================================

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>🛰️ MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 4</span></h1>
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
        # Usar concatenación simple para evitar cualquier problema con format specifiers
        st.error("❌ Error inesperado: " + str(err))
        logger.error(f"Error inesperado: {repr(err)}")


# ============================================================
# NAVEGACIÓN CON TABS
# ============================================================

# ============================================================
# SELECTOR DE TABS CON st.radio (estilizado como tabs)
# ============================================================

# Radio buttons estilizados como tabs con underline
active_tab = st.radio(
    "Navegación",
    ["📊 Observación", "📈 Tendencias", "🌡️ Climogramas", "📚 Divulgación"],
    horizontal=True,
    key="active_tab",
    label_visibility="collapsed"
)

# CSS para ocultar círculos y estilizar como tabs
st.markdown("""
<style>
/* Ocultar el círculo del radio */
div[role="radiogroup"] > label > div:first-child {
    display: none;
}
/* Estilo base de cada opción */
div[role="radiogroup"] > label {
    padding: 0.5rem 1rem;
    margin-right: 0.5rem;
    border-bottom: 3px solid transparent;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.2s ease;
}
/* Hover */
div[role="radiogroup"] > label:hover {
    border-bottom: 3px solid rgba(255, 75, 75, 0.3);
}
/* Opción seleccionada */
div[role="radiogroup"] > label:has(input:checked) {
    border-bottom: 3px solid #ff4b4b;
    font-weight: 600;
    color: #ff4b4b;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# TAB 1: OBSERVACIÓN
if active_tab == "📊 Observación":
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

    if connected and has_chart_data:
        section_title("Gráficos")

        from datetime import datetime, timedelta
        import pandas as pd
        import plotly.graph_objects as go

        # --- 1) Construir serie con datetimes reales
        dt_list = []
        temp_list = []
        for epoch, temp in zip(chart_epochs, chart_temps):
            dt = datetime.fromtimestamp(epoch)  # si fuera UTC: datetime.utcfromtimestamp(epoch)
            dt_list.append(dt)
            temp_list.append(temp)

        df_obs = pd.DataFrame({"dt": dt_list, "temp": temp_list}).sort_values("dt")

        # --- 1.5) Alinear timestamps a la rejilla (clave para que el reindex funcione)
        step_minutes = 5
        df_obs["dt"] = pd.to_datetime(df_obs["dt"]).dt.floor(f"{step_minutes}min")

        # Si hay duplicados (varios puntos en el mismo tick), nos quedamos con el último
        df_obs = df_obs.groupby("dt", as_index=False)["temp"].last().sort_values("dt")

        # --- 2) Crear malla completa del día (cada 5 min)
        now_local = datetime.now()
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        grid = pd.date_range(
            start=day_start,
            end=day_end,
            freq=f"{step_minutes}min",
            inclusive="left"
        )

        # --- 3) Reindexar (ahora sí casan los timestamps)
        s = pd.Series(df_obs["temp"].values, index=pd.to_datetime(df_obs["dt"]))
        y = s.reindex(grid)  # sin rellenar; NaN = huecos

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
        
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=grid,
            y=y.values,              # <- pasar valores explícitos evita rarezas
            mode="lines",
            name="Temperatura",
            line=dict(color="rgb(255, 107, 107)", width=3),
            connectgaps=False,
            fill="tozeroy",
            fillcolor="rgba(255, 107, 107, 0.1)"
        ))

        fig.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)

        if dark:
            text_color = "rgba(255, 255, 255, 0.92)"
            grid_color = "rgba(255, 255, 255, 0.15)"
        else:
            text_color = "rgba(15, 18, 25, 0.92)"
            grid_color = "rgba(18, 18, 18, 0.12)"

        fig.update_layout(
            title=dict(
                text="Temperatura del Día",
                x=0.5,
                xanchor="center",
                font=dict(size=18, color=text_color)
            ),
            xaxis=dict(
                title="Hora",
                type="date",
                range=[day_start, day_end],
                tickformat="%H:%M",
                dtick=60 * 60 * 1000,   # 1h
                gridcolor=grid_color,
                showgrid=True,
                tickfont=dict(color=text_color)
            ),
            yaxis=dict(
                title="Temperatura (°C)",
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

        st.plotly_chart(fig, use_container_width=True)

        # --- GRÁFICO DE PRESIÓN DE VAPOR (e vs e_s) ---
        st.markdown("### 💧 Presión de Vapor")
        
        from models.thermodynamics import e_s as calc_e_s, vapor_pressure
        
        # Obtener datos de session_state
        chart_humidities = st.session_state.get("chart_humidities", [])
        
        logger.info(f"   Gráfico e vs e_s: epochs={len(chart_epochs)}, temps={len(chart_temps)}, humidities={len(chart_humidities)}")
        
        # Preparar datos
        vapor_times = []
        e_values = []
        e_sat_values = []
        
        for epoch, temp, rh in zip(chart_epochs, chart_temps, chart_humidities):
            if math.isnan(temp) or math.isnan(rh):
                continue
            
            dt = datetime.fromtimestamp(epoch)
            vapor_times.append(dt)
            
            # Calcular e y e_s
            e = vapor_pressure(temp, rh)
            e_sat = calc_e_s(temp)
            
            e_values.append(e)
            e_sat_values.append(e_sat)
        
        logger.info(f"   Gráfico e vs e_s: {len(vapor_times)} puntos válidos procesados")
        
        # Alinear a rejilla de 5 min
        df_vapor = pd.DataFrame({
            "dt": vapor_times,
            "e": e_values,
            "e_s": e_sat_values
        })
        df_vapor["dt"] = pd.to_datetime(df_vapor["dt"]).dt.floor("5min")
        df_vapor = df_vapor.groupby("dt", as_index=False).last()
        
        # Reindexar a la malla completa del día
        s_e = pd.Series(df_vapor["e"].values, index=pd.to_datetime(df_vapor["dt"]))
        s_e_s = pd.Series(df_vapor["e_s"].values, index=pd.to_datetime(df_vapor["dt"]))
        y_e = s_e.reindex(grid)
        y_e_s = s_e_s.reindex(grid)
        
        # Crear gráfico con Plotly
        fig_vapor = go.Figure()
        
        # Línea de e (presión de vapor)
        fig_vapor.add_trace(go.Scatter(
            x=grid,
            y=y_e.values,
            mode="lines",
            name="e (Presión de vapor)",
            line=dict(color="rgb(52, 152, 219)", width=3),  # Azul
            connectgaps=False,
            hovertemplate="<b>%{x|%H:%M}</b><br>e: %{y:.2f} hPa<extra></extra>"
        ))
        
        # Línea de e_s (presión de saturación)
        fig_vapor.add_trace(go.Scatter(
            x=grid,
            y=y_e_s.values,
            mode="lines",
            name="e_s (Presión de saturación)",
            line=dict(color="rgb(231, 76, 60)", width=2, dash="dash"),  # Rojo discontinuo
            connectgaps=False,
            hovertemplate="<b>%{x|%H:%M}</b><br>e_s: %{y:.2f} hPa<extra></extra>"
        ))
        
        # Línea vertical en hora actual
        fig_vapor.add_vline(x=now_local, line_width=1, line_dash="dot", opacity=0.6)
        
        # Layout
        fig_vapor.update_layout(
            title=dict(
                text="Presión de Vapor y Saturación",
                x=0.5,
                xanchor="center",
                font=dict(size=18, color=text_color)
            ),
            xaxis=dict(
                title="Hora",
                type="date",
                range=[day_start, day_end],
                tickformat="%H:%M",
                dtick=60 * 60 * 1000,  # Cada 1 hora
                showgrid=True,
                gridcolor=grid_color,
                tickfont=dict(color=text_color)
            ),
            yaxis=dict(
                title="Presión de vapor (hPa)",
                showgrid=True,
                gridcolor=grid_color,
                tickfont=dict(color=text_color)
            ),
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=text_color),
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5
            ),
            margin=dict(l=60, r=40, t=60, b=60),
            height=400,
            annotations=[dict(
                text="meteolabx.com",
                xref="paper", yref="paper",
                x=0.98, y=0.02,
                xanchor="right", yanchor="bottom",
                showarrow=False,
                font=dict(size=10, color="rgba(128,128,128,0.5)")
            )]
        )
        
        st.plotly_chart(fig_vapor, use_container_width=True, config={"displayModeBar": False})

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "📈 Tendencias":
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

            # Colores según tema
            if dark:
                text_color = "rgba(255, 255, 255, 0.92)"
                grid_color = "rgba(255, 255, 255, 0.15)"
            else:
                text_color = "rgba(15, 18, 25, 0.92)"
                grid_color = "rgba(18, 18, 18, 0.12)"

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
                    xaxis=dict(title="Hora", type="date", range=[day_start, day_end],
                              tickformat=tickformat, dtick=dtick_ms,
                              gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                    yaxis=dict(title="dθe/dt (K/h)", range=y_range_theta_e,
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

                st.plotly_chart(fig_theta_e, use_container_width=True)
            except Exception as e:
                st.error("Error al calcular tendencia de θe: " + str(e))
                logger.error(f"Error tendencia θe: {repr(e)}")

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
                    xaxis=dict(title="Hora", type="date", range=[day_start, day_end],
                              tickformat=tickformat, dtick=dtick_ms,
                              gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                    yaxis=dict(title="de/dt (hPa/h)", range=y_range_e,
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

                st.plotly_chart(fig_e, use_container_width=True)
            except Exception as e:
                st.error("Error al calcular tendencia de e: " + str(e))
                logger.error(f"Error tendencia e: {repr(e)}")

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
                    xaxis=dict(title="Hora", type="date", range=[day_start, day_end],
                              tickformat=tickformat, dtick=dtick_ms,
                              gridcolor=grid_color, showgrid=True, tickfont=dict(color=text_color)),
                    yaxis=dict(title="dp/dt (hPa/h)", range=y_range_p,
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

                st.plotly_chart(fig_p, use_container_width=True)
            except Exception as e:
                st.error("Error al calcular tendencia de presión: " + str(e))
                logger.error(f"Error tendencia presión: {repr(e)}")


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
        st_autorefresh(interval=REFRESH_SECONDS * 1000, key="refresh_data")


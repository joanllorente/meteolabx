import streamlit as st
import requests
import math
import time
import base64
import textwrap
import json
import streamlit.components.v1 as components
from streamlit_local_storage import LocalStorage
from datetime import datetime
from streamlit_autorefresh import st_autorefresh
from collections import deque
from typing import Optional

# ============================================================
# CONFIG
# ============================================================

REFRESH_SECONDS = 20          # Público: mejor 60s (menos carga/abuso)
WIND_DIR_OFFSET_DEG = 30.0
WU_URL = "https://api.weather.com/v2/pws/observations/current"


# LocalStorage keys (recordar en este dispositivo)
LS_STATION = "meteolabx_active_station"
LS_APIKEY  = "meteolabx_active_key"
LS_Z       = "meteolabx_active_z"

# Browser persistent storage (per device/browser)
localS = LocalStorage()

# ============================================================
# UTILIDADES
# ============================================================

def html_clean(s: str) -> str:
    return textwrap.dedent(s).strip()

def is_nan(x):
    return x != x


# Local storage handled via streamlit-local-storage (see LocalStorage() below)

def normalize_text_input(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def set_local_storage(item_key: str, value, key_suffix: str) -> None:
    localS.setItem(item_key, value, key=f"ls-{item_key}-{key_suffix}")

def icon_svg(kind: str, uid: str, dark: bool = False) -> str:
    stroke = "rgba(255,255,255,0.55)" if dark else "rgba(0,0,0,0.12)"
    glow1 = "rgba(255,255,255,0.35)" if dark else "rgba(255,255,255,0.55)"
    glow2 = "rgba(0,0,0,0.22)" if dark else "rgba(0,0,0,0.10)"
    g = lambda name: f"{name}-{uid}"

    if kind == "temp":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#FFD56A"/>
              <stop offset="0.55" stop-color="#FF8A5B"/>
              <stop offset="1" stop-color="#5E8BFF"/>
            </linearGradient>
            <filter id="{g('glow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="3.2" result="b"/>
              <feColorMatrix in="b" type="matrix"
                values="1 0 0 0 0
                        0 1 0 0 0
                        0 0 1 0 0
                        0 0 0 0.45 0" result="g"/>
              <feMerge>
                <feMergeNode in="g"/>
                <feMergeNode in="SourceGraphic"/>
              </feMerge>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <path d="M17 35c0 5.5 4.5 10 10 10s10-4.5 10-10c0-3.1-1.5-5.9-3.8-7.7V16.5C33.2 11.8 30.4 8 27 8s-6.2 3.8-6.2 8.5v10.8C18.5 29.1 17 31.9 17 35z"
                fill="white" opacity="0.28" filter="url(#{g('glow')})"/>
          <path d="M27 12c1.5 0 2.7 2 2.7 4.5V32a5.5 5.5 0 1 1-5.4 0V16.5C24.3 14 25.5 12 27 12z"
                fill="white" opacity="0.85"/>
          <circle cx="29" cy="14.6" r="1.0" fill="{glow1}" opacity="0.9"/>
        </svg>
        """)

    if kind == "dew":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#B9E6FF"/>
              <stop offset="1" stop-color="#5AA8FF"/>
            </linearGradient>
            <radialGradient id="{g('drop')}" cx="35%" cy="25%" r="70%">
              <stop offset="0" stop-color="#E9F7FF"/>
              <stop offset="0.5" stop-color="#7CC7FF"/>
              <stop offset="1" stop-color="#2F7BFF"/>
            </radialGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="5" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <path filter="url(#{g('shadow')})"
            d="M27 10c0 0 11 14 11 21.5C38 38.4 33.1 44 27 44s-11-5.6-11-12.5C16 24 27 10 27 10z"
            fill="url(#{g('drop')})"/>
          <path d="M22 27c2-3 6-5 10-5" stroke="rgba(255,255,255,0.6)" stroke-width="3" stroke-linecap="round"/>
          <path d="M20.5 34.5c2 2.5 6 3.5 9.5 2.5" stroke="rgba(255,255,255,0.45)" stroke-width="3" stroke-linecap="round"/>
        </svg>
        """)

    if kind == "rh":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#73E0FF"/>
              <stop offset="1" stop-color="#2F80ED"/>
            </linearGradient>
            <linearGradient id="{g('ring')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="rgba(255,255,255,0.95)"/>
              <stop offset="1" stop-color="rgba(255,255,255,0.55)"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <circle cx="27" cy="27" r="15.5" fill="rgba(255,255,255,0.18)"/>
            <circle cx="27" cy="27" r="15.5" fill="none" stroke="url(#{g('ring')})" stroke-width="3.5" opacity="0.75"/>
            <path d="M27 15.5 A11.5 11.5 0 0 1 38.5 27"
                  fill="none" stroke="rgba(255,255,255,0.95)" stroke-width="4" stroke-linecap="round"/>
            <circle cx="38.5" cy="27" r="2.2" fill="white" opacity="0.9"/>
          </g>
        </svg>
        """)

    if kind == "press":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#C8A8FF"/>
              <stop offset="1" stop-color="#FFB6D5"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <circle cx="27" cy="28" r="14.5" fill="rgba(255,255,255,0.18)"/>
            <circle cx="27" cy="28" r="14.5" fill="none" stroke="rgba(255,255,255,0.75)" stroke-width="3"/>
            <path d="M27 28 L36 19" stroke="rgba(255,255,255,0.95)" stroke-width="3.2" stroke-linecap="round"/>
            <circle cx="27" cy="28" r="3" fill="white" opacity="0.9"/>
            <path d="M16 28a11 11 0 0 0 22 0" stroke="{stroke}" stroke-width="2.2" stroke-linecap="round"/>
          </g>
        </svg>
        """)

    if kind == "wind":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#7DFFB5"/>
              <stop offset="1" stop-color="#48C6EF"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round">
            <path d="M14 26c8 0 10-6 18-6 5 0 8 2.5 8 6" stroke-width="3.2"/>
            <path d="M14 32c10 0 12-4 20-4 4 0 7 2 7 5" stroke-width="3.0" opacity="0.9"/>
            <path d="M14 20c7 0 9-3 14-3 3 0 5 1.5 5 4" stroke-width="2.6" opacity="0.75"/>
          </g>
        </svg>
        """)

    if kind == "rain":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#FF8A65"/>
              <stop offset="1" stop-color="#FFD180"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <path d="M18 30c-2.8 0-5-2.2-5-5 0-2.4 1.6-4.4 3.8-4.9
                     1-3.4 4.1-5.9 7.8-5.9 3.6 0 6.7 2.3 7.7 5.6
                     2.9 0.2 5.2 2.6 5.2 5.6 0 3.1-2.5 5.6-5.6 5.6H18z"
                  fill="rgba(255,255,255,0.92)"/>
            <path d="M22 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
            <path d="M29 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
            <path d="M36 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
          </g>
        </svg>
        """)

    return ""

def icon_img(kind: str, uid: str, dark: bool = False) -> str:
    svg = icon_svg(kind, uid=uid, dark=dark)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"<img class='icon-img' src='data:image/svg+xml;base64,{b64}'/>"

def card(title, value, unit="", icon_kind="temp", subtitle_html="", uid="x", dark=False):
    unit_html = f"<span class='unit'>{unit}</span>" if unit else ""
    sub_html = f"<div class='subtitle'>{subtitle_html}</div>" if subtitle_html else ""
    icon_html = icon_img(icon_kind, uid=uid, dark=dark)

    return html_clean(f"""
  <div class="card card-h">
    <div class="icon-col">
      <div class="icon big">{icon_html}</div>
    </div>

    <div class="content-col">
      <div class="card-title">{title}</div>
      <div class="card-value">{value}{unit_html}</div>
      {sub_html}
    </div>
  </div>
""")

def section_title(text):
    st.markdown(f"<div class='section-title'>{text}</div>", unsafe_allow_html=True)

def render_grid(cards, cols=3, extra_class=""):
    cards_html = "".join(cards)
    html = f"<div class='grid grid-{cols} {extra_class}'>{cards_html}</div>"
    st.markdown(html, unsafe_allow_html=True)

def rain_intensity_label(rate_mm_h):
    if is_nan(rate_mm_h) or rate_mm_h <= 0:
        return "Sin precipitación"
    elif rate_mm_h < 0.4:
        return "Traza de precipitación"
    elif rate_mm_h < 1.0:
        return "Lluvia muy débil"
    elif rate_mm_h < 2.5:
        return "Lluvia débil"
    elif rate_mm_h < 6.5:
        return "Lluvia ligera"
    elif rate_mm_h < 16:
        return "Lluvia moderada"
    elif rate_mm_h < 40:
        return "Lluvia fuerte"
    elif rate_mm_h < 100:
        return "Lluvia muy fuerte"
    else:
        return "Lluvia torrencial"

def es_datetime_from_epoch(epoch):
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%d-%m-%Y %H:%M:%S")

def init_pressure_history(maxlen=720):
    if "p_hist" not in st.session_state:
        st.session_state.p_hist = deque(maxlen=maxlen)

def push_pressure(p_hpa: float, epoch: int):
    if p_hpa != p_hpa:
        return
    hist = st.session_state.p_hist
    if len(hist) == 0 or epoch > hist[-1][0]:
        hist.append((epoch, p_hpa))

def pressure_trend_3h():
    hist = st.session_state.p_hist
    if len(hist) < 2:
        return (float("nan"), float("nan"), "—", "•")

    t_now, p_now = hist[-1]
    target = t_now - 3*3600

    t_old, p_old = hist[0]
    for (t, p) in hist:
        if t <= target:
            t_old, p_old = t, p
        else:
            break

    dt = t_now - t_old
    if dt <= 0:
        return (float("nan"), float("nan"), "—", "•")

    dp = p_now - p_old
    rate_h = dp / (dt / 3600.0)

    if abs(dp) < 0.2:
        return (dp, rate_h, "Estable", "→")
    elif dp > 0:
        return (dp, rate_h, "Subiendo", "↗")
    else:
        return (dp, rate_h, "Bajando", "↘")

def fmt_hpa(x, nd=1):
    return "—" if x != x else f"{x:.{nd}f}"

def buoyancy_label(B_cms2):
    if is_nan(B_cms2):
        return "—"
    if B_cms2 > 5:
        return "Muy inestable"
    elif B_cms2 > 2:
        return "Inestabilidad"
    elif B_cms2 > 0.5:
        return "Ligeramente inestable"
    elif B_cms2 > -0.5:
        return "Estabilidad neutra"
    elif B_cms2 > -2:
        return "Estable"
    else:
        return "Muy estable"

def wind_dir_text(deg):
    if deg != deg:
        return "—"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    i = int((deg + 11.25) // 22.5) % 16
    return dirs[i]

def wind_name_cat(deg):
    if deg != deg:
        return "—"
    deg = deg % 360
    if deg >= 337.5 or deg < 22.5:
        return "Tramuntana"
    elif 22.5 <= deg < 67.5:
        return "Gregal"
    elif 67.5 <= deg < 112.5:
        return "Llevant"
    elif 112.5 <= deg < 157.5:
        return "Xaloc"
    elif 157.5 <= deg < 202.5:
        return "Migjorn"
    elif 202.5 <= deg < 247.5:
        return "Garbí"
    elif 247.5 <= deg < 292.5:
        return "Ponent"
    elif 292.5 <= deg < 337.5:
        return "Mestral"

def age_string(epoch):
    age = int(time.time() - epoch)
    if age < 60:
        return f"{age} s"
    return f"{age//60} min {age%60} s"

def quantize_rain_mm_wu(mm_wu: float) -> float:
    if is_nan(mm_wu):
        return float("nan")
    mm_corr = mm_wu * 1.0049
    tips = round(mm_corr / 0.4)
    return tips * 0.4

def e_s(T):
    return 6.112 * math.exp((17.67 * T) / (T + 243.5))

def q_from_e(e, p):
    return 0.622 * e / (p - 0.378 * e)

def theta_celsius(Tc, p):
    Tk = Tc + 273.15
    return Tk * (1000 / p) ** 0.286 - 273.15

def Tv_celsius(Tc, q):
    Tk = Tc + 273.15
    return Tk * (1 + 0.61 * q) - 273.15

def Te_celsius(Tc, q):
    Tk = Tc + 273.15
    return Tk * math.exp((2.5e6 * q) / (1004 * Tk)) - 273.15

def lcl_height(Tc, Td):
    return 125 * (Tc - Td)

def ensure_rain_history():
    if "rain_hist" not in st.session_state:
        st.session_state.rain_hist = deque(maxlen=2000)
    if "last_tip" not in st.session_state:
        st.session_state.last_tip = None
    if "prev_tip" not in st.session_state:
        st.session_state.prev_tip = None

def reset_rain_history():
    st.session_state.rain_hist = deque(maxlen=2000)
    st.session_state.last_tip = None
    st.session_state.prev_tip = None

def rain_rates_from_total(precip_total_mm: float, now_ts: float):
    ensure_rain_history()

    if is_nan(precip_total_mm):
        return float("nan"), float("nan"), float("nan")

    hist = st.session_state.rain_hist

    if hist and precip_total_mm + 1e-6 < hist[-1][1]:
        reset_rain_history()
        hist = st.session_state.rain_hist

    if (not hist) or abs(precip_total_mm - hist[-1][1]) > 1e-9:
        hist.append((now_ts, precip_total_mm))
        st.session_state.prev_tip = st.session_state.last_tip
        st.session_state.last_tip = (now_ts, precip_total_mm)

    inst = float("nan")
    a = st.session_state.prev_tip
    b = st.session_state.last_tip
    if a is not None and b is not None:
        t0, p0 = a
        t1, p1 = b
        dp = p1 - p0
        dt = t1 - t0
        if dp > 0 and dt > 0:
            inst = (dp / dt) * 3600.0

    def window_rate(window_s: float):
        if not hist:
            return float("nan")
        t_now, p_now = now_ts, precip_total_mm
        target = t_now - window_s
        if hist[0][0] > target:
            return float("nan")
        t_old, p_old = hist[0]
        for t_i, p_i in reversed(hist):
            if t_i <= target:
                t_old, p_old = t_i, p_i
                break
        dt = t_now - t_old
        dp = p_now - p_old
        if dt <= 0 or dp < 0:
            return float("nan")
        return (dp / dt) * 3600.0

    r1 = window_rate(60.0)
    r5 = window_rate(300.0)

    return inst, r1, r5

# ============================================================
# WEATHER UNDERGROUND
# ============================================================

class WuError(Exception):
    """Error controlado para no filtrar URLs/keys."""
    def __init__(self, kind: str, status_code: Optional[int] = None):
        super().__init__(kind)
        self.kind = kind
        self.status_code = status_code

def fetch_wu_current(station_id: str, api_key: str):
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal"
    }

    HI_MIN = 25.0

    try:
        r = requests.get(WU_URL, params=params, timeout=15)
    except requests.Timeout:
        raise WuError("timeout")
    except requests.RequestException:
        raise WuError("network")

    if r.status_code == 401:
        raise WuError("unauthorized", 401)
    if r.status_code == 404:
        raise WuError("notfound", 404)
    if r.status_code == 429:
        raise WuError("ratelimit", 429)
    if r.status_code >= 400:
        raise WuError("http", r.status_code)

    try:
        data = r.json()
        obs = data["observations"][0]
        m = obs["metric"]
    except Exception:
        raise WuError("badjson")

    raw_dir = obs.get("winddir", float("nan"))
    if raw_dir == raw_dir:
        wind_dir_deg = (raw_dir + WIND_DIR_OFFSET_DEG) % 360
    else:
        wind_dir_deg = float("nan")

    Tc = float(m["temp"])
    RH = float(obs["humidity"])
    p_hpa = float(m["pressure"])
    Td = float(m["dewpt"])

    heat_index = m.get("heatIndex", None)
    wind_chill = m.get("windChill", None)

    heat_index = float(heat_index) if heat_index is not None else float("nan")
    wind_chill = float(wind_chill) if wind_chill is not None else float("nan")

    wind = float(m.get("windSpeed", float("nan")))
    gust = float(m.get("windGust", float("nan")))
    precip_rate = float(m.get("precipRate", float("nan")))
    precip_total_raw = float(m.get("precipTotal", float("nan")))
    precip_total = quantize_rain_mm_wu(precip_total_raw)

    if Tc < HI_MIN:
        heat_index = float("nan")

    if not (Tc <= 10 and (not is_nan(wind)) and wind >= 4.8):
        wind_chill = float("nan")

    if not is_nan(wind_chill):
        feels_like = wind_chill
    elif not is_nan(heat_index):
        feels_like = heat_index
    else:
        feels_like = Tc

    return {
        "Tc": Tc,
        "RH": RH,
        "p_hpa": p_hpa,
        "Td": Td,
        "wind": wind,
        "gust": gust,
        "feels_like": feels_like,
        "heat_index": heat_index,
        "wind_chill": wind_chill,
        "precip_rate": precip_rate,
        "precip_total": precip_total,
        "wind_dir_deg": wind_dir_deg,
        "epoch": obs["epoch"],
        "time_local": obs["obsTimeLocal"],
        "time_utc": obs["obsTimeUtc"]
    }

def fetch_wu_current_session_cached(station_id: str, api_key: str, ttl_s: int):
    """
    Cache por SESIÓN (no global): evita que la api_key acabe en caches compartidos.
    """
    if "wu_cache" not in st.session_state:
        st.session_state.wu_cache = {}  # key -> {"t": float, "data": dict}

    cache = st.session_state.wu_cache
    k = (station_id, api_key)

    now = time.time()
    if k in cache:
        age = now - cache[k]["t"]
        if age < ttl_s:
            return cache[k]["data"]

    data = fetch_wu_current(station_id, api_key)
    cache[k] = {"t": now, "data": data}
    return data

# ============================================================
# STREAMLIT APP
# ============================================================

st.set_page_config(page_title="MeteoLabx", layout="wide")

# ---- Theme auto (oscuro de noche) + toggle
now = datetime.now()
auto_dark = (now.hour >= 20) or (now.hour <= 7)

# ------------------------------------------------------------
# Prefill desde el navegador (localStorage) usando componente
# Nota: algunos componentes necesitan 1-2 renders para poblar st.session_state.
# -----------------------------------------------------------

saved_station = localS.getItem(LS_STATION)
saved_key     = localS.getItem(LS_APIKEY)
saved_z       = localS.getItem(LS_Z)

active_station = st.session_state.get("active_station", "")
active_key = st.session_state.get("active_key", "")
active_z = st.session_state.get("active_z", "0")

if not active_station and saved_station:
    st.session_state["active_station"] = saved_station
if not active_key and saved_key:
    st.session_state["active_key"] = saved_key
if (not str(active_z).strip() or active_z == "0") and saved_z:
    st.session_state["active_z"] = normalize_text_input(saved_z)

st.session_state["active_z"] = normalize_text_input(st.session_state.get("active_z"))

st.sidebar.title("⚙️ Ajustes")
theme_mode = st.sidebar.radio("Tema", ["Auto", "Claro", "Oscuro"], index=0)

# ---- Sidebar: Conectar estación
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔌 Conectar estación")

# Inputs vinculados a la configuración activa (se pueden recordar en el navegador)
st.sidebar.text_input("Station ID (WU)", key="active_station", placeholder="Introducir ID")
st.sidebar.text_input("API Key (WU)", key="active_key", type="password", placeholder="Pega aquí tu API key")
st.sidebar.text_input("Altitud (m)", key="active_z", placeholder="Ej: 12.5")

st.sidebar.caption("Este panel consulta Weather Underground usando tu propia API key. No se almacena en disco.")

# ---- Recordar en este dispositivo (localStorage)
st.sidebar.markdown("---")
remember_device = st.sidebar.checkbox("Recordar en este dispositivo", value=True)
st.sidebar.caption("⚠️ Si es un ordenador compartido, desactívalo o pulsa ‘Olvidar’ al terminar.")

cS, cF = st.sidebar.columns(2)
with cS:
    save_clicked = st.button("💾 Guardar", use_container_width=True)
with cF:
    forget_clicked = st.button("🧹 Olvidar", use_container_width=True)

if save_clicked:
    if remember_device:
        set_local_storage(LS_STATION, st.session_state["active_station"], "save-station")
        set_local_storage(LS_APIKEY,  st.session_state["active_key"], "save-key")
        set_local_storage(LS_Z,       str(st.session_state["active_z"]), "save-z")
        st.sidebar.success("Guardado en este dispositivo ✅")
    else:
        st.sidebar.info("Activa ‘Recordar en este dispositivo’ para guardar.")

if forget_clicked:
    set_local_storage(LS_STATION, "", "forget-station")
    set_local_storage(LS_APIKEY,  "", "forget-key")
    set_local_storage(LS_Z,       "", "forget-z")
    st.session_state["active_station"] = ""
    st.session_state["active_key"] = ""
    st.session_state["active_z"] = "0"
    st.sidebar.success("Borrado ✅")
    st.rerun()

# estado conectado
if "connected" not in st.session_state:
    st.session_state["connected"] = False

colA, colB = st.sidebar.columns(2)
with colA:
    connect_clicked = st.button("✅ Conectar", use_container_width=True)
with colB:
    disconnect_clicked = st.button("⏹️ Desconectar", use_container_width=True)

if disconnect_clicked:
    st.session_state["connected"] = False

if connect_clicked:
    station = str(st.session_state.get("active_station", "")).strip()
    key = str(st.session_state.get("active_key", "")).strip()
    z_raw = str(st.session_state.get("active_z", "0")).strip()

    if not station or not key:
        st.sidebar.error("Falta Station ID o API key.")
    else:
        try:
            float(z_raw)  # validación
        except Exception:
            st.sidebar.error("Altitud inválida. Usa un número (ej: 12.5)")
        else:
            st.session_state["connected"] = True
            # autosave opcional al conectar
            if remember_device:
                set_local_storage(LS_STATION, station, "connect-station")
                set_local_storage(LS_APIKEY, key, "connect-key")
                set_local_storage(LS_Z, z_raw, "connect-z")

if st.session_state.get("connected"):
    st.sidebar.success(f"Conectado: {st.session_state.get('active_station','')}")
else:
    st.sidebar.info("No conectado")

# ---- dark mode
if theme_mode == "Auto":
    dark = auto_dark
elif theme_mode == "Oscuro":
    dark = True
else:
    dark = False

# ---- CSS (claro / oscuro)
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

# refresco datos SOLO cuando está conectado
if st.session_state.get("connected", False):
    st_autorefresh(interval=REFRESH_SECONDS * 1000, key="refresh_data")

# refresco rápido para la edad (no llama a WU)
st_autorefresh(interval=1000, key="refresh_age")

st.markdown(
    html_clean(f"""
    <div class="header">
      <h1>🛰️ MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Beta 1</span></h1>
      <div class="meta">
        Versión beta — la interfaz y las funciones pueden cambiar ·
        Tema: {"Oscuro" if dark else "Claro"} · Refresh: {REFRESH_SECONDS}s
      </div>
    </div>
    """),
    unsafe_allow_html=True
)

if not st.session_state.get("connected", False):
    st.info("Introduce Station ID, API key y altitud en la barra lateral y pulsa **Conectar**.")
    st.stop()

station_id = st.session_state["active_station"]
api_key = st.session_state["active_key"]
z = float(st.session_state["active_z"])

try:
    # Cache seguro por sesión (no global)
    base = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)

    # ============================================================
    # TIEMPO DEL DATO (usar epoch de WU)
    # ============================================================
    now_ts = time.time()  # para lluvia (historial por tick de la app)

    # ============================================================
    # LLUVIA
    # ============================================================
    inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], now_ts)
    inst_label = rain_intensity_label(inst_mm_h)

    # ============================================================
    # PRESIÓN
    # ============================================================
    p_msl = float(base["p_hpa"])

    g0 = 9.80665
    Rd = 287.05

    Tk = base["Tc"] + 273.15
    p_abs = p_msl * math.exp(-g0 * float(z) / (Rd * Tk))

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

    # ============================================================
    # TERMODINÁMICA
    # ============================================================
    e = e_s(base["Td"])
    q = q_from_e(e, p_abs)
    q_gkg = q * 1000

    theta = theta_celsius(base["Tc"], p_abs)
    Tv = Tv_celsius(base["Tc"], q)
    Te = Te_celsius(base["Tc"], q)

    Tvk = Tv + 273.15

    # ============================================================
    # FLOTABILIDAD
    # ============================================================
    B = g0 * (Tvk - Tk) / Tk
    B_cms2 = B * 100
    B_label = buoyancy_label(B_cms2)

    # ============================================================
    # DENSIDAD DEL AIRE (con Tv)
    # ============================================================
    rho = (p_abs * 100) / (Rd * Tvk)

    # ============================================================
    # HUMEDAD ABSOLUTA
    # ============================================================
    Rv = 461.5
    rho_v_gm3 = ((e * 100) / (Rv * Tk)) * 1000

    # ============================================================
    # LCL
    # ============================================================
    lcl = lcl_height(base["Tc"], base["Td"])

    st.markdown(
        html_clean(
            f"<div class='meta'>Último dato (local): {es_datetime_from_epoch(base['epoch'])} · Edad: {age_string(base['epoch'])}</div>"
        ),
        unsafe_allow_html=True
    )

    # ============================================================
    # NIVEL 1 — BÁSICOS
    # ============================================================
    section_title("Básicos")

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

    dew_sub = f"""
    <div>Presión de vapor: <b>{e:.1f} hPa</b></div>
    """

    cards_basic = [
        card("Temperatura", f"{base['Tc']:.1f}", "°C", icon_kind="temp", subtitle_html=temp_sub, uid="b1", dark=dark),
        card("Humedad relativa", f"{base['RH']:.0f}", "%", icon_kind="rh", uid="b2", dark=dark),
        card("Punto de rocío", f"{base['Td']:.1f}", "°C", icon_kind="dew", subtitle_html=dew_sub, uid="b3", dark=dark),
        card("Presión", f"{p_abs_disp}", "hPa", icon_kind="press", subtitle_html=press_sub, uid="b4", dark=dark),
        card("Viento", "—" if is_nan(base["wind"]) else f"{base['wind']:.1f}", "km/h", icon_kind="wind", subtitle_html=wind_sub, uid="b5", dark=dark),
        card("Precipitación hoy", precip_total_str, "mm", icon_kind="rain", subtitle_html=rain_sub, uid="b6", dark=dark),
    ]
    render_grid(cards_basic, cols=3, extra_class="grid-basic")

    # ============================================================
    # NIVEL 2 — DERIVADAS
    # ============================================================
    section_title("Derivadas / Termodinámica")

    cards_derived = [
        card("Humedad específica", f"{q_gkg:.2f}", "g/kg", icon_kind="rh", uid="d1", dark=dark),
        card("Humedad absoluta", f"{rho_v_gm3:.1f}", "g/m³", icon_kind="dew", uid="d7a", dark=dark),
        card("Temp. potencial", f"{theta:.1f}", "°C", icon_kind="temp", uid="d2", dark=dark),
        card("Temp. virtual", f"{Tv:.1f}", "°C", icon_kind="wind", uid="d3", dark=dark),
        card("Temp. equivalente", f"{Te:.1f}", "°C", icon_kind="rain", uid="d4", dark=dark),
        card("Densidad del aire", f"{rho:.3f}", "kg/m³", icon_kind="press", uid="d5", dark=dark),
        card("Flotabilidad", f"{B_cms2:.2f}", "cm/s²", icon_kind="wind", subtitle_html=f"<div>{B_label}</div>", uid="d7b", dark=dark),
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
    # No mostramos detalles para evitar filtrar cosas sensibles en un despliegue público
    st.error("Error inesperado en la aplicación.")

"""
Cliente API Weather Underground - VERSIÓN HÍBRIDA V3 (CORRECTA)
Procesa TODAS las observaciones de /all/1day para obtener extremos del día completo
"""
import streamlit as st
import requests
import logging
import time
from collections import OrderedDict
from typing import Dict, Optional
from config import (
    WIND_DIR_OFFSET_DEG, WU_TIMEOUT_SECONDS, MAX_CACHE_SIZE,
    HEAT_INDEX_MIN_TEMP, WIND_CHILL_MAX_TEMP, WIND_CHILL_MIN_SPEED,
    RAIN_QUANTIZE_CORRECTION, RAIN_TIP_RESOLUTION
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WU_URL_CURRENT = "https://api.weather.com/v2/pws/observations/current"
WU_URL_DAILY = "https://api.weather.com/v2/pws/observations/all/1day"


class WuError(Exception):
    def __init__(self, kind: str, status_code: Optional[int] = None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(kind)


def is_nan(x):
    return x != x


def safe_float(val, default=float("nan")):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def quantize_rain_mm_wu(mm_wu: float) -> float:
    if is_nan(mm_wu):
        return float("nan")
    mm_corr = mm_wu * RAIN_QUANTIZE_CORRECTION
    tips = round(mm_corr / RAIN_TIP_RESOLUTION)
    return tips * RAIN_TIP_RESOLUTION


def fetch_daily_timeseries(station_id: str, api_key: str) -> Dict:
    """
    Obtiene series temporales del día completo para gráficos
    Retorna listas de timestamps y valores para cada variable
    """
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal"
    }

    logger.info(f"Consultando series temporales (/all/1day)")

    try:
        r = requests.get(WU_URL_DAILY, params=params, timeout=WU_TIMEOUT_SECONDS)
        
        if r.status_code != 200:
            logger.warning(f"HTTP {r.status_code} en /all/1day para series temporales")
            return {
                "epochs": [],
                "temps": [],
                "has_data": False
            }
        
        data = r.json()
        observations = data.get("observations", [])
        
        if not observations:
            logger.warning("Sin observaciones en /all/1day para series temporales")
            return {
                "epochs": [],
                "temps": [],
                "has_data": False
            }
        
        # Extraer series temporales
        epochs = []
        temps = []
        
        for obs in observations:
            epoch = obs.get("epoch", 0)
            if epoch <= 0:
                continue
            
            # Usar tempAvg si existe, sino tempHigh, sino tempLow
            metric = obs.get("metric", {})
            temp = safe_float(metric.get("tempAvg"))
            if is_nan(temp):
                temp = safe_float(metric.get("tempHigh"))
            if is_nan(temp):
                temp = safe_float(metric.get("tempLow"))
            
            # Solo añadir si tenemos temperatura válida
            if not is_nan(temp):
                epochs.append(epoch)
                temps.append(temp)
        
        logger.info(f"Series temporales: {len(epochs)} puntos obtenidos")
        
        return {
            "epochs": epochs,
            "temps": temps,
            "has_data": len(epochs) > 0
        }
        
    except Exception as e:
        logger.warning(f"Error obteniendo series temporales: {e}")
        return {
            "epochs": [],
            "temps": [],
            "has_data": False
        }


def fetch_extremes_from_daily(station_id: str, api_key: str) -> Dict:
    """
    Obtiene extremos del DÍA COMPLETO procesando TODAS las observaciones
    También obtiene presión de hace ~3 horas para calcular tendencia real
    """
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal"
    }

    logger.info(f"Consultando extremos diarios (/all/1day)")

    try:
        r = requests.get(WU_URL_DAILY, params=params, timeout=WU_TIMEOUT_SECONDS)
        
        if r.status_code != 200:
            logger.warning(f"HTTP {r.status_code} en /all/1day")
            return {
                "temp_max": float("nan"),
                "temp_min": float("nan"),
                "rh_max": float("nan"),
                "rh_min": float("nan"),
                "gust_max": float("nan"),
                "pressure_3h_ago": float("nan"),
                "epoch_3h_ago": float("nan"),
            }
        
        data = r.json()
        observations = data.get("observations", [])
        
        if not observations:
            logger.warning("Sin observaciones en /all/1day")
            return {
                "temp_max": float("nan"),
                "temp_min": float("nan"),
                "rh_max": float("nan"),
                "rh_min": float("nan"),
                "gust_max": float("nan"),
                "pressure_3h_ago": float("nan"),
                "epoch_3h_ago": float("nan"),
            }
        
        # Listas para acumular todos los valores
        all_temp_high = []
        all_temp_low = []
        all_rh_high = []
        all_rh_low = []
        all_gust_high = []
        
        # Para buscar presión de hace 3 horas
        pressure_3h_ago = float("nan")
        epoch_3h_ago = float("nan")
        
        # Obtener el timestamp más reciente (última observación del día)
        latest_epoch = observations[-1].get("epoch", 0)
        target_epoch = latest_epoch - (3 * 3600)  # 3 horas atrás
        
        logger.info(f"Buscando presión cercana a 3h atrás (target epoch: {target_epoch})")
        
        # Buscar la observación más cercana a hace 3 horas
        closest_diff = float('inf')
        
        # Procesar TODAS las observaciones del día
        for obs in observations:
            # Humedad está en el nivel superior
            rh_high = safe_float(obs.get("humidityHigh"))
            rh_low = safe_float(obs.get("humidityLow"))
            
            if not is_nan(rh_high):
                all_rh_high.append(rh_high)
            if not is_nan(rh_low):
                all_rh_low.append(rh_low)
            
            # Temperatura y viento están dentro de metric
            metric = obs.get("metric", {})
            
            temp_high = safe_float(metric.get("tempHigh"))
            temp_low = safe_float(metric.get("tempLow"))
            gust_high = safe_float(metric.get("windgustHigh"))
            
            if not is_nan(temp_high):
                all_temp_high.append(temp_high)
            if not is_nan(temp_low):
                all_temp_low.append(temp_low)
            if not is_nan(gust_high):
                all_gust_high.append(gust_high)
            
            # Buscar presión más cercana a hace 3 horas
            obs_epoch = obs.get("epoch", 0)
            if obs_epoch > 0:
                diff = abs(obs_epoch - target_epoch)
                if diff < closest_diff:
                    closest_diff = diff
                    # Usar pressureMax o pressureMin (deberían ser iguales en intervalos cortos)
                    pressure_val = safe_float(metric.get("pressureMax"))
                    if is_nan(pressure_val):
                        pressure_val = safe_float(metric.get("pressureMin"))
                    if not is_nan(pressure_val):
                        pressure_3h_ago = pressure_val
                        epoch_3h_ago = obs_epoch
        
        # Log del resultado de presión
        if not is_nan(pressure_3h_ago):
            actual_hours_ago = (latest_epoch - epoch_3h_ago) / 3600.0
            logger.info(f"✅ Presión hace {actual_hours_ago:.2f}h: {pressure_3h_ago:.1f} hPa (diff: {closest_diff}s)")
        else:
            logger.warning("⚠️ No se encontró presión de hace 3h en /all/1day")
            
            # Buscar presión más cercana a hace 3 horas
            obs_epoch = obs.get("epoch", 0)
            if obs_epoch > 0:
                diff = abs(obs_epoch - target_epoch)
                if diff < closest_diff:
                    closest_diff = diff
                    pressure_val = safe_float(metric.get("pressureMax"))
                    if not is_nan(pressure_val):
                        pressure_3h_ago = pressure_val
                        epoch_3h_ago = obs_epoch
        
        # Calcular extremos del DÍA COMPLETO
        temp_max = max(all_temp_high) if all_temp_high else float("nan")
        temp_min = min(all_temp_low) if all_temp_low else float("nan")
        rh_max = max(all_rh_high) if all_rh_high else float("nan")
        rh_min = min(all_rh_low) if all_rh_low else float("nan")
        gust_max = max(all_gust_high) if all_gust_high else float("nan")
        
        logger.info(f"✅ Extremos del día (de {len(observations)} observaciones):")
        logger.info(f"   T: {temp_max:.1f}° / {temp_min:.1f}°C")
        logger.info(f"   RH: {rh_max:.0f}% / {rh_min:.0f}%")
        logger.info(f"   Racha: {gust_max:.1f} km/h")
        if not is_nan(pressure_3h_ago):
            logger.info(f"   Presión hace 3h: {pressure_3h_ago:.1f} hPa")
        
        return {
            "temp_max": temp_max,
            "temp_min": temp_min,
            "rh_max": rh_max,
            "rh_min": rh_min,
            "gust_max": gust_max,
            "pressure_3h_ago": pressure_3h_ago,
            "epoch_3h_ago": epoch_3h_ago,
        }
        
    except Exception as e:
        logger.warning(f"Error obteniendo extremos: {e}")
        return {
            "temp_max": float("nan"),
            "temp_min": float("nan"),
            "rh_max": float("nan"),
            "rh_min": float("nan"),
            "gust_max": float("nan"),
            "pressure_3h_ago": float("nan"),
            "epoch_3h_ago": float("nan"),
        }


def fetch_wu_current(station_id: str, api_key: str) -> Dict:
    """Obtiene datos actuales de /current"""
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal"
    }

    logger.info(f"Consultando datos actuales (/current)")

    try:
        r = requests.get(WU_URL_CURRENT, params=params, timeout=WU_TIMEOUT_SECONDS)
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
    except (KeyError, IndexError, ValueError):
        raise WuError("badjson")

    raw_dir = safe_float(obs.get("winddir"))
    if not is_nan(raw_dir):
        wind_dir_deg = (raw_dir + WIND_DIR_OFFSET_DEG) % 360
    else:
        wind_dir_deg = float("nan")

    Tc = safe_float(m.get("temp"))
    RH = safe_float(obs.get("humidity"))
    p_hpa = safe_float(m.get("pressure"))
    Td = safe_float(m.get("dewpt"))
    heat_index = safe_float(m.get("heatIndex"))
    wind_chill = safe_float(m.get("windChill"))
    wind = safe_float(m.get("windSpeed"))
    gust = safe_float(m.get("windGust"))
    precip_rate = safe_float(m.get("precipRate"))
    precip_total_raw = safe_float(m.get("precipTotal"))
    precip_total = quantize_rain_mm_wu(precip_total_raw)
    
    # Radiación solar y UV (pueden no estar presentes en todas las estaciones)
    solar_radiation = safe_float(obs.get("solarRadiation"))  # W/m²
    uv_index = safe_float(obs.get("uv"))  # índice UV

    if is_nan(Tc) or Tc < HEAT_INDEX_MIN_TEMP:
        heat_index = float("nan")

    if is_nan(Tc) or is_nan(wind) or not (Tc <= WIND_CHILL_MAX_TEMP and wind >= WIND_CHILL_MIN_SPEED):
        wind_chill = float("nan")

    if not is_nan(wind_chill):
        feels_like = wind_chill
    elif not is_nan(heat_index):
        feels_like = heat_index
    else:
        feels_like = Tc

    epoch = obs.get("epoch", 0)
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        epoch = int(time.time())

    logger.info(f"✅ Current: T={Tc:.1f}°C, RH={RH:.0f}%, P={p_hpa:.1f}hPa")
    if not is_nan(solar_radiation):
        logger.info(f"   Solar: {solar_radiation:.0f} W/m², UV: {uv_index:.1f if not is_nan(uv_index) else 'N/A'}")

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
        "solar_radiation": solar_radiation,
        "uv": uv_index,
        "epoch": epoch,
        "time_local": obs.get("obsTimeLocal", ""),
        "time_utc": obs.get("obsTimeUtc", ""),
    }


def fetch_wu_current_session_cached(station_id: str, api_key: str, ttl_s: int) -> Dict:
    """Cache híbrido con dos niveles"""
    if "wu_cache_current" not in st.session_state:
        st.session_state.wu_cache_current = OrderedDict()
    if "wu_cache_daily" not in st.session_state:
        st.session_state.wu_cache_daily = OrderedDict()

    cache_current = st.session_state.wu_cache_current
    cache_daily = st.session_state.wu_cache_daily
    k = (station_id, api_key)
    now = time.time()
    
    # Datos current (frecuente)
    need_current = True
    if k in cache_current:
        age = now - cache_current[k]["t"]
        if age < ttl_s:
            cache_current.move_to_end(k)
            base_data = cache_current[k]["data"]
            need_current = False
    
    if need_current:
        if len(cache_current) >= MAX_CACHE_SIZE:
            cache_current.popitem(last=False)
        base_data = fetch_wu_current(station_id, api_key)
        cache_current[k] = {"t": now, "data": base_data}
    
    # Extremos daily (menos frecuente - 10 minutos)
    TTL_DAILY = 600
    need_daily = True
    
    if k in cache_daily:
        age = now - cache_daily[k]["t"]
        if age < TTL_DAILY:
            cache_daily.move_to_end(k)
            extremes_data = cache_daily[k]["data"]
            need_daily = False
    
    if need_daily:
        if len(cache_daily) >= MAX_CACHE_SIZE:
            cache_daily.popitem(last=False)
        extremes_data = fetch_extremes_from_daily(station_id, api_key)
        cache_daily[k] = {"t": now, "data": extremes_data}
    
    result = base_data.copy()
    result.update(extremes_data)
    
    return result
"""
Servicio de análisis de precipitaciones
"""
import streamlit as st
from collections import deque
from utils.helpers import is_nan
from config import (
    RAIN_TRACE, RAIN_VERY_LIGHT, RAIN_LIGHT, RAIN_MODERATE_LIGHT,
    RAIN_MODERATE, RAIN_HEAVY, RAIN_VERY_HEAVY
)


def ensure_rain_history():
    """Inicializa el historial de lluvia en session_state"""
    if "rain_hist" not in st.session_state:
        st.session_state.rain_hist = deque(maxlen=2000)
    if "last_tip" not in st.session_state:
        st.session_state.last_tip = None
    if "prev_tip" not in st.session_state:
        st.session_state.prev_tip = None


def reset_rain_history():
    """Resetea el historial de lluvia"""
    st.session_state.rain_hist = deque(maxlen=2000)
    st.session_state.last_tip = None
    st.session_state.prev_tip = None


def rain_rates_from_total(precip_total_mm: float, now_ts: float):
    """
    Calcula tasas de precipitación instantánea y en ventanas temporales
    
    Args:
        precip_total_mm: Precipitación total acumulada (mm)
        now_ts: Timestamp actual (epoch)
        
    Returns:
        Tupla (inst_mm_h, r1_mm_h, r5_mm_h) con tasas en mm/h
    """
    ensure_rain_history()

    if is_nan(precip_total_mm):
        return float("nan"), float("nan"), float("nan")

    hist = st.session_state.rain_hist

    # Detectar reset del contador (ej: medianoche)
    if hist and precip_total_mm + 1e-6 < hist[-1][1]:
        reset_rain_history()
        hist = st.session_state.rain_hist

    # Agregar nuevo punto si cambió el total
    if (not hist) or abs(precip_total_mm - hist[-1][1]) > 1e-9:
        hist.append((now_ts, precip_total_mm))
        st.session_state.prev_tip = st.session_state.last_tip
        st.session_state.last_tip = (now_ts, precip_total_mm)

    # Tasa instantánea (entre últimos dos tips)
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
        """Calcula tasa de lluvia en una ventana temporal"""
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

    r1 = window_rate(60.0)   # 1 minuto
    r5 = window_rate(300.0)  # 5 minutos

    return inst, r1, r5


def rain_intensity_label(rate_mm_h: float) -> str:
    """
    Etiqueta descriptiva para intensidad de lluvia
    
    Args:
        rate_mm_h: Tasa de lluvia en mm/h
        
    Returns:
        Descripción de la intensidad
        
    Referencias:
        Clasificación estándar de intensidad de precipitación
    """
    if is_nan(rate_mm_h) or rate_mm_h <= 0:
        return "Sin precipitación"
    elif rate_mm_h < RAIN_TRACE:
        return "Traza de precipitación"
    elif rate_mm_h < RAIN_VERY_LIGHT:
        return "Lluvia muy débil"
    elif rate_mm_h < RAIN_LIGHT:
        return "Lluvia débil"
    elif rate_mm_h < RAIN_MODERATE_LIGHT:
        return "Lluvia ligera"
    elif rate_mm_h < RAIN_MODERATE:
        return "Lluvia moderada"
    elif rate_mm_h < RAIN_HEAVY:
        return "Lluvia fuerte"
    elif rate_mm_h < RAIN_VERY_HEAVY:
        return "Lluvia muy fuerte"
    else:
        return "Lluvia torrencial"

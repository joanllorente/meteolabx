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


def rain_rates_from_total(precip_total_mm: float, data_epoch: float):
    """
    Calcula tasas de precipitación instantánea y en ventanas temporales
    
    MEJORAS:
    - Usa el epoch del dato de WU (cuando se recibió el volcado), no el tiempo de la app
    - Primera lectura sin referencia: usa 0.4 mm/h como default
    - Intensidad promediada: si pasan 2 min entre volcados de 0.4 mm → 12 mm/h, no 0
    
    Args:
        precip_total_mm: Precipitación total acumulada (mm)
        data_epoch: Timestamp del dato de WU (cuando se recibió)
        
    Returns:
        Tupla (inst_mm_h, r1_mm_h, r5_mm_h) con tasas en mm/h
    """
    ensure_rain_history()

    if is_nan(precip_total_mm) or is_nan(data_epoch):
        return float("nan"), float("nan"), float("nan")

    hist = st.session_state.rain_hist

    # Detectar reset del contador (ej: medianoche)
    if hist and precip_total_mm + 1e-6 < hist[-1][1]:
        reset_rain_history()
        hist = st.session_state.rain_hist

    # Agregar nuevo punto SOLO si hay precipitación Y cambió el total
    # No trackear volcados de 0 mm (sin lluvia)
    if precip_total_mm > 1e-9:  # Hay precipitación real
        if (not hist) or abs(precip_total_mm - hist[-1][1]) > 1e-9:
            hist.append((data_epoch, precip_total_mm))
            st.session_state.prev_tip = st.session_state.last_tip
            st.session_state.last_tip = (data_epoch, precip_total_mm)

    # Tasa instantánea (entre últimos dos tips)
    inst = float("nan")
    a = st.session_state.prev_tip
    b = st.session_state.last_tip
    
    if b is not None:
        t1, p1 = b
        
        # Solo calcular intensidad si hay precipitación real en last_tip
        if p1 > 1e-9:
            if a is not None:
                # Tenemos dos volcados: calcular intensidad real
                t0, p0 = a
                dp = p1 - p0
                dt = t1 - t0
                if dp > 0 and dt > 0:
                    inst = (dp / dt) * 3600.0
            else:
                # PRIMER VOLCADO: usar 0.4 mm/h como default
                inst = 0.4
        # Si p1 == 0, no hay lluvia → inst queda como NaN

    def window_rate(window_s: float):
        """
        Calcula tasa de lluvia en una ventana temporal
        MEJORADO: Promedia la intensidad en lugar de devolver 0
        """
        if not hist:
            return float("nan")
        
        t_now = data_epoch
        p_now = precip_total_mm
        target = t_now - window_s
        
        # Si no hay datos suficientemente antiguos, usar el más antiguo disponible
        if hist[0][0] > target:
            # No tenemos datos de toda la ventana
            # Usar el dato más antiguo y promediar
            t_old, p_old = hist[0]
        else:
            # Buscar el dato más cercano al inicio de la ventana
            t_old, p_old = hist[0]
            for t_i, p_i in reversed(hist):
                if t_i <= target:
                    t_old, p_old = t_i, p_i
                    break
        
        dt = t_now - t_old
        dp = p_now - p_old
        
        # MEJORA: Siempre promediar la intensidad
        if dt <= 0:
            return float("nan")
        
        if dp < 0:
            # Reset del contador detectado
            return float("nan")
        
        # Promediar: si entre volcados pasaron 2 min y cayeron 0.4 mm
        # la intensidad es (0.4 / 2min) * 60 = 12 mm/h
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

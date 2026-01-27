"""
Servicio de análisis de tendencias de presión atmosférica
"""
import streamlit as st
from collections import deque
from utils.helpers import is_nan
from config import PRESSURE_STABLE_THRESHOLD, PRESSURE_RAPID_CHANGE


def init_pressure_history(maxlen: int = 720):
    """
    Inicializa el historial de presión en session_state
    
    Args:
        maxlen: Longitud máxima del deque (default: 720 ~ 6h a 30s/punto)
    """
    if "p_hist" not in st.session_state:
        st.session_state.p_hist = deque(maxlen=maxlen)


def push_pressure(p_hpa: float, epoch: int):
    """
    Agrega un punto de presión al historial
    
    Args:
        p_hpa: Presión en hPa
        epoch: Timestamp del dato
    """
    if is_nan(p_hpa):
        return
    hist = st.session_state.p_hist
    if len(hist) == 0 or epoch > hist[-1][0]:
        hist.append((epoch, p_hpa))


def pressure_trend_3h():
    """
    Calcula la tendencia de presión en las últimas 3 horas
    
    Returns:
        Tupla (dp, rate_h, label, arrow) donde:
        - dp: Diferencia de presión en hPa
        - rate_h: Tasa de cambio en hPa/h
        - label: Etiqueta descriptiva
        - arrow: Símbolo de flecha
    """
    hist = st.session_state.p_hist
    if len(hist) < 2:
        return (float("nan"), float("nan"), "—", "•")

    t_now, p_now = hist[-1]
    target = t_now - 3 * 3600  # 3 horas atrás

    # Buscar el punto más cercano a 3h atrás
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

    # Clasificar tendencia usando constantes de config
    if abs(dp) < PRESSURE_STABLE_THRESHOLD:
        return (dp, rate_h, "Estable", "→")
    elif dp > 0:
        if dp > PRESSURE_RAPID_CHANGE:
            return (dp, rate_h, "Subiendo rápido", "⬆")
        return (dp, rate_h, "Subiendo", "↗")
    else:
        if dp < -PRESSURE_RAPID_CHANGE:
            return (dp, rate_h, "Bajando rápido", "⬇")
        return (dp, rate_h, "Bajando", "↘")


def pressure_label_extended(dp: float) -> str:
    """
    Etiqueta extendida para tendencia de presión con predicción meteorológica
    
    Args:
        dp: Diferencia de presión en hPa
        
    Returns:
        Descripción meteorológica de la tendencia
    """
    if is_nan(dp):
        return "Datos insuficientes"
    
    if abs(dp) < 0.5:
        return "Condiciones estables"
    elif dp > 3:
        return "Mejora rápida - Alta entrante"
    elif dp > 1.5:
        return "Mejora gradual - Cielos despejados"
    elif dp > 0:
        return "Ligera mejora"
    elif dp < -3:
        return "Empeoramiento rápido - Posible tormenta"
    elif dp < -1.5:
        return "Empeoramiento gradual - Lluvia probable"
    else:
        return "Ligero empeoramiento"

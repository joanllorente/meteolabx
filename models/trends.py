"""
Cálculos de tendencias meteorológicas

Todas las variables calculadas a partir de T, RH y p_abs
"""
import math
import numpy as np
import pandas as pd


# Constantes físicas
CP = 1004.0      # J/kg/K - Calor específico del aire a presión constante
LV = 2.5e6       # J/kg - Calor latente de vaporización
RD = 287.05      # J/kg/K - Constante específica del aire seco
P0 = 1000.0      # hPa - Presión de referencia
KAPPA = RD / CP  # ≈ 0.286


def saturation_pressure(t_celsius):
    """
    Presión de saturación (Tetens)
    e_s(T) = 6.112 * exp(17.67*T / (T+243.5))
    
    Args:
        t_celsius: Temperatura en °C
        
    Returns:
        Presión de saturación en hPa
    """
    t = float(t_celsius)
    return 6.112 * math.exp((17.67 * t) / (t + 243.5))


def vapor_pressure(t_celsius, rh_pct):
    """
    Presión de vapor a partir de T y HR
    e = (RH/100) * e_s(T)
    
    Args:
        t_celsius: Temperatura en °C
        rh_pct: Humedad relativa en %
        
    Returns:
        Presión de vapor en hPa
    """
    return (float(rh_pct) / 100.0) * saturation_pressure(t_celsius)


def specific_humidity(t_celsius, rh_pct, p_hpa):
    """
    Humedad específica q (kg/kg)
    Calculada a partir de T, RH y p
    
    Args:
        t_celsius: Temperatura en °C
        rh_pct: Humedad relativa en %
        p_hpa: Presión absoluta en hPa
        
    Returns:
        Humedad específica en kg/kg
    """
    e = vapor_pressure(t_celsius, rh_pct)
    p = float(p_hpa)
    r = 0.622 * e / (p - e)  # Razón de mezcla
    q = r / (1.0 + r)  # Humedad específica
    return float(q)


def potential_temperature(t_celsius, p_hpa):
    """
    Calcula temperatura potencial θ (K)
    
    Args:
        t_celsius: Temperatura en °C
        p_hpa: Presión absoluta en hPa
        
    Returns:
        Temperatura potencial en K
    """
    t = float(t_celsius)
    p = float(p_hpa)
    t_kelvin = t + 273.15
    theta = t_kelvin * (P0 / p) ** (RD / CP)
    return float(theta)


def equivalent_potential_temperature(t_celsius, rh_pct, p_hpa):
    """
    Temperatura potencial equivalente θe (K)
    θe = θ * exp(Lv*q / (cp*T_K))
    
    Calculada a partir de T, RH y p
    
    Args:
        t_celsius: Temperatura en °C
        rh_pct: Humedad relativa en %
        p_hpa: Presión absoluta en hPa
        
    Returns:
        Temperatura potencial equivalente en K
    """
    t = float(t_celsius)
    rh = float(rh_pct)
    p = float(p_hpa)
    
    t_kelvin = t + 273.15
    theta = potential_temperature(t, p)
    q = specific_humidity(t, rh, p)
    
    # θe = θ * exp(Lv * q / (cp * T))
    theta_e = theta * math.exp((LV * q) / (CP * t_kelvin))
    return float(theta_e)


def calculate_trend(values, times, interval_minutes=10):
    """
    Calcula tendencia (derivada discreta) usando un intervalo fijo
    
    Args:
        values: Array de valores
        times: Array de tiempos (pandas DatetimeIndex o similar)
        interval_minutes: Intervalo en minutos para calcular la tendencia
        
    Returns:
        Array de tendencias (misma longitud que values, con NaN donde no se puede calcular)
    """
    trends = []
    
    # Convertir times a DatetimeIndex si no lo es
    if not isinstance(times, pd.DatetimeIndex):
        times = pd.to_datetime(times)
    
    for i, t in enumerate(times):
        # Buscar el punto interval_minutes atrás
        target_time = t - pd.Timedelta(minutes=interval_minutes)
        
        # Encontrar el índice más cercano
        time_diffs_td = np.abs(times - target_time)
        
        # Convertir a segundos (manejar tanto Series como TimedeltaIndex)
        if hasattr(time_diffs_td, 'total_seconds'):
            # Es un TimedeltaIndex o similar
            time_diffs = time_diffs_td.total_seconds().values
        else:
            # Es una Serie con .dt accessor
            time_diffs = time_diffs_td.dt.total_seconds().values
        
        min_diff_idx = np.argmin(time_diffs)
        
        # Tolerancia: 30 segundos para all1day, 2 minutos para hourly/7day
        tolerance = 120 if interval_minutes >= 60 else 30
        
        if time_diffs[min_diff_idx] <= tolerance:
            v_now = float(values[i])
            v_past = float(values[min_diff_idx])
            
            if not (math.isnan(v_now) or math.isnan(v_past)):
                # Tendencia por hora
                dt_hours = float(interval_minutes) / 60.0
                trend = float((v_now - v_past) / dt_hours)
                trends.append(trend)
            else:
                trends.append(np.nan)
        else:
            trends.append(np.nan)
    
    return np.array(trends, dtype=np.float64)

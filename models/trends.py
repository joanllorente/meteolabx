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
    Temperatura potencial equivalente θe (K) según Bolton (1980).

    Fórmula implementada:
        θE = T_K * (1000/p)^(0.2854 * (1 - 0.28e-3 * r))
             * exp(((3.376/T_L) - 0.00254) * r * (1 + 0.81e-3 * r))

    con:
        - T_K en K
        - p en hPa (presión absoluta)
        - r en g/kg
        - T_L en K:
          T_L = 1 / (1/(T_K - 55) - ln(RH/100)/2840) + 55

    Args:
        t_celsius: Temperatura en °C.
        rh_pct: Humedad relativa en %.
        p_hpa: Presión absoluta en hPa.

    Returns:
        Temperatura potencial equivalente en K (float("nan") si no es calculable).
    """
    try:
        t = float(t_celsius)
        rh = float(rh_pct)
        p = float(p_hpa)
    except (TypeError, ValueError):
        return float("nan")

    if math.isnan(t) or math.isnan(rh) or math.isnan(p):
        return float("nan")
    if p <= 0.0:
        return float("nan")

    # Evita log(0) y limita ruido de sensores (>100%).
    rh_limited = min(max(rh, 0.1), 100.0)

    t_kelvin = t + 273.15
    e = vapor_pressure(t, rh_limited)
    if p <= e:
        return float("nan")

    # r en g/kg (la fórmula de Bolton usa g/kg).
    r_kgkg = 0.622 * e / (p - e)
    r_gkg = r_kgkg * 1000.0

    tl_denom = (1.0 / (t_kelvin - 55.0)) - (math.log(rh_limited / 100.0) / 2840.0)
    if tl_denom == 0.0:
        return float("nan")
    t_l = (1.0 / tl_denom) + 55.0
    if t_l <= 0.0 or math.isnan(t_l):
        return float("nan")

    theta_e = (
        t_kelvin
        * (1000.0 / p) ** (0.2854 * (1.0 - 0.28e-3 * r_gkg))
        * math.exp(((3.376 / t_l) - 0.00254) * r_gkg * (1.0 + 0.81e-3 * r_gkg))
    )
    return float(theta_e)


def calculate_trend(values, times, interval_minutes=10):
    """
    Calcula tendencia (derivada discreta) usando un intervalo fijo.

    Adaptado para series irregulares: la tolerancia temporal se ajusta
    automáticamente según la resolución real de la serie.
    """
    trends = []

    # Convertir times a DatetimeIndex si no lo es
    if not isinstance(times, pd.DatetimeIndex):
        times = pd.to_datetime(times)

    # Inferir resolución temporal típica (segundos)
    inferred_step_s = None
    try:
        if len(times) >= 2:
            # Compatibilidad pandas: .view('int64') en Series está deprecado.
            time_ns = np.asarray(pd.DatetimeIndex(times).astype("int64"), dtype=np.int64)
            diffs = np.diff(time_ns) / 1e9
            diffs = diffs[diffs > 0]
            if diffs.size > 0:
                inferred_step_s = float(np.median(diffs))
    except Exception:
        inferred_step_s = None

    interval_seconds = float(interval_minutes) * 60.0

    # Tolerancia robusta para datos no perfectamente regulares.
    # - mínimo 30s
    # - al menos 1.5x la resolución típica (si se conoce)
    # - hasta ~35% del intervalo objetivo
    tolerance_candidates = [30.0, interval_seconds * 0.35]
    if inferred_step_s is not None:
        tolerance_candidates.append(inferred_step_s * 1.5)
    tolerance = max(tolerance_candidates)

    for i, t in enumerate(times):
        target_time = t - pd.Timedelta(minutes=interval_minutes)

        time_diffs_td = np.abs(times - target_time)
        if hasattr(time_diffs_td, 'total_seconds'):
            time_diffs = time_diffs_td.total_seconds().values
        else:
            time_diffs = time_diffs_td.dt.total_seconds().values

        min_diff_idx = np.argmin(time_diffs)

        if time_diffs[min_diff_idx] <= tolerance:
            v_now = float(values[i])
            v_past = float(values[min_diff_idx])

            if not (math.isnan(v_now) or math.isnan(v_past)):
                dt_hours = float(interval_minutes) / 60.0
                trend = float((v_now - v_past) / dt_hours)
                trends.append(trend)
            else:
                trends.append(np.nan)
        else:
            trends.append(np.nan)

    return np.array(trends, dtype=np.float64)

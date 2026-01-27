"""
Cálculos termodinámicos y meteorológicos
"""
import math
from config import G0, RD, RV, EPSILON, EPSILON_COMP, KAPPA, TV_COEF, CP, LV, LCL_FACTOR

def is_nan(x):
    return x != x

def e_s(Td: float) -> float:
    return 6.112 * math.exp((17.67 * Td) / (Td + 243.5))

def q_from_e(e: float, p: float) -> float:
    return EPSILON * e / (p - EPSILON_COMP * e)

def theta_celsius(T_celsius: float, p_hpa: float) -> float:
    T_k = T_celsius + 273.15
    return T_k * (1000 / p_hpa) ** KAPPA - 273.15

def Tv_celsius(T_celsius: float, q: float) -> float:
    T_k = T_celsius + 273.15
    return T_k * (1 + TV_COEF * q) - 273.15

def Te_celsius(T_celsius: float, q: float) -> float:
    T_k = T_celsius + 273.15
    return T_k * math.exp((LV * q) / (CP * T_k)) - 273.15

def lcl_height(T_celsius: float, Td_celsius: float) -> float:
    return LCL_FACTOR * (T_celsius - Td_celsius)

def pressure_to_msl(p_abs: float, z: float, T_celsius: float) -> float:
    T_k = T_celsius + 273.15
    return p_abs * math.exp(G0 * z / (RD * T_k))

def msl_to_absolute(p_msl: float, z: float, T_celsius: float) -> float:
    T_k = T_celsius + 273.15
    return p_msl * math.exp(-G0 * z / (RD * T_k))

def air_density(p_abs: float, Tv_celsius: float) -> float:
    Tv_k = Tv_celsius + 273.15
    return (p_abs * 100) / (RD * Tv_k)

def absolute_humidity(e: float, T_celsius: float) -> float:
    T_k = T_celsius + 273.15
    return ((e * 100) / (RV * T_k)) * 1000

def wet_bulb_celsius_stull(T_c: float, RH_pct: float) -> float:
    try:
        if T_c is None or RH_pct is None:
            return float("nan")
        T = float(T_c)
        RH = float(RH_pct)
        if not (0.0 <= RH <= 100.0):
            return float("nan")
        Tw = (
            T * math.atan(0.151977 * math.sqrt(RH + 8.313659))
            + math.atan(T + RH)
            - math.atan(RH - 1.676331)
            + 0.00391838 * (RH ** 1.5) * math.atan(0.023101 * RH)
            - 4.686035
        )
        return Tw
    except Exception:
        return float("nan")

wet_bulb_celsius = wet_bulb_celsius_stull

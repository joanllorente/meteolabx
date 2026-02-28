"""
Cálculos termodinámicos y meteorológicos

Todas las ecuaciones calculadas a partir de T, HR y p_abs
según especificación del documento de referencia.
"""
import math
from config import G0, RD, RV, EPSILON, CP, LV, KAPPA, TV_COEF, LCL_FACTOR

def is_nan(x):
    return x != x

# ====================
# PRESIÓN DE VAPOR
# ====================

def e_s(T_celsius: float) -> float:
    """
    Presión de saturación (Tetens)
    e_s(T) = 6.112 * exp(17.67*T / (T+243.5))
    
    Args:
        T_celsius: Temperatura en °C
    Returns:
        Presión de saturación en hPa
    """
    return 6.112 * math.exp((17.67 * T_celsius) / (T_celsius + 243.5))

def vapor_pressure(T_celsius: float, RH_pct: float) -> float:
    """
    Presión de vapor a partir de T y HR
    e = (RH/100) * e_s(T)
    
    Args:
        T_celsius: Temperatura en °C
        RH_pct: Humedad relativa en %
    Returns:
        Presión de vapor en hPa
    """
    return (RH_pct / 100.0) * e_s(T_celsius)

def dewpoint_from_vapor_pressure(e: float) -> float:
    """
    Temperatura de rocío a partir de presión de vapor
    Td = 243.5*ln(e/6.112) / (17.67 - ln(e/6.112))
    
    Args:
        e: Presión de vapor en hPa
    Returns:
        Temperatura de rocío en °C
    """
    if e <= 0:
        return float("nan")
    ln_e = math.log(e / 6.112)
    return (243.5 * ln_e) / (17.67 - ln_e)

# ====================
# HUMEDAD
# ====================

def mixing_ratio(e: float, p_abs: float) -> float:
    """
    Razón de mezcla
    r = 0.622 * e / (p - e)
    
    Args:
        e: Presión de vapor en hPa
        p_abs: Presión absoluta en hPa
    Returns:
        Razón de mezcla (adimensional)
    """
    if p_abs <= e:
        return float("nan")
    return 0.622 * e / (p_abs - e)

def specific_humidity(e: float, p_abs: float) -> float:
    """
    Humedad específica
    q = r / (1 + r)
    
    Args:
        e: Presión de vapor en hPa
        p_abs: Presión absoluta en hPa
    Returns:
        Humedad específica (adimensional)
    """
    r = mixing_ratio(e, p_abs)
    if is_nan(r):
        return float("nan")
    return r / (1.0 + r)

def absolute_humidity(e: float, T_celsius: float) -> float:
    """
    Humedad absoluta
    ρv = e / (Rv * T_K)
    
    Args:
        e: Presión de vapor en hPa
        T_celsius: Temperatura en °C
    Returns:
        Humedad absoluta en g/m³
    """
    T_k = T_celsius + 273.15
    # e en hPa → Pa (multiplicar por 100), resultado en kg/m³ → g/m³ (multiplicar por 1000)
    return ((e * 100) / (RV * T_k)) * 1000

# ====================
# TEMPERATURAS
# ====================

def virtual_temperature(T_celsius: float, q: float) -> float:
    """
    Temperatura virtual
    Tv = T_K * (1 + 0.61*q)
    
    Args:
        T_celsius: Temperatura en °C
        q: Humedad específica (adimensional)
    Returns:
        Temperatura virtual en °C
    """
    T_k = T_celsius + 273.15
    return T_k * (1.0 + TV_COEF * q) - 273.15

def potential_temperature(T_celsius: float, p_abs: float) -> float:
    """
    Temperatura potencial
    θ = T_K * (1000/p)^κ
    
    Args:
        T_celsius: Temperatura en °C
        p_abs: Presión absoluta en hPa
    Returns:
        Temperatura potencial en °C
    """
    T_k = T_celsius + 273.15
    return T_k * (1000.0 / p_abs) ** KAPPA - 273.15

def equivalent_temperature(T_celsius: float, q: float) -> float:
    """
    Temperatura equivalente
    Te = T_K * exp(Lv*q / (cp*T_K))
    
    Args:
        T_celsius: Temperatura en °C
        q: Humedad específica (adimensional)
    Returns:
        Temperatura equivalente en °C
    """
    T_k = T_celsius + 273.15
    return T_k * math.exp((LV * q) / (CP * T_k)) - 273.15

def equivalent_potential_temperature(T_celsius: float, p_abs: float, q: float) -> float:
    """
    Temperatura potencial equivalente
    θe = θ * exp(Lv*q / (cp*T_K))
    
    Args:
        T_celsius: Temperatura en °C
        p_abs: Presión absoluta en hPa
        q: Humedad específica (adimensional)
    Returns:
        Temperatura potencial equivalente en °C
    """
    theta = potential_temperature(T_celsius, p_abs)
    T_k = T_celsius + 273.15
    theta_k = theta + 273.15
    return theta_k * math.exp((LV * q) / (CP * T_k)) - 273.15

def wet_bulb_celsius_stull(T_celsius: float, RH_pct: float) -> float:
    """
    Temperatura de bulbo húmedo (Stull 2011)
    
    Args:
        T_celsius: Temperatura en °C
        RH_pct: Humedad relativa en %
    Returns:
        Temperatura de bulbo húmedo en °C
    """
    try:
        if T_celsius is None or RH_pct is None:
            return float("nan")
        T = float(T_celsius)
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


def wet_bulb_psychrometric(T_celsius: float, RH_pct: float, p_abs_hpa: float) -> float:
    """
    Temperatura de bulbo húmedo por ecuación psicrométrica (Newton-Raphson).

    Resuelve  e = e_s(Tw) − γ·(T − Tw)
    con  γ = cp·p / (ε·Lv)

    Args:
        T_celsius: Temperatura en °C
        RH_pct: Humedad relativa en %
        p_abs_hpa: Presión absoluta en hPa
    Returns:
        Temperatura de bulbo húmedo en °C
    """
    try:
        if T_celsius is None or RH_pct is None or p_abs_hpa is None:
            return float("nan")
        T = float(T_celsius)
        RH = float(RH_pct)
        p = float(p_abs_hpa)
        if is_nan(T) or is_nan(RH) or is_nan(p) or p <= 0:
            return wet_bulb_celsius_stull(T, RH)

        # Constante psicrométrica  γ = cp·p / (ε·Lv)  [hPa/°C]
        gamma = CP * p / (EPSILON * LV)

        # Presión de vapor real
        e_actual = vapor_pressure(T, RH)
        if is_nan(e_actual):
            return wet_bulb_celsius_stull(T, RH)

        # Semilla: Stull 2011
        Tw = wet_bulb_celsius_stull(T, RH)
        if is_nan(Tw):
            return float("nan")

        # Newton-Raphson
        for _ in range(50):
            es_tw = e_s(Tw)
            # f(Tw) = e_s(Tw) − γ·(T − Tw) − e
            f_val = es_tw - gamma * (T - Tw) - e_actual
            # f'(Tw) = de_s/dTw + γ
            des_dtw = es_tw * 17.67 * 243.5 / ((Tw + 243.5) ** 2)
            fp_val = des_dtw + gamma
            if abs(fp_val) < 1e-12:
                break
            delta = f_val / fp_val
            Tw -= delta
            if abs(delta) < 1e-6:
                break

        # Clamp a [Td, T] para garantía física
        Td = dewpoint_from_vapor_pressure(e_actual)
        if not is_nan(Td):
            Tw = max(Tw, Td)
        Tw = min(Tw, T)
        return Tw
    except Exception:
        return wet_bulb_celsius_stull(T_celsius, RH_pct)


# ====================
# PRESIÓN
# ====================

def msl_to_absolute(p_msl: float, z: float, T_celsius: float) -> float:
    """
    Presión absoluta a partir de MSLP usando ecuación hipsométrica
    p_abs = p_msl * exp(-g*z / (Rd*T_K))
    
    Args:
        p_msl: Presión a nivel del mar en hPa
        z: Altitud en metros
        T_celsius: Temperatura en °C
    Returns:
        Presión absoluta en hPa
    """
    T_k = T_celsius + 273.15
    return p_msl * math.exp(-G0 * z / (RD * T_k))

def absolute_to_msl(p_abs: float, z: float, T_celsius: float) -> float:
    """
    MSLP a partir de presión absoluta usando ecuación hipsométrica
    p_msl = p_abs * exp(g*z / (Rd*T_K))
    
    Args:
        p_abs: Presión absoluta en hPa
        z: Altitud en metros
        T_celsius: Temperatura en °C
    Returns:
        Presión a nivel del mar en hPa
    """
    T_k = T_celsius + 273.15
    return p_abs * math.exp(G0 * z / (RD * T_k))

# ====================
# OTROS
# ====================

def air_density(p_abs: float, Tv_celsius: float) -> float:
    """
    Densidad del aire
    ρ = p / (Rd * Tv)
    
    Args:
        p_abs: Presión absoluta en hPa
        Tv_celsius: Temperatura virtual en °C
    Returns:
        Densidad en kg/m³
    """
    Tv_k = Tv_celsius + 273.15
    return (p_abs * 100) / (RD * Tv_k)

def lcl_height(T_celsius: float, Td_celsius: float) -> float:
    """
    Altura del nivel de condensación por elevación (LCL)
    Aproximación simplificada
    
    Args:
        T_celsius: Temperatura en °C
        Td_celsius: Temperatura de rocío en °C
    Returns:
        Altura LCL en metros
    """
    return LCL_FACTOR * (T_celsius - Td_celsius)

# ====================
# SENSACIÓN TÉRMICA
# ====================

def apparent_temperature(T_celsius: float, e_hpa: float, v_ms: float) -> float:
    """
    Temperatura aparente (Steadman, 1984).
    T_app = T + 0.33·e − 0.70·v − 4.00

    Args:
        T_celsius: Temperatura en °C
        e_hpa: Presión de vapor en hPa
        v_ms: Velocidad del viento en m/s
    Returns:
        Temperatura aparente en °C
    """
    if is_nan(T_celsius) or is_nan(e_hpa):
        return float("nan")
    if is_nan(v_ms):
        v_ms = 0.0
    return T_celsius + 0.33 * e_hpa - 0.70 * v_ms - 4.00


def heat_index_rothfusz(T_celsius: float, RH_pct: float) -> float:
    """
    Heat Index (Rothfusz, NWS).
    Regresión polinómica de 9 coeficientes.

    Args:
        T_celsius: Temperatura en °C
        RH_pct: Humedad relativa en %
    Returns:
        Heat Index en °C
    """
    if is_nan(T_celsius) or is_nan(RH_pct):
        return float("nan")
    T = T_celsius
    RH = RH_pct
    return (
        -8.78469475556
        + 1.61139411 * T
        + 2.33854883889 * RH
        - 0.14611605 * T * RH
        - 0.012308094 * T * T
        - 0.0164248277778 * RH * RH
        + 0.002211732 * T * T * RH
        + 0.00072546 * T * RH * RH
        - 0.000003582 * T * T * RH * RH
    )


# Dispatcher bulbo húmedo: psicrométrica si hay presión, Stull si no
def wet_bulb_celsius(T_celsius: float, RH_pct: float, p_abs_hpa: float = None) -> float:
    """Tw: usa ecuación psicrométrica si hay p_abs, Stull 2011 si no."""
    if p_abs_hpa is not None and not is_nan(p_abs_hpa):
        return wet_bulb_psychrometric(T_celsius, RH_pct, p_abs_hpa)
    return wet_bulb_celsius_stull(T_celsius, RH_pct)


theta_celsius = potential_temperature
Tv_celsius = virtual_temperature
Te_celsius = equivalent_temperature


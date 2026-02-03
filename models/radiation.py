"""
Cálculos relacionados con radiación solar y evapotranspiración según FAO-56
"""
import math
from datetime import datetime


def is_nan(x):
    """Verifica si un valor es NaN"""
    return x != x


# ============================================================
# RADIACIÓN SOLAR TEÓRICA SEGÚN FAO-56
# ============================================================

def solar_declination(day_of_year: int) -> float:
    """
    Declinación solar según FAO-56 Eq. 24
    
    Args:
        day_of_year: Día juliano (1-365)
        
    Returns:
        Declinación solar en radianes
    """
    return 0.409 * math.sin((2 * math.pi / 365) * day_of_year - 1.39)


def inverse_relative_distance(day_of_year: int) -> float:
    """
    Distancia relativa inversa Tierra-Sol según FAO-56 Eq. 23
    
    Args:
        day_of_year: Día juliano (1-365)
        
    Returns:
        dr (adimensional)
    """
    return 1 + 0.033 * math.cos((2 * math.pi / 365) * day_of_year)


def sunset_hour_angle(latitude_rad: float, declination_rad: float) -> float:
    """
    Ángulo horario de puesta de sol según FAO-56 Eq. 25
    
    Args:
        latitude_rad: Latitud en radianes
        declination_rad: Declinación solar en radianes
        
    Returns:
        Ángulo horario en radianes
    """
    return math.acos(-math.tan(latitude_rad) * math.tan(declination_rad))


def extraterrestrial_radiation(latitude_deg: float, day_of_year: int) -> float:
    """
    Radiación solar extraterrestre (Ra) según FAO-56 Eq. 21
    
    Args:
        latitude_deg: Latitud en grados (positiva norte, negativa sur)
        day_of_year: Día juliano (1-365)
        
    Returns:
        Ra en MJ/m²/día
    """
    # Constantes
    GSC = 0.0820  # Constante solar en MJ/m²/min
    
    # Convertir latitud a radianes
    lat_rad = math.radians(latitude_deg)
    
    # Declinación solar
    delta = solar_declination(day_of_year)
    
    # Distancia relativa inversa
    dr = inverse_relative_distance(day_of_year)
    
    # Ángulo horario de puesta de sol
    ws = sunset_hour_angle(lat_rad, delta)
    
    # Ra según FAO-56 Eq. 21
    ra = (24 * 60 / math.pi) * GSC * dr * (
        ws * math.sin(lat_rad) * math.sin(delta) +
        math.cos(lat_rad) * math.cos(delta) * math.sin(ws)
    )
    
    return ra


def clear_sky_radiation(elevation_m: float, ra: float) -> float:
    """
    Radiación solar en cielo despejado (Rso) según FAO-56 Eq. 37
    
    Args:
        elevation_m: Elevación sobre el nivel del mar en metros
        ra: Radiación extraterrestre en MJ/m²/día
        
    Returns:
        Rso en MJ/m²/día
    """
    # FAO-56 Eq. 37 para ausencia de turbidez
    rso = (0.75 + 2e-5 * elevation_m) * ra
    return rso


def solar_radiation_max_wm2(latitude_deg: float, elevation_m: float, timestamp: float) -> float:
    """
    Radiación solar máxima teórica instantánea en W/m²
    
    Calcula la radiación máxima que podría recibirse en condiciones de cielo despejado
    en una ubicación y fecha dadas.
    
    Args:
        latitude_deg: Latitud en grados
        elevation_m: Elevación en metros
        timestamp: Timestamp Unix
        
    Returns:
        Radiación máxima en W/m² (instantánea al mediodía)
    """
    dt = datetime.fromtimestamp(timestamp)
    day_of_year = dt.timetuple().tm_yday
    
    # Radiación extraterrestre diaria
    ra = extraterrestrial_radiation(latitude_deg, day_of_year)
    
    # Radiación de cielo despejado diaria
    rso_mj = clear_sky_radiation(elevation_m, ra)
    
    # Convertir de MJ/m²/día a W/m² instantáneo al mediodía
    # Asumiendo distribución sinusoidal a lo largo del día (12h efectivas)
    # Máximo instantáneo ≈ 1.5 * promedio durante horas de sol
    rso_wm2 = (rso_mj / (12 * 3600)) * 1e6 * 1.5
    
    return rso_wm2


# ============================================================
# FAO-56 PENMAN-MONTEITH ET0
# ============================================================

def penman_monteith_et0(
    solar_rad_wm2: float,  # W/m²
    temp_c: float,         # °C
    rh: float,             # %
    wind_ms: float,        # m/s
    latitude_deg: float,   # grados
    elevation_m: float,    # metros
    timestamp: float       # epoch timestamp
) -> float:
    """
    Evapotranspiración de referencia (ET0) según FAO-56 Penman-Monteith
    
    Método estándar para calcular ET0 de cultivo de referencia (pasto).
    
    Args:
        solar_rad_wm2: Radiación solar en W/m²
        temp_c: Temperatura del aire en °C
        rh: Humedad relativa en %
        wind_ms: Velocidad del viento a 2m en m/s
        latitude_deg: Latitud en grados
        elevation_m: Elevación sobre nivel del mar en metros
        timestamp: Timestamp Unix (para calcular día del año)
        
    Returns:
        ET0 en mm/día
        
    Referencias:
        Allen et al. (1998) - FAO Irrigation and Drainage Paper 56
        Ecuación completa: FAO-56 Eq. 6
    """
    # Validar entradas
    if any(is_nan(x) for x in [solar_rad_wm2, temp_c, rh, wind_ms, latitude_deg]):
        return float("nan")
    
    if solar_rad_wm2 < 0 or rh < 0 or rh > 100 or wind_ms < 0:
        return float("nan")
    
    # Día del año
    dt = datetime.fromtimestamp(timestamp)
    day_of_year = dt.timetuple().tm_yday
    
    # Presión atmosférica según FAO-56 Eq. 7
    p_kpa = 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26
    
    # Constante psicrométrica según FAO-56 Eq. 8
    gamma = 0.665e-3 * p_kpa  # kPa/°C
    
    # Pendiente curva presión de vapor (Δ) según FAO-56 Eq. 13
    e_s = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    delta = (4098 * e_s) / ((temp_c + 237.3) ** 2)
    
    # Presión de vapor actual según FAO-56 Eq. 17
    e_a = e_s * (rh / 100.0)
    
    # Déficit de presión de vapor
    vpd = e_s - e_a
    
    # Convertir radiación solar de W/m² a MJ/m²/día
    # W/m² es potencia instantánea, necesitamos energía diaria
    # Asumimos que es medición cerca del máximo solar del día
    # Factor conservador: 0.0864 * 0.5 (asume 12h efectivas)
    rs_mj = solar_rad_wm2 * 0.0864 * 0.5
    
    # Radiación extraterrestre Ra según FAO-56
    ra = extraterrestrial_radiation(latitude_deg, day_of_year)
    
    # Radiación neta de onda corta según FAO-56 Eq. 38
    albedo = 0.23  # Para cultivo de referencia
    rns = (1 - albedo) * rs_mj
    
    # Radiación de cielo despejado según FAO-56
    rso = clear_sky_radiation(elevation_m, ra)
    
    # Radiación neta de onda larga según FAO-56 Eq. 39
    stefan_boltzmann = 4.903e-9  # MJ/K⁴/m²/día
    temp_k = temp_c + 273.16
    
    # Relación Rs/Rso (limitada entre 0.3 y 1.0)
    rs_rso = min(max(rs_mj / rso if rso > 0 else 0.7, 0.3), 1.0)
    
    rnl = stefan_boltzmann * (temp_k ** 4) * (0.34 - 0.14 * math.sqrt(e_a)) * (1.35 * rs_rso - 0.35)
    
    # Radiación neta según FAO-56 Eq. 40
    rn = rns - rnl
    
    # Flujo de calor del suelo (despreciable en escala diaria) según FAO-56
    g = 0.0
    
    # Término de radiación (numerador parte 1)
    radiation_term = 0.408 * delta * (rn - g)
    
    # Término aerodinámico (numerador parte 2) según FAO-56 Eq. 6
    wind_term = (gamma * 900 / (temp_c + 273)) * wind_ms * vpd
    
    # Denominador según FAO-56 Eq. 6
    denominator = delta + gamma * (1 + 0.34 * wind_ms)
    
    if denominator == 0:
        return float("nan")
    
    # ET0 según FAO-56 Eq. 6
    et0 = (radiation_term + wind_term) / denominator
    
    return max(et0, 0.0)


# ============================================================
# ÍNDICES Y ETIQUETAS
# ============================================================

def sky_clarity_index(
    solar_rad: float,              # W/m²
    latitude_deg: float,           # grados
    elevation_m: float,            # metros
    timestamp: float               # epoch
) -> float:
    """
    Índice de claridad del cielo (0-1)
    
    Indica qué porcentaje de la radiación máxima teórica está llegando.
    Usa la radiación de cielo despejado calculada con latitud y elevación.
    
    Args:
        solar_rad: Radiación solar medida en W/m²
        latitude_deg: Latitud en grados
        elevation_m: Elevación en metros
        timestamp: Timestamp Unix
        
    Returns:
        Índice entre 0 (nublado) y 1 (cielo despejado)
    """
    if is_nan(solar_rad) or solar_rad < 0:
        return float("nan")
    
    # Calcular radiación máxima teórica
    solar_max = solar_radiation_max_wm2(latitude_deg, elevation_m, timestamp)
    
    # Claridad como fracción de la radiación máxima
    clarity = solar_rad / solar_max if solar_max > 0 else 0.0
    
    # Limitar a [0, 1]
    return min(max(clarity, 0.0), 1.0)


def sky_clarity_label(clarity: float) -> str:
    """
    Etiqueta descriptiva del índice de claridad
    
    Args:
        clarity: Índice de claridad (0-1)
        
    Returns:
        Descripción textual
    """
    if is_nan(clarity):
        return "—"
    
    if clarity >= 0.8:
        return "Despejado"
    elif clarity >= 0.6:
        return "Poco nuboso"
    elif clarity >= 0.4:
        return "Parcialmente nuboso"
    elif clarity >= 0.2:
        return "Nuboso"
    else:
        return "Muy nuboso"


def uv_index_label(uv: float) -> str:
    """
    Etiqueta descriptiva del índice UV
    
    Args:
        uv: Índice UV (0-11+)
        
    Returns:
        Descripción del riesgo
    """
    if is_nan(uv):
        return "—"
    
    if uv < 3:
        return "Bajo"
    elif uv < 6:
        return "Moderado"
    elif uv < 8:
        return "Alto"
    elif uv < 11:
        return "Muy alto"
    else:
        return "Extremo"


def water_balance(precip_mm: float, et0_mm: float) -> float:
    """
    Balance hídrico simple
    
    Args:
        precip_mm: Precipitación acumulada en mm
        et0_mm: Evapotranspiración en mm
        
    Returns:
        Balance (P - ET0) en mm
    """
    if is_nan(precip_mm) or is_nan(et0_mm):
        return float("nan")
    
    return precip_mm - et0_mm


def water_balance_label(balance_mm: float) -> str:
    """
    Etiqueta descriptiva del balance hídrico
    
    Args:
        balance_mm: Balance en mm
        
    Returns:
        Descripción
    """
    if is_nan(balance_mm):
        return "—"
    
    if balance_mm > 5:
        return "Superávit"
    elif balance_mm > 0:
        return "Positivo"
    elif balance_mm > -5:
        return "Equilibrio"
    else:
        return "Déficit"

"""
Cálculos relacionados con radiación solar y evapotranspiración
"""
import math


def is_nan(x):
    """Verifica si un valor es NaN"""
    return x != x


def priestley_taylor_et0(
    solar_rad: float,  # W/m²
    temp_c: float,     # °C
    rh: float,         # %
    p_hpa: float       # hPa
) -> float:
    """
    Evapotranspiración de referencia (ET0) por método Priestley-Taylor simplificado
    
    Priestley-Taylor es una aproximación que no requiere datos de viento ni latitud.
    Asume condiciones húmedas y usa un coeficiente empírico (α = 1.26).
    
    Args:
        solar_rad: Radiación solar en W/m²
        temp_c: Temperatura del aire en °C
        rh: Humedad relativa en %
        p_hpa: Presión atmosférica en hPa
        
    Returns:
        ET0 en mm/día
        
    Referencias:
        Priestley & Taylor (1972)
        Allen et al. (1998) - FAO-56 simplificado
    """
    # Validar entradas
    if is_nan(solar_rad) or is_nan(temp_c) or is_nan(rh) or is_nan(p_hpa):
        return float("nan")
    
    if solar_rad < 0 or rh < 0 or rh > 100:
        return float("nan")
    
    # Constantes
    ALPHA = 1.26  # Coeficiente de Priestley-Taylor
    LAMBDA = 2.45  # Calor latente de vaporización (MJ/kg)
    GAMMA = 0.665e-3 * p_hpa  # Constante psicrométrica (kPa/°C)
    
    # Pendiente de la curva de presión de vapor (Δ) en kPa/°C
    # Ecuación de Tetens simplificada
    e_s = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    delta = (4098 * e_s) / ((temp_c + 237.3) ** 2)
    
    # Convertir radiación de W/m² a MJ/m²/día
    # 1 W/m² durante 1 día = 0.0864 MJ/m²/día
    # Pero solar_rad es instantánea, asumimos promedio del día ≈ 0.4 * max
    # Para simplificar: usamos factor de conversión conservador
    rad_mj_day = solar_rad * 0.0864 * 0.5  # Factor 0.5 asume medición cerca del máximo
    
    # Radiación neta aproximada (asumiendo albedo 0.23)
    rn = rad_mj_day * (1 - 0.23)
    
    # Flujo de calor del suelo (despreciable en escala diaria)
    g = 0.0
    
    # ET0 por Priestley-Taylor (mm/día)
    if (delta + GAMMA) == 0:
        return float("nan")
    
    et0 = ALPHA * (delta / (delta + GAMMA)) * ((rn - g) / LAMBDA)
    
    # Ajuste por humedad (opcional, reduce ET0 en condiciones muy húmedas)
    if rh > 80:
        humid_factor = 1.0 - 0.1 * ((rh - 80) / 20)  # Reduce hasta 10% si RH=100%
        et0 *= max(humid_factor, 0.9)
    
    return max(et0, 0.0)


def sky_clarity_index(
    solar_rad: float,  # W/m²
    solar_max_theoretical: float = 1000.0  # W/m² (aproximación sin latitud)
) -> float:
    """
    Índice de claridad del cielo (0-1)
    
    Indica qué porcentaje de la radiación máxima teórica está llegando.
    Sin latitud/fecha, usamos un máximo teórico conservador.
    
    Args:
        solar_rad: Radiación solar medida en W/m²
        solar_max_theoretical: Radiación máxima teórica en W/m²
        
    Returns:
        Índice entre 0 (nublado) y 1 (cielo despejado)
    """
    if is_nan(solar_rad) or solar_rad < 0:
        return float("nan")
    
    # Claridad como fracción de la radiación máxima
    clarity = solar_rad / solar_max_theoretical
    
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
    Balance hídrico diario
    
    Balance = Precipitación - Evapotranspiración
    
    Args:
        precip_mm: Precipitación acumulada del día en mm
        et0_mm: ET0 estimada del día en mm
        
    Returns:
        Balance en mm (positivo = exceso, negativo = déficit)
    """
    if is_nan(precip_mm) or is_nan(et0_mm):
        return float("nan")
    
    return precip_mm - et0_mm


def water_balance_label(balance: float) -> str:
    """
    Etiqueta descriptiva del balance hídrico
    
    Args:
        balance: Balance en mm
        
    Returns:
        Descripción
    """
    if is_nan(balance):
        return "—"
    
    if balance > 5:
        return "Exceso"
    elif balance > 0:
        return "Superávit"
    elif balance > -2:
        return "Equilibrado"
    elif balance > -5:
        return "Déficit leve"
    else:
        return "Déficit"

"""
Cálculos relacionados con radiación solar y evapotranspiración según FAO-56
"""
import math
from datetime import datetime, timedelta


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
    acos_arg = -math.tan(latitude_rad) * math.tan(declination_rad)
    # Evita errores de dominio por redondeo numérico (p. ej. 1.0000000002).
    acos_arg = max(-1.0, min(1.0, float(acos_arg)))
    return math.acos(acos_arg)


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
        ra: Radiación extraterrestre (mismas unidades objetivo)
        
    Returns:
        Rso en las mismas unidades que `ra`
    """
    # FAO-56 Eq. 37 para ausencia de turbidez
    rso = (0.75 + 2e-5 * elevation_m) * ra
    return rso


def _seasonal_correction_solar_time(day_of_year: int) -> float:
    """
    Corrección estacional del tiempo solar (Sc) en horas.
    FAO-56 Eq. 32/33.
    """
    b = 2.0 * math.pi * (day_of_year - 81) / 364.0
    return 0.1645 * math.sin(2.0 * b) - 0.1255 * math.cos(b) - 0.025 * math.sin(b)


def extraterrestrial_radiation_short_period(
    latitude_deg: float,
    longitude_deg: float,
    timestamp: float,
    period_minutes: float = 1.0,
) -> float:
    """
    Radiación extraterrestre para periodos horarios o menores.
    FAO-56 Eq. 28 (con Eq. 29-33 para ángulo horario solar).

    Returns:
        Ra en MJ/m²/h para el periodo centrado en `timestamp`.
    """
    if is_nan(latitude_deg) or period_minutes <= 0:
        return float("nan")

    lon_deg = 0.0 if is_nan(longitude_deg) else float(longitude_deg)
    dt_utc = datetime.utcfromtimestamp(timestamp)

    # Día del año en hora solar aproximada (UTC + lon/15).
    dt_solar = dt_utc + timedelta(hours=(lon_deg / 15.0))
    day_of_year = dt_solar.timetuple().tm_yday

    lat_rad = math.radians(latitude_deg)
    delta = solar_declination(day_of_year)
    dr = inverse_relative_distance(day_of_year)
    sc = _seasonal_correction_solar_time(day_of_year)  # horas

    t_mid_utc_h = (
        dt_utc.hour
        + dt_utc.minute / 60.0
        + dt_utc.second / 3600.0
        + dt_utc.microsecond / 3_600_000_000.0
    )

    # Ángulo horario solar al punto medio del periodo (Eq. 31).
    omega = (math.pi / 12.0) * ((t_mid_utc_h + lon_deg / 15.0 + sc) - 12.0)
    period_h = float(period_minutes) / 60.0
    omega_1 = omega - (math.pi * period_h / 24.0)  # Eq. 29
    omega_2 = omega + (math.pi * period_h / 24.0)  # Eq. 30

    # Recortar al intervalo diurno [−ws, ws].
    ws = sunset_hour_angle(lat_rad, delta)
    omega_1 = max(-ws, min(ws, omega_1))
    omega_2 = max(-ws, min(ws, omega_2))
    if omega_2 <= omega_1:
        return 0.0

    GSC = 0.0820  # MJ/m²/min
    ra = (12.0 * 60.0 / math.pi) * GSC * dr * (
        (omega_2 - omega_1) * math.sin(lat_rad) * math.sin(delta)
        + math.cos(lat_rad) * math.cos(delta) * (math.sin(omega_2) - math.sin(omega_1))
    )
    return max(float(ra), 0.0)


def solar_radiation_max_wm2(
    latitude_deg: float,
    elevation_m: float,
    timestamp: float,
    longitude_deg: float = float("nan"),
    period_minutes: float = 1.0,
) -> float:
    """
    Radiación solar máxima teórica instantánea en W/m²
    
    Calcula la radiación máxima teórica para el instante de medida,
    usando Ra de periodo corto (Eq. 28, FAO-56) en una ventana de 1 minuto.
    
    Args:
        latitude_deg: Latitud en grados
        elevation_m: Elevación en metros
        timestamp: Timestamp Unix
        
    Returns:
        Radiación máxima teórica instantánea en W/m²
    """
    elev = 0.0 if is_nan(elevation_m) else float(elevation_m)
    ra_short = extraterrestrial_radiation_short_period(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        timestamp=timestamp,
        period_minutes=period_minutes,
    )
    if is_nan(ra_short):
        return float("nan")

    # Rso = (0.75 + 2e-5 z) * Ra, en las mismas unidades que Ra.
    rso_mj_per_h = clear_sky_radiation(elev, ra_short)

    # MJ/m²/h -> W/m²
    rso_wm2 = (rso_mj_per_h * 1_000_000.0) / 3600.0
    return max(float(rso_wm2), 0.0)


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
    timestamp: float,              # epoch
    longitude_deg: float = float("nan")
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

    # Durante la noche no tiene sentido etiquetar claridad del cielo con este índice.
    if is_nighttime(latitude_deg, timestamp, longitude_deg):
        return float("nan")
    
    # Calcular radiación máxima teórica
    solar_max = solar_radiation_max_wm2(
        latitude_deg,
        elevation_m,
        timestamp,
        longitude_deg=longitude_deg,
        period_minutes=1.0,
    )
    
    # Claridad como fracción de la radiación máxima
    clarity = solar_rad / solar_max if solar_max > 0 else 0.0
    
    # Limitar a [0, 1]
    return min(max(clarity, 0.0), 1.0)


def _astral_sun_times(latitude_deg: float, longitude_deg: float, timestamp: float):
    """Devuelve orto/ocaso usando Astral para la fecha local del timestamp."""
    if is_nan(latitude_deg) or is_nan(longitude_deg):
        return None, None

    try:
        from astral import Observer
        from astral.sun import sun
    except Exception:
        return None, None

    try:
        dt_local = datetime.fromtimestamp(timestamp).astimezone()
        tzinfo = dt_local.tzinfo
        observer = Observer(latitude=float(latitude_deg), longitude=float(longitude_deg))
        sun_data = sun(observer, date=dt_local.date(), tzinfo=tzinfo)
        return sun_data.get("sunrise"), sun_data.get("sunset")
    except Exception:
        return None, None


def _sunrise_sunset_api_times(latitude_deg: float, longitude_deg: float, timestamp: float):
    """Fallback online opcional vía sunrise-sunset.org en hora local del sistema."""
    if is_nan(latitude_deg) or is_nan(longitude_deg):
        return None, None
    try:
        import requests
    except Exception:
        return None, None

    try:
        dt_local = datetime.fromtimestamp(timestamp).astimezone()
        date_str = dt_local.strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.sunrise-sunset.org/json",
            params={
                "lat": f"{float(latitude_deg):.6f}",
                "lng": f"{float(longitude_deg):.6f}",
                "date": date_str,
                "formatted": 0,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            return None, None
        payload = resp.json()
        if payload.get("status") != "OK":
            return None, None

        sr = payload.get("results", {}).get("sunrise")
        ss = payload.get("results", {}).get("sunset")
        if not sr or not ss:
            return None, None

        sunrise_utc = datetime.fromisoformat(str(sr).replace("Z", "+00:00"))
        sunset_utc = datetime.fromisoformat(str(ss).replace("Z", "+00:00"))
        sunrise_local = sunrise_utc.astimezone(dt_local.tzinfo)
        sunset_local = sunset_utc.astimezone(dt_local.tzinfo)
        return sunrise_local, sunset_local
    except Exception:
        return None, None


def _fallback_sunrise_sunset_minutes(latitude_deg: float, longitude_deg: float, timestamp: float):
    """Fallback NOAA (aprox.) de orto/ocaso en minutos locales desde medianoche."""
    if is_nan(latitude_deg) or is_nan(longitude_deg):
        return None, None

    dt_local = datetime.fromtimestamp(timestamp).astimezone()
    n = dt_local.timetuple().tm_yday

    gamma = 2.0 * math.pi / 365.0 * (n - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    lat_rad = math.radians(latitude_deg)
    cos_ha = (
        math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(decl))
        - math.tan(lat_rad) * math.tan(decl)
    )

    if cos_ha >= 1.0:
        return None, None  # noche polar
    if cos_ha <= -1.0:
        return 0.0, 24.0 * 60.0  # sol de medianoche

    ha_deg = math.degrees(math.acos(cos_ha))
    tz_offset_min = (dt_local.utcoffset().total_seconds() / 60.0) if dt_local.utcoffset() else 0.0

    solar_noon = 720.0 - (4.0 * longitude_deg) - eqtime + tz_offset_min
    sunrise_min = solar_noon - (4.0 * ha_deg)
    sunset_min = solar_noon + (4.0 * ha_deg)

    return sunrise_min, sunset_min


def _fmt_local_minutes(minutes_val):
    if minutes_val is None:
        return "—"
    m = int(round(minutes_val)) % (24 * 60)
    hh = m // 60
    mm = m % 60
    return f"{hh:02d}:{mm:02d}"


def sunrise_sunset_label(latitude_deg: float, longitude_deg: float, timestamp: float) -> str:
    sunrise, sunset = _astral_sun_times(latitude_deg, longitude_deg, timestamp)
    if sunrise is None or sunset is None:
        sunrise, sunset = _sunrise_sunset_api_times(latitude_deg, longitude_deg, timestamp)
    if sunrise is None or sunset is None:
        sunrise_min, sunset_min = _fallback_sunrise_sunset_minutes(latitude_deg, longitude_deg, timestamp)
        return f"Orto {_fmt_local_minutes(sunrise_min)} · Ocaso {_fmt_local_minutes(sunset_min)}"

    return f"Orto {sunrise.strftime('%H:%M')} · Ocaso {sunset.strftime('%H:%M')}"


def is_nighttime(latitude_deg: float, timestamp: float, longitude_deg: float = float("nan")) -> bool:
    """Determina noche usando Astral cuando hay coordenadas completas."""
    sunrise, sunset = _astral_sun_times(latitude_deg, longitude_deg, timestamp)
    if sunrise is None or sunset is None:
        sunrise, sunset = _sunrise_sunset_api_times(latitude_deg, longitude_deg, timestamp)
    if sunrise is not None and sunset is not None:
        try:
            now_local = datetime.fromtimestamp(timestamp, tz=sunrise.tzinfo)
        except Exception:
            now_local = datetime.fromtimestamp(timestamp)
        return now_local < sunrise or now_local > sunset

    sunrise_min, sunset_min = _fallback_sunrise_sunset_minutes(latitude_deg, longitude_deg, timestamp)
    if sunrise_min is None or sunset_min is None:
        return False

    dt_local = datetime.fromtimestamp(timestamp).astimezone()
    now_min = dt_local.hour * 60.0 + dt_local.minute + dt_local.second / 60.0
    sunrise_min = sunrise_min % (24 * 60)
    sunset_min = sunset_min % (24 * 60)
    return now_min < sunrise_min or now_min > sunset_min


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

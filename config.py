"""
Configuración global de MeteoLabX
"""

# ============================================================
# CONFIGURACIÓN DE REFRESCO
# ============================================================
REFRESH_SECONDS = 30  # Público: mejor 60s (menos carga/abuso)
MIN_REFRESH_SECONDS = 15  # Mínimo recomendado para evitar rate limit

# ============================================================
# API WEATHER UNDERGROUND
# ============================================================
WU_URL = "https://api.weather.com/v2/pws/observations/current"
WU_TIMEOUT_SECONDS = 15
MAX_DATA_AGE_MINUTES = 30  # Advertir si datos son más antiguos

# ============================================================
# CORRECCIÓN DE DIRECCIÓN DEL VIENTO
# ============================================================
WIND_DIR_OFFSET_DEG = 30.0

# ============================================================
# KEYS PARA LOCALSTORAGE
# ============================================================
LS_STATION = "meteolabx_active_station"
LS_APIKEY = "meteolabx_active_key"
LS_Z = "meteolabx_active_z"
LS_AUTOCONNECT = "meteolabx_auto_connect"
LS_AUTOCONNECT_TARGET = "meteolabx_auto_connect_target"
LS_WU_FORGOTTEN = "meteolabx_wu_forgotten"

# ============================================================
# CACHE
# ============================================================
MAX_CACHE_SIZE = 10  # Máximo número de estaciones en cache

# ============================================================
# CONSTANTES FÍSICAS
# ============================================================
G0 = 9.80665  # Gravedad estándar (m/s²)
RD = 287.05   # Constante del gas del aire seco (J/(kg·K))
RV = 461.5    # Constante del gas del vapor de agua (J/(kg·K))

# ============================================================
# CONSTANTES TERMODINÁMICAS
# ============================================================
EPSILON = 0.622  # Rd/Rv - ratio de constantes de gas
EPSILON_COMP = 0.378  # 1 - epsilon, para cálculos de humedad
KAPPA = 0.286  # Rd/cp - exponente adiabático
TV_COEF = 0.61  # Coeficiente para temperatura virtual
CP = 1004.0  # Calor específico del aire a presión constante (J/(kg·K))
LV = 2.5e6  # Calor latente de vaporización (J/kg)
LCL_FACTOR = 125.0  # Factor de conversión para LCL (m/°C)

# ============================================================
# UMBRALES DE HEAT INDEX Y WIND CHILL
# ============================================================
HEAT_INDEX_MIN_TEMP = 25.0  # °C - temperatura mínima para calcular heat index
WIND_CHILL_MAX_TEMP = 10.0  # °C - temperatura máxima para wind chill
WIND_CHILL_MIN_SPEED = 4.8  # km/h - velocidad mínima para wind chill

# ============================================================
# UMBRALES DE INTENSIDAD DE LLUVIA (mm/h)
# ============================================================
RAIN_TRACE = 0.4
RAIN_VERY_LIGHT = 1.0
RAIN_LIGHT = 2.5
RAIN_MODERATE_LIGHT = 6.5
RAIN_MODERATE = 16.0
RAIN_HEAVY = 40.0
RAIN_VERY_HEAVY = 100.0

# ============================================================
# UMBRALES DE TENDENCIA DE PRESIÓN (hPa)
# ============================================================
PRESSURE_STABLE_THRESHOLD = 0.2
PRESSURE_RAPID_CHANGE = 2.0

# ============================================================
# VALIDACIÓN DE ENTRADA
# ============================================================
MIN_ALTITUDE_M = -500  # Mínima altitud válida (Mar Muerto)
MAX_ALTITUDE_M = 9000  # Máxima altitud válida (estaciones más altas)

# ============================================================
# CUANTIZACIÓN DE LLUVIA
# ============================================================
RAIN_QUANTIZE_CORRECTION = 1.0049
RAIN_TIP_RESOLUTION = 0.4  # mm por tip del pluviómetro

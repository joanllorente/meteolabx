"""
Versión alternativa: Calcular extremos diarios localmente
Guarda histórico en session_state y calcula máx/mín del día actual
"""
import streamlit as st
from datetime import datetime, date
from collections import defaultdict


def init_daily_extremes():
    """
    Inicializa el histórico de extremos diarios en session_state
    """
    if "daily_extremes" not in st.session_state:
        st.session_state.daily_extremes = {
            "date": None,  # Fecha actual
            "temp_values": [],
            "rh_values": [],
            "gust_values": [],
        }


def update_daily_extremes(Tc, RH, gust, epoch):
    """
    Actualiza los extremos diarios con el nuevo dato
    
    Args:
        Tc: Temperatura actual (°C)
        RH: Humedad relativa actual (%)
        gust: Racha de viento actual (km/h)
        epoch: Timestamp del dato
    """
    init_daily_extremes()
    
    # Obtener fecha actual
    current_date = datetime.fromtimestamp(epoch).date()
    
    # Si cambió el día, resetear
    if st.session_state.daily_extremes["date"] != current_date:
        st.session_state.daily_extremes = {
            "date": current_date,
            "temp_values": [],
            "rh_values": [],
            "gust_values": [],
        }
    
    # Agregar valores actuales
    extremes = st.session_state.daily_extremes
    
    if Tc is not None and Tc == Tc:  # No es NaN
        extremes["temp_values"].append(Tc)
    
    if RH is not None and RH == RH:  # No es NaN
        extremes["rh_values"].append(RH)
    
    if gust is not None and gust == gust:  # No es NaN
        extremes["gust_values"].append(gust)


def get_daily_extremes():
    """
    Obtiene los extremos diarios calculados
    
    Returns:
        Dict con temp_max, temp_min, rh_max, rh_min, gust_max
    """
    init_daily_extremes()
    
    extremes = st.session_state.daily_extremes
    
    result = {
        "temp_max": float("nan"),
        "temp_min": float("nan"),
        "rh_max": float("nan"),
        "rh_min": float("nan"),
        "gust_max": float("nan"),
    }
    
    # Calcular extremos de temperatura
    if extremes["temp_values"]:
        result["temp_max"] = max(extremes["temp_values"])
        result["temp_min"] = min(extremes["temp_values"])
    
    # Calcular extremos de humedad
    if extremes["rh_values"]:
        result["rh_max"] = max(extremes["rh_values"])
        result["rh_min"] = min(extremes["rh_values"])
    
    # Calcular racha máxima
    if extremes["gust_values"]:
        result["gust_max"] = max(extremes["gust_values"])
    
    return result


# ==============================================================================
# EJEMPLO DE USO EN app.py
# ==============================================================================
"""
# Después de obtener los datos de WU:
base = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)

# Actualizar extremos locales
update_daily_extremes(
    Tc=base["Tc"],
    RH=base["RH"],
    gust=base["gust"],
    epoch=base["epoch"]
)

# Obtener extremos calculados
local_extremes = get_daily_extremes()

# Usar en lugar de los de la API
temp_max = local_extremes["temp_max"]
temp_min = local_extremes["temp_min"]
rh_max = local_extremes["rh_max"]
rh_min = local_extremes["rh_min"]
gust_max = local_extremes["gust_max"]

# Resto del código igual...
temp_side = ""
if not is_nan(temp_max) and not is_nan(temp_min):
    temp_side = (
        f"<div class='max'>▲ {temp_max:.1f}°</div>"
        f"<div class='min'>▼ {temp_min:.1f}°</div>"
    )
"""

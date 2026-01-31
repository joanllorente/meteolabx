"""
Componentes de sidebar y funciones auxiliares
"""
import streamlit as st
from config import LS_STATION, LS_APIKEY, LS_Z
from utils.storage import set_local_storage
from utils.helpers import normalize_text_input, is_nan


def wind_dir_text(deg: float) -> str:
    """
    Convierte grados a dirección cardinal
    
    Args:
        deg: Grados (0-360)
        
    Returns:
        Dirección cardinal (ej: "NNE")
    """
    if is_nan(deg):
        return "—"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    i = int((deg + 11.25) // 22.5) % 16
    return dirs[i]


def wind_name_cat(deg: float) -> str:
    """
    Nombre catalán del viento según dirección
    
    Args:
        deg: Grados (0-360)
        
    Returns:
        Nombre del viento en catalán
    """
    if is_nan(deg):
        return "—"
    deg = deg % 360
    if deg >= 337.5 or deg < 22.5:
        return "Tramuntana"
    elif 22.5 <= deg < 67.5:
        return "Gregal"
    elif 67.5 <= deg < 112.5:
        return "Llevant"
    elif 112.5 <= deg < 157.5:
        return "Xaloc"
    elif 157.5 <= deg < 202.5:
        return "Migjorn"
    elif 202.5 <= deg < 247.5:
        return "Garbí"
    elif 247.5 <= deg < 292.5:
        return "Ponent"
    elif 292.5 <= deg < 337.5:
        return "Mestral"
    return "—"


def render_sidebar(localS):
    """
    Renderiza la barra lateral con configuración
    
    Args:
        localS: Instancia de LocalStorage
        
    Returns:
        Tupla (theme_mode, dark)
    """
    from datetime import datetime
    from utils.storage import get_stored_station, get_stored_apikey, get_stored_z
    
    # Prefill desde localStorage
    saved_station = get_stored_station()
    saved_key = get_stored_apikey()
    saved_z = get_stored_z()

    active_station = st.session_state.get("active_station", "")
    active_key = st.session_state.get("active_key", "")
    active_z = st.session_state.get("active_z", "0")

    if not active_station and saved_station:
        st.session_state["active_station"] = saved_station
    if not active_key and saved_key:
        st.session_state["active_key"] = saved_key
    if (not str(active_z).strip() or active_z == "0") and saved_z:
        st.session_state["active_z"] = normalize_text_input(saved_z)

    st.session_state["active_z"] = normalize_text_input(st.session_state.get("active_z"))

    # Tema
    st.sidebar.title("⚙️ Ajustes")
    theme_mode = st.sidebar.radio("Tema", ["Auto", "Claro", "Oscuro"], index=0)

    # Conectar estación
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔌 Conectar estación")
    
    # Aplicar borrado si está marcado (ANTES de crear widgets)
    if st.session_state.get("_clear_inputs", False):
        for key in ["active_station", "active_key", "active_z"]:
            if key in st.session_state:
                del st.session_state[key]
        del st.session_state["_clear_inputs"]

    st.sidebar.text_input("Station ID (WU)", key="active_station", placeholder="Introducir ID")
    st.sidebar.text_input("API Key (WU)", key="active_key", type="password", placeholder="Pega aquí tu API key")
    st.sidebar.text_input("Altitud (m)", key="active_z", placeholder="Ej: 12.5")

    st.sidebar.caption("Este panel consulta Weather Underground usando tu propia API key. No se almacena en disco.")

    # Recordar en dispositivo
    st.sidebar.markdown("---")
    remember_device = st.sidebar.checkbox("Recordar en este dispositivo", value=True)
    st.sidebar.caption("⚠️ Si es un ordenador compartido, desactívalo o pulsa 'Olvidar' al terminar.")

    cS, cF = st.sidebar.columns(2)
    with cS:
        save_clicked = st.button("Guardar", use_container_width=True)
    with cF:
        forget_clicked = st.button("Olvidar", use_container_width=True)

    if save_clicked:
        if remember_device:
            set_local_storage(LS_STATION, st.session_state["active_station"], "save")
            set_local_storage(LS_APIKEY, st.session_state["active_key"], "save")
            set_local_storage(LS_Z, str(st.session_state["active_z"]), "save")
            st.sidebar.success("Guardado en este dispositivo ✅")
        else:
            st.sidebar.info("Activa 'Recordar en este dispositivo' para guardar.")

    if forget_clicked:
        # Borrar de localStorage
        set_local_storage(LS_STATION, "", "forget")
        set_local_storage(LS_APIKEY, "", "forget")
        set_local_storage(LS_Z, "", "forget")
        
        # Marcar para borrar en el próximo ciclo
        st.session_state["_clear_inputs"] = True
        st.session_state["connected"] = False
        
        # Limpiar caché de API
        if "wu_cache_current" in st.session_state:
            st.session_state["wu_cache_current"] = {}
        if "wu_cache_daily" in st.session_state:
            st.session_state["wu_cache_daily"] = {}
        
        st.sidebar.success("✅ Datos borrados")
        st.rerun()

    # Estado conectado
    if "connected" not in st.session_state:
        st.session_state["connected"] = False

    colA, colB = st.sidebar.columns(2)
    with colA:
        connect_clicked = st.button("Conectar", use_container_width=True)
    with colB:
        disconnect_clicked = st.button("Desconectar", use_container_width=True)

    if disconnect_clicked:
        st.session_state["connected"] = False

    if connect_clicked:
        station = str(st.session_state.get("active_station", "")).strip()
        key = str(st.session_state.get("active_key", "")).strip()
        z_raw = str(st.session_state.get("active_z", "0")).strip()

        if not station or not key:
            st.sidebar.error("Falta Station ID o API key.")
        else:
            try:
                z_float = float(z_raw)
                # Validar rango de altitud
                from config import MIN_ALTITUDE_M, MAX_ALTITUDE_M
                if not (MIN_ALTITUDE_M <= z_float <= MAX_ALTITUDE_M):
                    st.sidebar.error(f"Altitud fuera de rango ({MIN_ALTITUDE_M} a {MAX_ALTITUDE_M}m)")
                else:
                    st.session_state["connected"] = True
                    if remember_device:
                        set_local_storage(LS_STATION, station, "connect")
                        set_local_storage(LS_APIKEY, key, "connect")
                        set_local_storage(LS_Z, z_raw, "connect")
            except Exception:
                st.sidebar.error("Altitud inválida. Usa un número (ej: 12.5)")

    if st.session_state.get("connected"):
        st.sidebar.success(f"Conectado: {st.session_state.get('active_station','')}")
    else:
        st.sidebar.info("No conectado")

    # Determinar tema
    now = datetime.now()
    auto_dark = (now.hour >= 20) or (now.hour <= 7)
    
    if theme_mode == "Auto":
        dark = auto_dark
    elif theme_mode == "Oscuro":
        dark = True
    else:
        dark = False

    return theme_mode, dark

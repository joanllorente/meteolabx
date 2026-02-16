"""
Componentes de sidebar y funciones auxiliares
"""
import streamlit as st
import os
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
    
    # Prefill desde localStorage activado por defecto para persistir credenciales
    # entre recargas locales; puede desactivarse con MLX_ENABLE_LOCAL_PREFILL=0.
    allow_local_prefill = os.getenv("MLX_ENABLE_LOCAL_PREFILL", "1") == "1"

    if allow_local_prefill:
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

    # Si hay conexión WU activa, mantener credenciales en sesión aunque un rerun
    # temporalmente deje vacíos los widgets de entrada (ej. cambio de tema).
    if st.session_state.get("connected") and st.session_state.get("connection_type") == "WU":
        if not str(st.session_state.get("active_station", "")).strip():
            st.session_state["active_station"] = str(st.session_state.get("wu_connected_station", "")).strip()
        if not str(st.session_state.get("active_key", "")).strip():
            st.session_state["active_key"] = str(st.session_state.get("wu_connected_api_key", "")).strip()
        if not str(st.session_state.get("active_z", "")).strip():
            st.session_state["active_z"] = normalize_text_input(st.session_state.get("wu_connected_z", ""))

    # Tema
    st.sidebar.title("⚙️ Ajustes")
    
    # Obtener índice guardado o usar 0 por defecto
    if "theme_index" not in st.session_state:
        st.session_state["theme_index"] = 0
    
    theme_mode = st.sidebar.radio(
        "Tema", 
        ["Auto", "Claro", "Oscuro"], 
        index=st.session_state["theme_index"],
        key="theme_selector"
    )
    
    # Actualizar índice si cambió
    theme_options = ["Auto", "Claro", "Oscuro"]
    new_index = theme_options.index(theme_mode)
    if st.session_state["theme_index"] != new_index:
        st.session_state["theme_index"] = new_index
        st.rerun()

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
    st.sidebar.text_input("Altitud (m)", key="active_z", placeholder="Opcional (se obtiene de API)")
    
    st.sidebar.caption("Este panel consulta Weather Underground usando tu propia API key. No se almacena en disco.")

    # Recordar en dispositivo
    st.sidebar.markdown("---")
    remember_default = bool(st.session_state.get("remember_device", True))
    remember_device = st.sidebar.checkbox("Recordar en este dispositivo", value=remember_default, key="remember_device")
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
            # Si se desactiva recordar, limpiar lo persistido para evitar ambigüedad.
            set_local_storage(LS_STATION, "", "save")
            set_local_storage(LS_APIKEY, "", "save")
            set_local_storage(LS_Z, "", "save")
            st.sidebar.info("Activa 'Recordar en este dispositivo' para guardar.")

    if forget_clicked:
        # Borrar de localStorage
        set_local_storage(LS_STATION, "", "forget")
        set_local_storage(LS_APIKEY, "", "forget")
        set_local_storage(LS_Z, "", "forget")
        
        # Marcar para borrar en el próximo ciclo
        st.session_state["_clear_inputs"] = True
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        for key in ["wu_connected_station", "wu_connected_api_key", "wu_connected_z"]:
            if key in st.session_state:
                del st.session_state[key]
        
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

    def render_connection_banner(text: str, connected_state: bool):
        """Banner de estado con texto tintado (sin blanco puro)."""
        now_local = datetime.now()
        auto_dark_local = (now_local.hour >= 20) or (now_local.hour <= 7)
        is_dark_ui = (
            theme_mode == "Oscuro" or
            (theme_mode == "Auto" and auto_dark_local)
        )

        if connected_state:
            bg = "rgba(61, 114, 87, 0.42)" if is_dark_ui else "rgba(55, 140, 88, 0.18)"
            fg = "rgb(176, 231, 199)" if is_dark_ui else "rgb(28, 104, 61)"
        else:
            bg = "rgba(57, 86, 125, 0.45)" if is_dark_ui else "rgba(66, 133, 244, 0.16)"
            fg = "rgb(64, 166, 255)" if is_dark_ui else "rgb(35, 112, 208)"

        st.sidebar.markdown(
            f"""
            <div class="mlbx-status-banner" style="
                --mlbx-banner-fg: {fg};
                margin-top: 8px;
                padding: 14px 16px;
                border-radius: 14px;
                border: none;
                background: {bg};
                font-size: 0.95rem;
                font-weight: 500;
            "><span class="mlbx-status-banner-text">{text}</span></div>
            """,
            unsafe_allow_html=True,
        )

    colA, colB = st.sidebar.columns(2)
    with colA:
        connect_clicked = st.button("Conectar", use_container_width=True)
    with colB:
        disconnect_clicked = st.button("Desconectar", use_container_width=True)

    if disconnect_clicked:
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        for key in ["wu_connected_station", "wu_connected_api_key", "wu_connected_z"]:
            if key in st.session_state:
                del st.session_state[key]
        for state_key in list(st.session_state.keys()):
            if (
                state_key.startswith('aemet_')
                or state_key.startswith('provider_station_')
                or state_key.startswith('meteocat_')
                or state_key.startswith('euskalmet_')
            ):
                del st.session_state[state_key]

    if connect_clicked:
        station = str(st.session_state.get("active_station", "")).strip()
        api_key = str(st.session_state.get("active_key", "")).strip()
        z_raw = str(st.session_state.get("active_z", "")).strip()

        if not station or not api_key:
            st.sidebar.error("Falta Station ID o API key.")
        else:
            # Conexión explícita de Weather Underground
            st.session_state["connection_type"] = "WU"
            # Limpiar restos de conexión por proveedor para evitar UI duplicada
            for state_key in list(st.session_state.keys()):
                if (
                    state_key.startswith('aemet_')
                    or state_key.startswith('provider_station_')
                    or state_key.startswith('meteocat_')
                    or state_key.startswith('euskalmet_')
                ):
                    del st.session_state[state_key]

            # Validar altitud si se proporcionó
            if z_raw:  # Si hay altitud manual
                try:
                    z_float = float(z_raw)
                    # Validar rango de altitud
                    from config import MIN_ALTITUDE_M, MAX_ALTITUDE_M
                    if not (MIN_ALTITUDE_M <= z_float <= MAX_ALTITUDE_M):
                        st.sidebar.error(f"Altitud fuera de rango ({MIN_ALTITUDE_M} a {MAX_ALTITUDE_M}m)")
                    else:
                        st.session_state["connected"] = True
                        st.session_state["wu_connected_station"] = station
                        st.session_state["wu_connected_api_key"] = api_key
                        st.session_state["wu_connected_z"] = z_raw
                        if remember_device:
                            set_local_storage(LS_STATION, station, "connect")
                            set_local_storage(LS_APIKEY, api_key, "connect")
                            set_local_storage(LS_Z, z_raw, "connect")
                            set_local_storage(LS_STATION, station, "save")
                            set_local_storage(LS_APIKEY, api_key, "save")
                            set_local_storage(LS_Z, z_raw, "save")
                        else:
                            set_local_storage(LS_STATION, "", "connect")
                            set_local_storage(LS_APIKEY, "", "connect")
                            set_local_storage(LS_Z, "", "connect")
                            set_local_storage(LS_STATION, "", "save")
                            set_local_storage(LS_APIKEY, "", "save")
                            set_local_storage(LS_Z, "", "save")
                except Exception:
                    st.sidebar.error("Altitud inválida. Usa un número (ej: 12.5)")
            else:  # Sin altitud manual, confiar en la API
                st.session_state["connected"] = True
                st.session_state["wu_connected_station"] = station
                st.session_state["wu_connected_api_key"] = api_key
                st.session_state["wu_connected_z"] = ""
                if remember_device:
                    set_local_storage(LS_STATION, station, "connect")
                    set_local_storage(LS_APIKEY, api_key, "connect")
                    set_local_storage(LS_Z, "", "connect")
                    set_local_storage(LS_STATION, station, "save")
                    set_local_storage(LS_APIKEY, api_key, "save")
                    set_local_storage(LS_Z, "", "save")
                else:
                    set_local_storage(LS_STATION, "", "connect")
                    set_local_storage(LS_APIKEY, "", "connect")
                    set_local_storage(LS_Z, "", "connect")
                    set_local_storage(LS_STATION, "", "save")
                    set_local_storage(LS_APIKEY, "", "save")
                    set_local_storage(LS_Z, "", "save")

    if st.session_state.get("connected"):
        # Mostrar nombre según tipo de conexión
        if st.session_state.get("connection_type") == "AEMET":
            station_name = st.session_state.get('aemet_station_name', 'AEMET')
        elif st.session_state.get("provider_station_name"):
            station_name = st.session_state.get('provider_station_name', 'Estación')
        else:
            station_name = st.session_state.get('active_station') or st.session_state.get('wu_connected_station', '')
        
        render_connection_banner(f"Conectado: {station_name}", connected_state=True)
        
        # Mostrar última actualización
        if "last_update_time" in st.session_state:
            import time
            last_update = st.session_state["last_update_time"]
            elapsed = time.time() - last_update
            
            if elapsed < 60:
                time_str = f"hace {int(elapsed)}s"
            elif elapsed < 3600:
                time_str = f"hace {int(elapsed/60)}min"
            else:
                time_str = f"hace {int(elapsed/3600)}h {int((elapsed%3600)/60)}min"
            
            st.sidebar.caption(f"Última actualización: {time_str}")
    else:
        render_connection_banner("No conectado", connected_state=False)
    
    # ============================================================
    # MODO DEMO RADIACIÓN (SOLO DESARROLLO/INTERNO)
    # ============================================================
    # Solo visible si se ejecuta con: DEMO_MODE=1 streamlit run meteolabx.py
    
    demo_radiation = False
    demo_solar = None
    demo_uv = None
    
    if os.getenv("DEMO_MODE") == "1" or os.getenv("METEOLABX_DEMO") == "1":
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🔬 Modo Demo (Interno)")
        
        demo_radiation = st.sidebar.checkbox(
            "Activar datos de radiación demo",
            value=False,
            help="Muestra controles para simular datos de radiación solar y UV cuando tu estación no tiene estos sensores"
        )
        
        if demo_radiation:
            st.sidebar.caption("📊 **Simula datos de radiación**")
            demo_solar = st.sidebar.slider(
                "Radiación solar (W/m²)",
                min_value=0,
                max_value=1200,
                value=650,
                step=50,
                help="Valores típicos: Nublado 100-300, Parcialmente nublado 400-700, Despejado 800-1200"
            )
            demo_uv = st.sidebar.slider(
                "Índice UV",
                min_value=0.0,
                max_value=15.0,
                value=6.0,
                step=0.5,
                help="Valores típicos: Bajo 0-2, Moderado 3-5, Alto 6-7, Muy alto 8-10, Extremo 11+"
            )
            st.sidebar.caption("💡 **Referencia rápida:**")
            st.sidebar.caption("• ☁️ Nublado: Solar ~200, UV ~2")
            st.sidebar.caption("• ⛅ Parcial: Solar ~500, UV ~5")  
            st.sidebar.caption("• ☀️ Despejado: Solar ~900, UV ~8")
    
    # Guardar en session_state para acceso desde main
    st.session_state["demo_radiation"] = demo_radiation
    st.session_state["demo_solar"] = demo_solar
    st.session_state["demo_uv"] = demo_uv
    
    # Mostrar estado de conexión por proveedor si aplica
    from components.station_selector import show_provider_connection_status
    show_provider_connection_status()

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

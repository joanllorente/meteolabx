"""
Componentes de sidebar y funciones auxiliares
"""
import streamlit as st
import os
from config import LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT
from utils.storage import set_local_storage, set_stored_autoconnect_target
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
    from utils.storage import (
        get_stored_station,
        get_stored_apikey,
        get_stored_z,
        get_stored_autoconnect,
        get_stored_autoconnect_target,
    )
    
    # Prefill desde localStorage activado por defecto para persistir credenciales
    # entre recargas locales; puede desactivarse con MLX_ENABLE_LOCAL_PREFILL=0.
    allow_local_prefill = os.getenv("MLX_ENABLE_LOCAL_PREFILL", "1") == "1"

    if allow_local_prefill:
        saved_station = get_stored_station()
        saved_key = get_stored_apikey()
        saved_z = get_stored_z()
        saved_autoconnect = bool(get_stored_autoconnect())
        saved_target = get_stored_autoconnect_target()

        active_station = st.session_state.get("active_station", "")
        active_key = st.session_state.get("active_key", "")
        active_z = st.session_state.get("active_z", "0")

        if not active_station and saved_station:
            st.session_state["active_station"] = saved_station
        if not active_key and saved_key:
            st.session_state["active_key"] = saved_key
        if (not str(active_z).strip() or active_z == "0") and saved_z:
            st.session_state["active_z"] = normalize_text_input(saved_z)

        has_saved_credentials = bool(str(saved_station or "").strip() and str(saved_key or "").strip())
        target_kind = str((saved_target or {}).get("kind", "")).strip().upper()
        valid_wu_target = bool(target_kind == "WU" and has_saved_credentials)
        valid_provider_target = bool(
            target_kind == "PROVIDER"
            and str((saved_target or {}).get("provider_id", "")).strip()
            and str((saved_target or {}).get("station_id", "")).strip()
        )
        has_valid_target = valid_wu_target or valid_provider_target
        if not has_valid_target:
            saved_autoconnect = False

        # Estado UI del toggle de sidebar: solo representa auto-conexión WU.
        wu_toggle_default = bool(saved_autoconnect and valid_wu_target)
        current_target_kind = target_kind if has_valid_target else ""
        if st.session_state.get("_wu_autoconnect_ui_target_kind") != current_target_kind:
            st.session_state["auto_connect_wu_device"] = wu_toggle_default
            st.session_state["_wu_autoconnect_ui_target_kind"] = current_target_kind
            st.session_state["_wu_autoconnect_ui_last_value"] = wu_toggle_default
        elif "auto_connect_wu_device" not in st.session_state:
            st.session_state["auto_connect_wu_device"] = wu_toggle_default
            st.session_state["_wu_autoconnect_ui_last_value"] = wu_toggle_default

        # Autoconexion al abrir si hay target guardado y el toggle estaba activo.
        if (
            saved_autoconnect
            and has_valid_target
            and not st.session_state.get("connected", False)
            and not st.session_state.get("_autoconnect_attempted", False)
        ):
            if valid_wu_target:
                st.session_state["connection_type"] = "WU"
                st.session_state["connected"] = True
                st.session_state["wu_connected_station"] = str(saved_station).strip()
                st.session_state["wu_connected_api_key"] = str(saved_key).strip()
                st.session_state["wu_connected_z"] = normalize_text_input(saved_z or "")
                st.session_state["active_station"] = str(saved_station).strip()
                st.session_state["active_key"] = str(saved_key).strip()
                st.session_state["active_z"] = normalize_text_input(saved_z or "")
                st.session_state["_autoconnect_attempted"] = True
                st.rerun()
            elif valid_provider_target:
                provider_id = str(saved_target.get("provider_id", "")).strip().upper()
                station_id = str(saved_target.get("station_id", "")).strip()
                station_name = str(saved_target.get("station_name", "")).strip() or station_id
                lat = saved_target.get("lat")
                lon = saved_target.get("lon")
                elevation_m = saved_target.get("elevation_m")

                st.session_state["connection_type"] = provider_id
                st.session_state["provider_station_id"] = station_id
                st.session_state["provider_station_name"] = station_name
                st.session_state["provider_station_lat"] = lat
                st.session_state["provider_station_lon"] = lon
                st.session_state["provider_station_alt"] = elevation_m

                if provider_id == "AEMET":
                    st.session_state["aemet_station_id"] = station_id
                    st.session_state["aemet_station_name"] = station_name
                    st.session_state["aemet_station_lat"] = lat
                    st.session_state["aemet_station_lon"] = lon
                    st.session_state["aemet_station_alt"] = elevation_m
                elif provider_id == "METEOCAT":
                    st.session_state["meteocat_station_id"] = station_id
                    st.session_state["meteocat_station_name"] = station_name
                    st.session_state["meteocat_station_lat"] = lat
                    st.session_state["meteocat_station_lon"] = lon
                    st.session_state["meteocat_station_alt"] = elevation_m
                elif provider_id == "EUSKALMET":
                    st.session_state["euskalmet_station_id"] = station_id
                    st.session_state["euskalmet_station_name"] = station_name
                    st.session_state["euskalmet_station_lat"] = lat
                    st.session_state["euskalmet_station_lon"] = lon
                    st.session_state["euskalmet_station_alt"] = elevation_m
                elif provider_id == "METEOGALICIA":
                    st.session_state["meteogalicia_station_id"] = station_id
                    st.session_state["meteogalicia_station_name"] = station_name
                    st.session_state["meteogalicia_station_lat"] = lat
                    st.session_state["meteogalicia_station_lon"] = lon
                    st.session_state["meteogalicia_station_alt"] = elevation_m
                elif provider_id == "NWS":
                    st.session_state["nws_station_id"] = station_id
                    st.session_state["nws_station_name"] = station_name
                    st.session_state["nws_station_lat"] = lat
                    st.session_state["nws_station_lon"] = lon
                    st.session_state["nws_station_alt"] = elevation_m
                else:
                    st.session_state["auto_connect_wu_device"] = False
                    st.session_state["_autoconnect_attempted"] = True
                    st.rerun()

                st.session_state["connected"] = True
                st.session_state["_autoconnect_attempted"] = True
                st.rerun()

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
    
    theme_options = ["Auto", "Claro", "Oscuro"]
    if st.session_state.get("theme_selector") not in theme_options:
        st.session_state["theme_selector"] = theme_options[0]

    # Usar solo key/session_state (sin index manual) para evitar el doble clic.
    theme_mode = st.sidebar.radio(
        "Tema",
        theme_options,
        key="theme_selector",
    )

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
    
    st.sidebar.caption("Este panel consulta Weather Underground usando tu propia API key. Solo se guarda localmente si pulsas Guardar.")

    # Recordar en dispositivo
    st.sidebar.markdown("---")
    auto_connect_default = bool(st.session_state.get("auto_connect_wu_device", False))
    auto_connect_wu_device = st.sidebar.checkbox(
        "Conectar automáticamente al iniciar",
        value=auto_connect_default,
        key="auto_connect_wu_device",
    )
    st.sidebar.caption("Solo puede haber una auto-conexión activa; siempre se usa la última estación marcada.")

    if "_wu_autoconnect_ui_last_value" not in st.session_state:
        st.session_state["_wu_autoconnect_ui_last_value"] = auto_connect_default
    last_wu_toggle_value = bool(st.session_state.get("_wu_autoconnect_ui_last_value", auto_connect_default))
    if auto_connect_wu_device != last_wu_toggle_value:
        station_for_target = str(st.session_state.get("active_station", "")).strip()
        key_for_target = str(st.session_state.get("active_key", "")).strip()
        z_for_target = str(st.session_state.get("active_z", "")).strip()

        if auto_connect_wu_device:
            if station_for_target and key_for_target:
                set_local_storage(LS_AUTOCONNECT, "1", "save")
                set_stored_autoconnect_target(
                    {
                        "kind": "WU",
                        "station": station_for_target,
                        "api_key": key_for_target,
                        "z": z_for_target,
                    }
                )
                st.session_state["_wu_autoconnect_ui_target_kind"] = "WU"
                st.sidebar.success("Auto-conexión al iniciar activada (WU) ✅")
            else:
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
                st.session_state["_wu_autoconnect_ui_target_kind"] = ""
                st.sidebar.warning("Para activar auto-conexión WU, completa Station ID y API Key.")
        else:
            current_target = get_stored_autoconnect_target() or {}
            current_kind = str(current_target.get("kind", "")).strip().upper()
            if current_kind == "WU":
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
                st.session_state["_wu_autoconnect_ui_target_kind"] = ""
                st.sidebar.info("Auto-conexión al iniciar desactivada.")

        # Evita autoconectar en caliente en esta misma sesión.
        st.session_state["_autoconnect_attempted"] = True
        st.session_state["_wu_autoconnect_ui_last_value"] = bool(auto_connect_wu_device)

    cS, cF = st.sidebar.columns(2)
    with cS:
        save_clicked = st.button("Guardar", width="stretch")
    with cF:
        forget_clicked = st.button("Olvidar", width="stretch")

    if save_clicked:
        station_to_save = str(st.session_state.get("active_station", "")).strip()
        key_to_save = str(st.session_state.get("active_key", "")).strip()
        z_to_save = str(st.session_state.get("active_z", "")).strip()

        set_local_storage(LS_STATION, station_to_save, "save")
        set_local_storage(LS_APIKEY, key_to_save, "save")
        set_local_storage(LS_Z, z_to_save, "save")
        st.sidebar.success("Guardado en este dispositivo ✅")

    if forget_clicked:
        # Borrar de localStorage
        set_local_storage(LS_STATION, "", "forget")
        set_local_storage(LS_APIKEY, "", "forget")
        set_local_storage(LS_Z, "", "forget")
        set_local_storage(LS_AUTOCONNECT, "", "forget")
        set_stored_autoconnect_target(None)
        
        # Marcar para borrar en el próximo ciclo
        st.session_state["_clear_inputs"] = True
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        st.session_state["_autoconnect_attempted"] = False
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
        connect_clicked = st.button("Conectar", width="stretch")
    with colB:
        disconnect_clicked = st.button("Desconectar", width="stretch")

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
                or state_key.startswith('meteogalicia_')
                or state_key.startswith('nws_')
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
                    or state_key.startswith('meteogalicia_')
                    or state_key.startswith('nws_')
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
                except Exception:
                    st.sidebar.error("Altitud inválida. Usa un número (ej: 12.5)")
            else:  # Sin altitud manual, confiar en la API
                st.session_state["connected"] = True
                st.session_state["wu_connected_station"] = station
                st.session_state["wu_connected_api_key"] = api_key
                st.session_state["wu_connected_z"] = ""

            if (
                st.session_state.get("connected")
                and st.session_state.get("connection_type") == "WU"
                and bool(st.session_state.get("auto_connect_wu_device", False))
            ):
                set_local_storage(LS_STATION, station, "save")
                set_local_storage(LS_APIKEY, api_key, "save")
                set_local_storage(LS_Z, z_raw, "save")
                set_local_storage(LS_AUTOCONNECT, "1", "save")
                set_stored_autoconnect_target(
                    {
                        "kind": "WU",
                        "station": station,
                        "api_key": api_key,
                        "z": z_raw,
                    }
                )
                st.session_state["_autoconnect_attempted"] = True

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

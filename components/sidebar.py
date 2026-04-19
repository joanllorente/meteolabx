"""
Componentes de sidebar y funciones auxiliares
"""
import streamlit as st
import os
from datetime import datetime
from config import LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT, LS_WU_FORGOTTEN
from utils.i18n import (
    get_language_label,
    get_supported_languages,
    init_language,
    set_language,
    t,
)
from utils.storage import (
    set_local_storage,
    set_stored_autoconnect_target,
    forget_local_storage_keys,
    get_stored_unit_preferences,
    set_stored_unit_preferences,
)
from utils.helpers import normalize_text_input, is_nan
from utils.units import DEFAULT_UNIT_PREFERENCES, UNIT_LABELS, UNIT_OPTIONS, normalize_unit_preferences
from services.wu_calibration import (
    WU_CALIBRATION_ORDER,
    WU_CALIBRATION_SPECS,
    default_wu_calibration,
    normalize_wu_calibration,
)


def _now_local() -> datetime:
    """Hora actual en la timezone del navegador del usuario."""
    from zoneinfo import ZoneInfo
    # JS escribe la timezone del navegador en el query param _tz en cada carga de página
    if "browser_tz" not in st.session_state:
        tz_name = st.query_params.get("_tz", "")
        if tz_name:
            try:
                ZoneInfo(tz_name)  # validar que existe
                st.session_state["browser_tz"] = tz_name
            except Exception:
                pass
    tz_name = st.session_state.get("browser_tz", "")
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now().astimezone()


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


def render_sidebar(_local_storage_unused=None):
    """
    Renderiza la barra lateral con configuración

    Returns:
        Tupla (theme_mode, dark)
    """
    from utils.storage import (
        get_stored_station,
        get_stored_apikey,
        get_stored_z,
        get_stored_autoconnect,
        get_stored_autoconnect_target,
        get_local_storage_value,
        get_stored_wu_station_calibration,
        set_stored_wu_station_calibration,
    )

    # Fase 2 del Olvidar: los setItem del ciclo anterior ya llegaron al navegador.
    # Ahora limpiamos el estado de sesión y forzamos el rerun de limpieza de UI.
    if st.session_state.pop("_forget_pending", False):
        st.session_state["_skip_local_prefill_once"] = True
        st.session_state["_clear_inputs"] = True
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        st.session_state["_autoconnect_attempted"] = False
        for _k in ["wu_connected_station", "wu_connected_api_key", "wu_connected_z"]:
            st.session_state.pop(_k, None)
        st.session_state.pop("wu_cache_current", None)
        st.session_state.pop("wu_cache_daily", None)
        st.rerun()

    first_sidebar_load = not bool(st.session_state.get("_sidebar_inputs_initialized", False))

    # Prefill desde localStorage activado por defecto para persistir credenciales
    # entre recargas locales; puede desactivarse con MLX_ENABLE_LOCAL_PREFILL=0.
    allow_local_prefill = os.getenv("MLX_ENABLE_LOCAL_PREFILL", "1") == "1"

    skip_prefill_once = bool(st.session_state.pop("_skip_local_prefill_once", False))
    if allow_local_prefill and not skip_prefill_once:
        saved_station = get_stored_station()
        saved_key = get_stored_apikey()
        saved_z = get_stored_z()
        saved_autoconnect = bool(get_stored_autoconnect())
        saved_target = get_stored_autoconnect_target()
        is_wu_forgotten = str(get_local_storage_value(LS_WU_FORGOTTEN) or "").lower() in ("1", "true", "yes", "si", "on")
        if is_wu_forgotten:
            saved_station = None
            saved_key = None
            saved_z = None
            target_kind_raw = str((saved_target or {}).get("kind", "")).strip().upper()
            if target_kind_raw == "WU":
                saved_autoconnect = False
                saved_target = None

        active_station = st.session_state.get("active_station", "")
        active_key = st.session_state.get("active_key", "")
        active_z = st.session_state.get("active_z", "0")

        if first_sidebar_load:
            st.session_state["active_station"] = str(saved_station or "").strip()
            st.session_state["active_key"] = str(saved_key or "").strip()
            st.session_state["active_z"] = normalize_text_input(saved_z or "")
        else:
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

                # Prefijos de session_state por proveedor
                _provider_prefix = {
                    "AEMET": "aemet", "METEOCAT": "meteocat",
                    "EUSKALMET": "euskalmet", "FROST": "frost", "METEOFRANCE": "meteofrance",
                    "METEOGALICIA": "meteogalicia",
                    "NWS": "nws", "POEM": "poem",
                }
                prefix = _provider_prefix.get(provider_id)
                if prefix:
                    st.session_state[f"{prefix}_station_id"]   = station_id
                    st.session_state[f"{prefix}_station_name"] = station_name
                    st.session_state[f"{prefix}_station_lat"]  = lat
                    st.session_state[f"{prefix}_station_lon"]  = lon
                    st.session_state[f"{prefix}_station_alt"]  = elevation_m
                else:
                    st.session_state["auto_connect_wu_device"] = False
                    st.session_state["_autoconnect_attempted"] = True
                    st.rerun()

                st.session_state["connected"] = True
                st.session_state["_autoconnect_attempted"] = True
                st.rerun()
    elif first_sidebar_load:
        st.session_state["active_station"] = ""
        st.session_state["active_key"] = ""
        st.session_state["active_z"] = ""

    st.session_state["_sidebar_inputs_initialized"] = True

    st.session_state["active_z"] = normalize_text_input(st.session_state.get("active_z"))

    current_lang = init_language()
    saved_unit_preferences = get_stored_unit_preferences()
    for category, default_value in DEFAULT_UNIT_PREFERENCES.items():
        state_key = f"unit_pref_{category}"
        if state_key not in st.session_state:
            st.session_state[state_key] = saved_unit_preferences.get(category, default_value)

    # Si hay conexión WU activa, mantener credenciales en sesión aunque un rerun
    # temporalmente deje vacíos los widgets de entrada (ej. cambio de tema).
    if st.session_state.get("connected") and st.session_state.get("connection_type") == "WU":
        if not str(st.session_state.get("active_station", "")).strip():
            st.session_state["active_station"] = str(st.session_state.get("wu_connected_station", "")).strip()
        if not str(st.session_state.get("active_key", "")).strip():
            st.session_state["active_key"] = str(st.session_state.get("wu_connected_api_key", "")).strip()
        if not str(st.session_state.get("active_z", "")).strip():
            st.session_state["active_z"] = normalize_text_input(st.session_state.get("wu_connected_z", ""))

    # Idioma + tema
    st.sidebar.title(f"⚙️ {t('sidebar.settings_title')}")

    supported_languages = get_supported_languages()
    selector_lang = str(st.session_state.get("lang_selector", "")).strip().lower()
    if selector_lang in supported_languages and selector_lang != current_lang:
        current_lang = set_language(selector_lang)
    elif selector_lang not in supported_languages:
        st.session_state["lang_selector"] = current_lang

    selected_lang = st.sidebar.selectbox(
        t("sidebar.language.label"),
        supported_languages,
        format_func=get_language_label,
        key="lang_selector",
    )
    if selected_lang != current_lang:
        set_language(selected_lang)
        st.rerun()

    theme_options = ["auto", "light", "dark"]
    legacy_theme_aliases = {"Auto": "auto", "Claro": "light", "Oscuro": "dark"}
    current_theme = legacy_theme_aliases.get(
        str(st.session_state.get("theme_selector", "")).strip(),
        str(st.session_state.get("theme_selector", "")).strip(),
    )
    if current_theme not in theme_options:
        current_theme = theme_options[0]
    if st.session_state.get("theme_selector") != current_theme:
        st.session_state["theme_selector"] = current_theme

    theme_mode = st.sidebar.segmented_control(
        t("sidebar.theme.label"),
        theme_options,
        format_func=lambda option: t(f"sidebar.theme.options.{option}"),
        key="theme_selector",
        width="stretch",
    )
    if theme_mode is None:
        theme_mode = current_theme
    if theme_mode != current_theme:
        st.session_state["theme_selector"] = theme_mode
        st.session_state["_pending_active_tab"] = st.session_state.get("active_tab", "observation")
        st.rerun()

    # Conectar estación
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"### 🔌 {t('sidebar.connection.title')}")
    
    # Aplicar borrado si está marcado (ANTES de crear widgets)
    if st.session_state.get("_clear_inputs", False):
        st.session_state["active_station"] = ""
        st.session_state["active_key"] = ""
        st.session_state["active_z"] = ""
        st.session_state["auto_connect_wu_device"] = False
        st.session_state["_wu_autoconnect_ui_last_value"] = False
        st.session_state["_wu_autoconnect_ui_target_kind"] = ""
        del st.session_state["_clear_inputs"]

    st.sidebar.text_input(
        t("sidebar.connection.fields.station_id"),
        key="active_station",
        placeholder=t("sidebar.connection.placeholders.station_id"),
        autocomplete="off",
    )
    st.sidebar.text_input(
        t("sidebar.connection.fields.api_key"),
        key="active_key",
        type="password",
        placeholder=t("sidebar.connection.placeholders.api_key"),
        autocomplete="new-password",
    )
    st.sidebar.text_input(
        t("sidebar.connection.fields.altitude"),
        key="active_z",
        placeholder=t("sidebar.connection.placeholders.altitude"),
        autocomplete="off",
    )

    connection_caption = str(t("sidebar.connection.caption") or "").strip()
    auto_connect_default = bool(st.session_state.get("auto_connect_wu_device", False))
    auto_connect_wu_device = st.sidebar.toggle(
        t("sidebar.autoconnect.label"),
        value=auto_connect_default,
        key="auto_connect_wu_device",
    )
    autoconnect_caption = str(t("sidebar.autoconnect.caption") or "").strip()
    if autoconnect_caption and autoconnect_caption != "sidebar.autoconnect.caption":
        st.sidebar.caption(autoconnect_caption)

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
                set_local_storage(LS_WU_FORGOTTEN, "0", "save")
                set_stored_autoconnect_target(
                    {
                        "kind": "WU",
                        "station": station_for_target,
                        "api_key": key_for_target,
                        "z": z_for_target,
                    }
                )
                st.session_state["_wu_autoconnect_ui_target_kind"] = "WU"
                st.sidebar.success(t("sidebar.autoconnect.enabled"))
            else:
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
                st.session_state["_wu_autoconnect_ui_target_kind"] = ""
                st.sidebar.warning(t("sidebar.autoconnect.missing_credentials"))
        else:
            current_target = get_stored_autoconnect_target() or {}
            current_kind = str(current_target.get("kind", "")).strip().upper()
            if current_kind == "WU":
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
                st.session_state["_wu_autoconnect_ui_target_kind"] = ""
                st.sidebar.info(t("sidebar.autoconnect.disabled"))

        # Evita autoconectar en caliente en esta misma sesión.
        st.session_state["_autoconnect_attempted"] = True
        st.session_state["_wu_autoconnect_ui_last_value"] = bool(auto_connect_wu_device)

    cS, cF = st.sidebar.columns(2)
    with cS:
        save_clicked = st.button(t("sidebar.buttons.save"), width="stretch")
    with cF:
        forget_clicked = st.button(t("sidebar.buttons.forget"), width="stretch")

    if save_clicked:
        station_to_save = str(st.session_state.get("active_station", "")).strip()
        key_to_save = str(st.session_state.get("active_key", "")).strip()
        z_to_save = str(st.session_state.get("active_z", "")).strip()

        set_local_storage(LS_STATION, station_to_save, "save")
        set_local_storage(LS_APIKEY, key_to_save, "save")
        set_local_storage(LS_Z, z_to_save, "save")
        set_local_storage(LS_WU_FORGOTTEN, "0", "save")
        st.sidebar.success(t("sidebar.messages.saved_device"))

    if forget_clicked:
        # Fase 1: escribir los marcadores en localStorage y marcar pendiente.
        # NO llamamos st.rerun() aquí para que los widgets setItem se rendericen
        # en este ciclo y el JS del componente los procese antes del siguiente ciclo.
        forget_local_storage_keys()
        st.session_state["_forget_pending"] = True
        st.sidebar.success(t("sidebar.messages.data_erased"))


    # Estado conectado
    if "connected" not in st.session_state:
        st.session_state["connected"] = False

    def render_connection_banner(text: str, connected_state: bool):
        """Banner de estado con texto tintado (sin blanco puro)."""
        now_local = _now_local()
        auto_dark_local = (now_local.hour >= 20) or (now_local.hour <= 7)
        is_dark_ui = (
            theme_mode == "dark" or
            (theme_mode == "auto" and auto_dark_local)
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
        connect_clicked = st.button(t("sidebar.buttons.connect"), width="stretch")
    with colB:
        disconnect_clicked = st.button(t("sidebar.buttons.disconnect"), width="stretch")

    _PROVIDER_PREFIXES = (
        'aemet_', 'provider_station_', 'meteocat_', 'euskalmet_', 'frost_',
        'meteofrance_', 'meteogalicia_', 'nws_', 'poem_',
    )

    if disconnect_clicked:
        if st.session_state.get("connection_type") == "AEMET":
            try:
                from services.aemet import clear_aemet_runtime_cache
                clear_aemet_runtime_cache()
            except Exception:
                pass
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        st.session_state.pop("wu_sensor_presence", None)
        st.session_state.pop("wu_sensor_presence_station", None)
        st.session_state.pop("wu_station_calibration", None)
        st.session_state.pop("wu_station_calibration_station", None)
        for k in ("wu_connected_station", "wu_connected_api_key", "wu_connected_z"):
            st.session_state.pop(k, None)
        for k in [k for k in st.session_state if k.startswith(_PROVIDER_PREFIXES)]:
            del st.session_state[k]

    if connect_clicked:
        station = str(st.session_state.get("active_station", "")).strip()
        api_key = str(st.session_state.get("active_key", "")).strip()
        z_raw = str(st.session_state.get("active_z", "")).strip()

        if not station or not api_key:
            st.sidebar.error(t("sidebar.messages.missing_station_or_key"))
        else:
            # Conexión explícita de Weather Underground
            st.session_state["connection_type"] = "WU"
            # Limpiar restos de conexión por proveedor para evitar UI duplicada
            for k in [k for k in st.session_state if k.startswith(_PROVIDER_PREFIXES)]:
                del st.session_state[k]
            st.session_state.pop("wu_sensor_presence", None)
            st.session_state.pop("wu_sensor_presence_station", None)

            # Validar altitud si se proporcionó
            if z_raw:  # Si hay altitud manual
                try:
                    z_float = float(z_raw)
                    # Validar rango de altitud
                    from config import MIN_ALTITUDE_M, MAX_ALTITUDE_M
                    if not (MIN_ALTITUDE_M <= z_float <= MAX_ALTITUDE_M):
                        st.sidebar.error(
                            t(
                                "sidebar.messages.altitude_out_of_range",
                                min=MIN_ALTITUDE_M,
                                max=MAX_ALTITUDE_M,
                            )
                        )
                    else:
                        st.session_state["connected"] = True
                        st.session_state["wu_connected_station"] = station
                        st.session_state["wu_connected_api_key"] = api_key
                        st.session_state["wu_connected_z"] = z_raw
                except Exception:
                    st.sidebar.error(t("sidebar.messages.altitude_invalid"))
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
                set_local_storage(LS_WU_FORGOTTEN, "0", "save")
                set_stored_autoconnect_target(
                    {
                        "kind": "WU",
                        "station": station,
                        "api_key": api_key,
                        "z": z_raw,
                    }
                )
                st.session_state["_autoconnect_attempted"] = True

    if connection_caption and connection_caption != "sidebar.connection.caption":
        st.sidebar.caption(connection_caption)

    st.sidebar.markdown("---")

    is_connected_wu = bool(
        st.session_state.get("connected")
        and st.session_state.get("connection_type") == "WU"
    )
    wu_station_id = str(
        st.session_state.get("wu_connected_station", "")
        or st.session_state.get("active_station", "")
    ).strip().upper()
    sensor_presence_station = str(st.session_state.get("wu_sensor_presence_station", "")).strip().upper()
    sensor_presence = (
        st.session_state.get("wu_sensor_presence", {})
        if sensor_presence_station and sensor_presence_station == wu_station_id
        else {}
    )

    if is_connected_wu and wu_station_id:
        stored_calibration = normalize_wu_calibration(get_stored_wu_station_calibration(wu_station_id))
        current_calibration = dict(stored_calibration)
        st.session_state["wu_station_calibration"] = current_calibration
        st.session_state["wu_station_calibration_station"] = wu_station_id

        st.sidebar.markdown(f"### 🎛️ {t('sidebar.calibration.title')}")
        if isinstance(sensor_presence, dict) and any(bool(sensor_presence.get(sensor)) for sensor in WU_CALIBRATION_ORDER):
            for sensor in WU_CALIBRATION_ORDER:
                if not bool(sensor_presence.get(sensor)):
                    continue

                spec = WU_CALIBRATION_SPECS[sensor]
                decimals = int(spec.get("decimals", 1))
                step = 1.0 if decimals == 0 else 0.1
                fmt = "%.0f" if decimals == 0 else "%.1f"
                range_fmt = "{value:.0f}" if decimals == 0 else "{value:.1f}"
                widget_key = f"wu_calibration_{wu_station_id}_{sensor}"
                if widget_key not in st.session_state:
                    st.session_state[widget_key] = float(stored_calibration.get(sensor, 0.0))

                value = st.sidebar.number_input(
                    t(f"sidebar.calibration.fields.{sensor}"),
                    min_value=float(spec["min"]),
                    max_value=float(spec["max"]),
                    step=step,
                    format=fmt,
                    key=widget_key,
                    help=t(
                        "sidebar.calibration.range_help",
                        min=range_fmt.format(value=float(spec["min"])),
                        max=range_fmt.format(value=float(spec["max"])),
                        unit=str(spec["unit"]),
                    ),
                )
                current_calibration[sensor] = round(float(value), decimals)

            current_calibration = normalize_wu_calibration(current_calibration)
            st.session_state["wu_station_calibration"] = current_calibration
            st.session_state["wu_station_calibration_station"] = wu_station_id

            if current_calibration != stored_calibration:
                set_stored_wu_station_calibration(wu_station_id, current_calibration)
                st.session_state["_wu_calibration_changed"] = True
        else:
            st.sidebar.caption(t("sidebar.calibration.loading"))

        st.sidebar.markdown("---")

    st.sidebar.markdown(f"### 📏 {t('sidebar.units.title')}")
    selected_unit_preferences = {}
    for category, options in UNIT_OPTIONS.items():
        widget_key = f"unit_pref_{category}"
        current_value = str(
            st.session_state.get(
                widget_key,
                saved_unit_preferences.get(category, str(options[0])),
            )
        )
        if current_value not in options:
            current_value = str(options[0])
        value = st.sidebar.segmented_control(
            t(f"sidebar.units.fields.{category}"),
            options,
            format_func=lambda option, category=category: UNIT_LABELS[category][str(option)],
            key=widget_key,
            width="stretch",
        )
        if value is None:
            value = current_value
        selected_unit_preferences[category] = str(value)

    selected_unit_preferences = normalize_unit_preferences(selected_unit_preferences)
    st.session_state["unit_preferences"] = selected_unit_preferences
    if selected_unit_preferences != normalize_unit_preferences(saved_unit_preferences):
        set_stored_unit_preferences(selected_unit_preferences)

    st.sidebar.markdown("---")
    
    # ============================================================
    # MODO DEMO RADIACIÓN (SOLO DESARROLLO/INTERNO)
    # ============================================================
    # Solo visible si se ejecuta con: DEMO_MODE=1 streamlit run meteolabx.py
    
    demo_radiation = False
    demo_solar = None
    demo_uv = None
    
    if os.getenv("DEMO_MODE") == "1" or os.getenv("METEOLABX_DEMO") == "1":
        st.sidebar.markdown("---")
        st.sidebar.markdown(f"### 🔬 {t('sidebar.demo.title')}")
        
        demo_radiation = st.sidebar.toggle(
            t("sidebar.demo.enable"),
            value=False,
            help=t("sidebar.demo.enable_help"),
        )
        
        if demo_radiation:
            st.sidebar.caption(t("sidebar.demo.intro"))
            demo_solar = st.sidebar.slider(
                t("sidebar.demo.solar_label"),
                min_value=0,
                max_value=1200,
                value=650,
                step=50,
                help=t("sidebar.demo.solar_help"),
            )
            demo_uv = st.sidebar.slider(
                t("sidebar.demo.uv_label"),
                min_value=0.0,
                max_value=15.0,
                value=6.0,
                step=0.5,
                help=t("sidebar.demo.uv_help"),
            )
            st.sidebar.caption(t("sidebar.demo.quick_ref"))
            st.sidebar.caption(t("sidebar.demo.cloudy"))
            st.sidebar.caption(t("sidebar.demo.partial"))
            st.sidebar.caption(t("sidebar.demo.clear"))
    
    # Guardar en session_state para acceso desde main
    st.session_state["demo_radiation"] = demo_radiation
    st.session_state["demo_solar"] = demo_solar
    st.session_state["demo_uv"] = demo_uv

    # Determinar tema
    now = _now_local()
    auto_dark = (now.hour >= 20) or (now.hour <= 7)
    
    if theme_mode == "auto":
        dark = auto_dark
    elif theme_mode == "dark":
        dark = True
    else:
        dark = False

    return theme_mode, dark

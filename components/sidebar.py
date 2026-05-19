"""
Componentes de sidebar y funciones auxiliares
"""
import streamlit as st
import os
from datetime import datetime
from config import (
    LS_STATION,
    LS_APIKEY,
    LS_Z,
    LS_AUTOCONNECT,
    LS_AUTOCONNECT_TARGET,
    LS_WU_FORGOTTEN,
    LS_WU_CALIBRATIONS,
    LS_UNIT_PREFERENCES,
)
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
    consume_local_storage_writes,
    flush_local_storage_writes,
    get_stored_unit_preferences,
    hydrate_local_storage_snapshot,
    local_storage_snapshot_ready,
    set_stored_unit_preferences,
)
from utils.helpers import normalize_text_input, is_nan, coerce_str
from utils.units import DEFAULT_UNIT_PREFERENCES, UNIT_LABELS, UNIT_OPTIONS, normalize_unit_preferences
from services.wu_calibration import (
    WU_CALIBRATION_ORDER,
    WU_CALIBRATION_SPECS,
    default_wu_calibration,
    normalize_wu_calibration,
)
from utils.provider_state import (
    apply_station_selection,
    apply_wu_station_state,
    clear_provider_autoconnect_widget_state,
    disconnect_active_station,
)
from utils.state_keys import AUTOCONNECT_ATTEMPTED, CONNECTION_LOADING
from local_storage_bridge import sync_local_storage


LOCAL_STORAGE_BOOTSTRAP_KEYS = [
    LS_STATION,
    LS_APIKEY,
    LS_Z,
    LS_AUTOCONNECT,
    LS_AUTOCONNECT_TARGET,
    LS_WU_FORGOTTEN,
    LS_WU_CALIBRATIONS,
    LS_UNIT_PREFERENCES,
]

WU_STATION_INPUT_KEY = "wu_input_station"
WU_API_KEY_INPUT_KEY = "wu_input_api_key"
WU_ALTITUDE_INPUT_KEY = "wu_input_altitude"
WU_INPUT_KEYS = (
    WU_STATION_INPUT_KEY,
    WU_API_KEY_INPUT_KEY,
    WU_ALTITUDE_INPUT_KEY,
)


def _sync_wu_input_widgets_from_active(
    *,
    overwrite: bool = False,
    overwrite_if_pristine: bool = False,
) -> None:
    """Mantiene los widgets WU separados del estado canónico active_*."""
    user_edited = bool(st.session_state.get("_wu_inputs_user_edited", False))
    values = {
        WU_STATION_INPUT_KEY: coerce_str(st.session_state.get("active_station", "")),
        WU_API_KEY_INPUT_KEY: coerce_str(st.session_state.get("active_key", "")),
        WU_ALTITUDE_INPUT_KEY: normalize_text_input(st.session_state.get("active_z", "")),
    }
    for key, value in values.items():
        if (
            overwrite
            or (overwrite_if_pristine and not user_edited)
            or not str(st.session_state.get(key, "")).strip()
        ):
            st.session_state[key] = value
    if overwrite or (overwrite_if_pristine and not user_edited):
        st.session_state["_wu_inputs_user_edited"] = False


def _sync_active_wu_from_input_widgets() -> None:
    """Copia el formulario WU al estado canónico usado por el resto de la app."""
    st.session_state["active_station"] = coerce_str(
        st.session_state.get(WU_STATION_INPUT_KEY, "")
    )
    st.session_state["active_key"] = coerce_str(
        st.session_state.get(WU_API_KEY_INPUT_KEY, "")
    )
    st.session_state["active_z"] = normalize_text_input(
        st.session_state.get(WU_ALTITUDE_INPUT_KEY, "")
    )


def _mark_wu_inputs_user_edited() -> None:
    st.session_state["_wu_inputs_user_edited"] = True


def _now_local() -> datetime:
    """Hora actual en la timezone del navegador del usuario."""
    from zoneinfo import ZoneInfo
    # La timezone llega por el componente browser_context; _tz queda como
    # fallback legacy para sesiones antiguas que aún carguen esa URL.
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


def _browser_prefers_dark() -> bool:
    """Preferencia de tema del navegador/dispositivo; si no existe, fallback por hora."""
    scheme = str(
        st.session_state.get("browser_color_scheme")
        or st.query_params.get("_cs", "")
    ).strip().lower()
    if scheme in ("dark", "light"):
        st.session_state["browser_color_scheme"] = scheme
        return scheme == "dark"

    now = _now_local()
    return (now.hour >= 20) or (now.hour <= 7)


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

    bootstrap_writes = consume_local_storage_writes()
    storage_payload = sync_local_storage(
        keys=LOCAL_STORAGE_BOOTSTRAP_KEYS,
        writes=bootstrap_writes,
        key="mlx_local_storage_bootstrap",
    )
    if isinstance(storage_payload, dict) and storage_payload.get("ready"):
        hydrate_local_storage_snapshot(storage_payload.get("values"))
    storage_ready = local_storage_snapshot_ready()
    for _bad_input_key in ("active_station", "active_key", "active_z", *WU_INPUT_KEYS):
        if str(st.session_state.get(_bad_input_key, "")).strip() == "[object Object]":
            st.session_state[_bad_input_key] = ""

    # Fase 2 del Olvidar: los setItem del ciclo anterior ya llegaron al navegador.
    # Ahora limpiamos el estado de sesión y forzamos el rerun de limpieza de UI.
    if st.session_state.pop("_forget_pending", False):
        st.session_state["_skip_local_prefill_once"] = True
        st.session_state["_clear_inputs"] = True
        st.session_state["connected"] = False
        st.session_state["connection_type"] = None
        st.session_state[AUTOCONNECT_ATTEMPTED] = False
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
    defer_local_prefill = bool(allow_local_prefill and not skip_prefill_once and not storage_ready)
    provider_takeover_guard_this_run = False
    if allow_local_prefill and not skip_prefill_once and storage_ready:
        saved_station = get_stored_station()
        saved_key = get_stored_apikey()
        saved_z = get_stored_z()
        saved_autoconnect_raw = coerce_str(get_local_storage_value(LS_AUTOCONNECT), lower=True)
        saved_autoconnect = bool(get_stored_autoconnect())
        saved_target = get_stored_autoconnect_target()
        session_autoconnect_enabled = st.session_state.get("_mlx_session_autoconnect_enabled", None)
        session_autoconnect_target = st.session_state.get("_mlx_session_autoconnect_target")
        if isinstance(session_autoconnect_target, dict) and session_autoconnect_enabled is not False:
            # Si el usuario acaba de cambiar de proveedor a WU (o viceversa),
            # el snapshot del navegador puede tardar un rerun en reflejarlo.
            # La intención más reciente de esta sesión manda sobre localStorage
            # para que el toggle visible no "rebote" apagado.
            saved_target = session_autoconnect_target
            saved_autoconnect = True
            saved_autoconnect_raw = "1"
        elif session_autoconnect_enabled is False:
            saved_target = None
            saved_autoconnect = False
            saved_autoconnect_raw = "0"
        is_wu_forgotten = str(get_local_storage_value(LS_WU_FORGOTTEN) or "").lower() in ("1", "true", "yes", "si", "on")
        if is_wu_forgotten:
            saved_station = None
            saved_key = None
            saved_z = None
            target_kind_raw = str((saved_target or {}).get("kind", "")).strip().upper()
            if target_kind_raw == "WU":
                saved_autoconnect = False
                saved_target = None
        else:
            target_kind_raw = str((saved_target or {}).get("kind", "")).strip().upper()
            if target_kind_raw == "WU":
                # El objetivo de autoconexión guarda una copia completa de WU.
                # Si alguna escritura auxiliar llega tarde, úsala para hidratar
                # los campos al abrir una sesión nueva en producción.
                if not coerce_str(saved_station):
                    saved_station = coerce_str((saved_target or {}).get("station", "")) or None
                if not coerce_str(saved_key):
                    saved_key = coerce_str((saved_target or {}).get("api_key", "")) or None
                if not coerce_str(saved_z):
                    saved_z = coerce_str((saved_target or {}).get("z", "")) or None

        active_station = st.session_state.get("active_station", "")
        active_key = st.session_state.get("active_key", "")
        active_z = st.session_state.get("active_z", "0")
        session_has_wu_credentials = bool(coerce_str(active_station) and coerce_str(active_key))
        session_wu_autoconnect_enabled = bool(
            st.session_state.get("auto_connect_wu_device", False)
            and session_has_wu_credentials
        )

        if first_sidebar_load:
            st.session_state["active_station"] = coerce_str(saved_station)
            st.session_state["active_key"] = coerce_str(saved_key)
            st.session_state["active_z"] = normalize_text_input(saved_z or "")
            _sync_wu_input_widgets_from_active(overwrite=True)
        else:
            if not active_station and saved_station:
                st.session_state["active_station"] = saved_station
            if not active_key and saved_key:
                st.session_state["active_key"] = saved_key
            if (not str(active_z).strip() or active_z == "0") and saved_z:
                st.session_state["active_z"] = normalize_text_input(saved_z)
            _sync_wu_input_widgets_from_active(overwrite_if_pristine=True)

        has_saved_credentials = bool(coerce_str(saved_station) and coerce_str(saved_key))
        target_kind = str((saved_target or {}).get("kind", "")).strip().upper()
        valid_wu_target = bool(target_kind == "WU" and has_saved_credentials)
        valid_provider_target = bool(
            target_kind == "PROVIDER"
            and str((saved_target or {}).get("provider_id", "")).strip()
            and str((saved_target or {}).get("station_id", "")).strip()
        )
        has_valid_target = valid_wu_target or valid_provider_target
        if has_valid_target and saved_autoconnect_raw not in ("0", "false", "no", "off"):
            saved_autoconnect = True
        if not has_valid_target:
            saved_autoconnect = False
        if has_valid_target and saved_autoconnect:
            st.session_state["_mlx_session_autoconnect_target"] = dict(saved_target or {})
            st.session_state["_mlx_session_autoconnect_enabled"] = True
            if valid_wu_target:
                clear_provider_autoconnect_widget_state()

        # Estado UI del toggle de sidebar: solo representa auto-conexión WU.
        wu_toggle_default = bool(saved_autoconnect and valid_wu_target)
        current_target_kind = target_kind if has_valid_target else ""
        wu_target_token = ""
        if wu_toggle_default:
            wu_target_token = "|".join(
                (
                    "WU",
                    coerce_str(saved_station),
                    normalize_text_input(saved_z or ""),
                )
            )
        if session_wu_autoconnect_enabled and not has_valid_target:
            # En producción, la escritura a localStorage puede llegar un ciclo
            # más tarde que el rerun disparado por "Guardar". No apagues el
            # toggle si las credenciales WU siguen presentes en session_state.
            wu_toggle_default = True
            current_target_kind = "WU"
        wu_toggle_changed_this_run = bool(st.session_state.get("_wu_autoconnect_toggle_changed", False))
        wu_toggle_event_armed = bool(st.session_state.get("_wu_autoconnect_event_armed", False))
        if (
            wu_toggle_default
            and wu_target_token
            and st.session_state.get("_wu_autoconnect_hydrated_token") != wu_target_token
        ):
            # Primera hidratación visual de un target WU guardado. Safari /
            # Streamlit pueden entregar un valor frontend viejo (False) antes
            # de que el toggle se pinte con el target real. Ese primer pulso
            # no es una acción del usuario, así que sembramos el valor correcto
            # antes de instanciar el widget.
            st.session_state.pop("_wu_autoconnect_toggle_changed", None)
            st.session_state["auto_connect_wu_device"] = True
            st.session_state["_wu_autoconnect_ui_target_kind"] = "WU"
            st.session_state["_wu_autoconnect_ui_last_value"] = True
            st.session_state["_wu_autoconnect_disable_armed"] = False
            st.session_state["_wu_autoconnect_hydrated_token"] = wu_target_token
            wu_toggle_changed_this_run = False
        provider_takeover_pending = bool(
            st.session_state.pop("_provider_autoconnect_takeover_pending", False)
        )
        provider_takeover_grace = min(
            1,
            int(st.session_state.get("_provider_autoconnect_takeover_grace", 0) or 0),
        )
        provider_takeover_guard = bool(provider_takeover_pending or provider_takeover_grace > 0)
        provider_takeover_guard_this_run = bool(
            provider_takeover_guard and current_target_kind == "PROVIDER"
        )
        if provider_takeover_grace > 0:
            st.session_state["_provider_autoconnect_takeover_grace"] = provider_takeover_grace - 1
        if provider_takeover_guard_this_run:
            # Acabamos de guardar un proveedor como target. En el rerun
            # inmediato puede llegar un callback viejo del toggle WU en True;
            # si lo aceptamos, sobrescribe otra vez el target con WU. La
            # ventana dura unos pocos reruns porque Safari/Streamlit pueden
            # entregar el callback stale un ciclo tarde.
            st.session_state.pop("_wu_autoconnect_toggle_changed", None)
            st.session_state["auto_connect_wu_device"] = False
            st.session_state["_wu_autoconnect_ui_target_kind"] = "PROVIDER"
            st.session_state["_wu_autoconnect_ui_last_value"] = False
            st.session_state["_wu_autoconnect_disable_armed"] = False
            wu_toggle_changed_this_run = False

        # Ventana de gracia tras un autoconnect: durante los próximos N reruns
        # tratamos cualquier callback "desactivador" del toggle como fantasma
        # (Streamlit puede disparar on_change al rehidratar el frontend con
        # un valor stale del DOM tras un rerun, lo cual NO es un click real).
        # Sin esta ventana, el callback del 3.er rerun pasaba la defensa de
        # `_wu_autoconnect_event_armed` (que ya estaba True) y entraba al
        # bloque que borra LS_AUTOCONNECT_TARGET de localStorage, dejando la
        # próxima sesión sin posibilidad de autoconectar.
        _grace_remaining = int(st.session_state.get("_wu_autoconnect_post_grace", 0) or 0)
        if _grace_remaining > 0:
            st.session_state["_wu_autoconnect_post_grace"] = _grace_remaining - 1
            incoming_toggle_value = bool(st.session_state.get("auto_connect_wu_device", False))
            if wu_toggle_changed_this_run and wu_toggle_default and not incoming_toggle_value:
                # Callback fantasma intentando desactivar algo que en
                # localStorage sigue activo: anular y restaurar el toggle.
                st.session_state.pop("_wu_autoconnect_toggle_changed", None)
                st.session_state["auto_connect_wu_device"] = True
                wu_toggle_changed_this_run = False

        if wu_toggle_changed_this_run and not wu_toggle_event_armed and wu_toggle_default:
            # Streamlit puede hidratar el toggle con un valor frontend viejo
            # justo al abrir la web. Ese callback no es un click real del
            # usuario; si lo aceptamos, borra la autoconexión guardada antes
            # incluso de que aparezcan las credenciales en los inputs.
            st.session_state.pop("_wu_autoconnect_toggle_changed", None)
            wu_toggle_changed_this_run = False
        if (
            wu_toggle_changed_this_run
            and current_target_kind == "PROVIDER"
            and bool(st.session_state.get("auto_connect_wu_device", False))
            and st.session_state.get("_wu_autoconnect_ui_target_kind") != "PROVIDER"
        ):
            # Al cambiar de WU a proveedor, el frontend puede rehidratar el
            # toggle WU con el valor anterior (True) y disparar on_change. Si
            # el último estado visual todavía era WU, este callback es stale:
            # lo anulamos antes de instanciar el widget para no tocar una key
            # de Streamlit después de renderizarla.
            st.session_state.pop("_wu_autoconnect_toggle_changed", None)
            st.session_state["auto_connect_wu_device"] = False
            st.session_state["_wu_autoconnect_ui_target_kind"] = "PROVIDER"
            st.session_state["_wu_autoconnect_ui_last_value"] = False
            st.session_state["_wu_autoconnect_disable_armed"] = False
            wu_toggle_changed_this_run = False
        if not wu_toggle_changed_this_run:
            current_wu_toggle_value = bool(st.session_state.get("auto_connect_wu_device", False))
            if (
                st.session_state.get("_wu_autoconnect_ui_target_kind") != current_target_kind
                or current_wu_toggle_value != wu_toggle_default
            ):
                st.session_state["auto_connect_wu_device"] = wu_toggle_default
                st.session_state["_wu_autoconnect_ui_target_kind"] = current_target_kind
                st.session_state["_wu_autoconnect_ui_last_value"] = wu_toggle_default
            elif "auto_connect_wu_device" not in st.session_state:
                st.session_state["auto_connect_wu_device"] = wu_toggle_default
                st.session_state["_wu_autoconnect_ui_last_value"] = wu_toggle_default

        # Autoconexion al abrir si hay target guardado y el toggle estaba activo.
        should_autoconnect_wu = (
            saved_autoconnect
            and valid_wu_target
            and not st.session_state.get("connected", False)
            and not st.session_state.get(AUTOCONNECT_ATTEMPTED, False)
        )
        should_autoconnect_provider = (
            saved_autoconnect
            and valid_provider_target
            and not st.session_state.get("connected", False)
            and not st.session_state.get(AUTOCONNECT_ATTEMPTED, False)
        )
        if should_autoconnect_wu or should_autoconnect_provider:
            if should_autoconnect_wu:
                station_clean = str(saved_station).strip()
                key_clean = str(saved_key).strip()
                z_clean = normalize_text_input(saved_z or "")

                # Sembrar los widgets antes de que se instancien para evitar
                # warnings de Streamlit y garantizar que el usuario vea sus
                # credenciales en el sidebar mientras se autoconecta.
                st.session_state["active_station"] = station_clean
                st.session_state["active_key"] = key_clean
                st.session_state["active_z"] = z_clean
                _sync_wu_input_widgets_from_active(overwrite=True)

                # Usar el flujo unificado de conexión WU: setea el overlay de
                # carga, limpia caches de proveedor previos y deja la sesión
                # lista para que el ciclo principal haga el fetch real.
                apply_wu_station_state(
                    station_clean,
                    key_clean,
                    z_clean,
                    connected=True,
                )

                st.session_state[AUTOCONNECT_ATTEMPTED] = True
                # Ventana de gracia tras el autoconnect: durante los próximos
                # reruns ignoramos callbacks "desactivadores" del toggle, que
                # pueden venir de Streamlit rehidratando el frontend con un
                # valor stale (no son clicks reales del usuario). Si no
                # tuviéramos esta ventana, el callback fantasma del 3.er+
                # rerun borraría localStorage y la próxima sesión ya no
                # autoconectaría.
                st.session_state["_wu_autoconnect_post_grace"] = 5
                selection_ok = True
                st.rerun()
            elif should_autoconnect_provider:
                provider_id = str(saved_target.get("provider_id", "")).strip().upper()
                station_id = str(saved_target.get("station_id", "")).strip()
                station_name = str(saved_target.get("station_name", "")).strip() or station_id
                lat = saved_target.get("lat")
                lon = saved_target.get("lon")
                elevation_m = saved_target.get("elevation_m")

                # Marcar el intento ANTES de rerun para evitar bucles de
                # auto-conexión si la conexión falla más adelante en el ciclo
                # principal (donde se restaura el estado y `connected` vuelve
                # a False).
                st.session_state[AUTOCONNECT_ATTEMPTED] = True

                selection_ok = apply_station_selection(
                    {
                        "provider_id": provider_id,
                        "station_id": station_id,
                        "name": station_name,
                        "lat": lat,
                        "lon": lon,
                        "elevation_m": elevation_m,
                        "station_tz": saved_target.get("station_tz", ""),
                    },
                    connected=True,
                )
            if not selection_ok:
                # Target inválido: desactivamos auto-conexión para no
                # reintentar el mismo error en cada arranque.
                st.session_state["auto_connect_wu_device"] = False
            st.rerun()
    elif first_sidebar_load and not defer_local_prefill:
        st.session_state["active_station"] = ""
        st.session_state["active_key"] = ""
        st.session_state["active_z"] = ""
        _sync_wu_input_widgets_from_active(overwrite=True)

    if not defer_local_prefill:
        st.session_state["_sidebar_inputs_initialized"] = True

    # Solo reescribir si la normalización cambia el valor, así evitamos una
    # escritura innecesaria en cada rerun y prevenimos warnings de Streamlit
    # cuando el widget ya esté instanciado en futuras reorganizaciones.
    _current_active_z = st.session_state.get("active_z")
    _normalized_active_z = normalize_text_input(_current_active_z)
    if _current_active_z != _normalized_active_z:
        st.session_state["active_z"] = _normalized_active_z
        _sync_wu_input_widgets_from_active(overwrite_if_pristine=True)

    current_lang = init_language()
    saved_unit_preferences = get_stored_unit_preferences()
    for category, default_value in DEFAULT_UNIT_PREFERENCES.items():
        state_key = f"unit_pref_{category}"
        saved_value = str(saved_unit_preferences.get(category, default_value)).strip().lower()
        if saved_value not in UNIT_OPTIONS[category]:
            saved_value = default_value
        if state_key not in st.session_state:
            st.session_state[state_key] = saved_value
        else:
            current_state_value = str(st.session_state.get(state_key, "")).strip().lower()
            if current_state_value not in UNIT_OPTIONS[category]:
                st.session_state[state_key] = saved_value

    # Si hay conexión WU activa o recién aplicada, mantener credenciales en
    # sesión aunque un rerun temporalmente deje vacíos los widgets de entrada.
    # Esto cubre también el ciclo justo después de una autoconexión, donde las
    # claves wu_connected_* ya existen aunque el resto del estado aún se esté
    # asentando.
    current_connection_type = str(st.session_state.get("connection_type", "")).strip().upper()
    loading_payload = st.session_state.get(CONNECTION_LOADING)
    loading_provider = (
        str(loading_payload.get("provider", "")).strip().upper()
        if isinstance(loading_payload, dict)
        else ""
    )
    has_wu_runtime_credentials = bool(
        str(st.session_state.get("wu_connected_station", "")).strip()
        and str(st.session_state.get("wu_connected_api_key", "")).strip()
    )
    if (
        current_connection_type == "WU"
        or (loading_provider == "WU" and has_wu_runtime_credentials)
    ):
        if not str(st.session_state.get("active_station", "")).strip():
            st.session_state["active_station"] = str(st.session_state.get("wu_connected_station", "")).strip()
        if not str(st.session_state.get("active_key", "")).strip():
            st.session_state["active_key"] = str(st.session_state.get("wu_connected_api_key", "")).strip()
        if not str(st.session_state.get("active_z", "")).strip():
            st.session_state["active_z"] = normalize_text_input(st.session_state.get("wu_connected_z", ""))
        _sync_wu_input_widgets_from_active(overwrite_if_pristine=True)

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

    def _handle_theme_selector_change() -> None:
        # Al cambiar el tema, Streamlit dispara un rerun pero los segmented_control
        # pueden perder el ``active_tab`` actual durante el ciclo. Memorizamos la
        # pestaña aquí para que :func:`_sync_active_tab_state` la restaure en el
        # siguiente render.
        st.session_state["_pending_active_tab"] = st.session_state.get("active_tab", "observation")
        st.session_state["_theme_force_refresh"] = True

    theme_options = ["auto", "light", "dark"]
    legacy_theme_aliases = {"Auto": "auto", "Claro": "light", "Oscuro": "dark"}
    raw_theme = coerce_str(st.session_state.get("theme_selector", ""))
    current_theme = legacy_theme_aliases.get(raw_theme, raw_theme)
    if current_theme not in theme_options:
        current_theme = theme_options[0]
    if st.session_state.get("theme_selector") != current_theme:
        st.session_state["theme_selector"] = current_theme

    theme_mode = st.sidebar.segmented_control(
        t("sidebar.theme.label"),
        theme_options,
        format_func=lambda option: t(f"sidebar.theme.options.{option}"),
        key="theme_selector",
        on_change=_handle_theme_selector_change,
        width="stretch",
    )
    if theme_mode is None:
        theme_mode = current_theme
    if st.session_state.pop("_theme_force_refresh", False):
        st.rerun()

    # Conectar estación
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"### 🔌 {t('sidebar.connection.title')}")

    if defer_local_prefill:
        defer_count = int(st.session_state.get("_local_prefill_defer_count", 0) or 0) + 1
        st.session_state["_local_prefill_defer_count"] = defer_count
        if defer_count <= 6:
            st.sidebar.caption(t("sidebar.connection.loading_saved"))
            auto_dark = _browser_prefers_dark()
            if theme_mode == "auto":
                dark = auto_dark
            elif theme_mode == "dark":
                dark = True
            else:
                dark = False
            flush_local_storage_writes("mlx_local_storage_sidebar_flush")
            return theme_mode, dark

        defer_local_prefill = False
        st.session_state["_sidebar_inputs_initialized"] = True
        st.session_state.setdefault("active_station", "")
        st.session_state.setdefault("active_key", "")
        st.session_state.setdefault("active_z", "")
        _sync_wu_input_widgets_from_active(overwrite_if_pristine=True)
    else:
        st.session_state.pop("_local_prefill_defer_count", None)
    
    # Aplicar borrado si está marcado (ANTES de crear widgets)
    if st.session_state.get("_clear_inputs", False):
        st.session_state["active_station"] = ""
        st.session_state["active_key"] = ""
        st.session_state["active_z"] = ""
        for _k in WU_INPUT_KEYS:
            st.session_state[_k] = ""
        st.session_state["_wu_inputs_user_edited"] = False
        st.session_state["auto_connect_wu_device"] = False
        st.session_state["_wu_autoconnect_ui_last_value"] = False
        st.session_state["_wu_autoconnect_ui_target_kind"] = ""
        del st.session_state["_clear_inputs"]

    st.sidebar.text_input(
        t("sidebar.connection.fields.station_id"),
        key=WU_STATION_INPUT_KEY,
        placeholder=t("sidebar.connection.placeholders.station_id"),
        autocomplete="off",
        on_change=_mark_wu_inputs_user_edited,
    )
    st.sidebar.text_input(
        t("sidebar.connection.fields.api_key"),
        key=WU_API_KEY_INPUT_KEY,
        type="password",
        placeholder=t("sidebar.connection.placeholders.api_key"),
        help=t("sidebar.connection.api_key_help"),
        autocomplete="new-password",
        on_change=_mark_wu_inputs_user_edited,
    )
    st.sidebar.text_input(
        t("sidebar.connection.fields.altitude"),
        key=WU_ALTITUDE_INPUT_KEY,
        placeholder=t("sidebar.connection.placeholders.altitude"),
        autocomplete="off",
        on_change=_mark_wu_inputs_user_edited,
    )
    _sync_active_wu_from_input_widgets()

    connection_caption = coerce_str(t("sidebar.connection.caption"))
    def _mark_wu_autoconnect_toggle_changed() -> None:
        st.session_state["_wu_autoconnect_toggle_changed"] = True

    auto_connect_default = bool(st.session_state.get("auto_connect_wu_device", False))
    auto_connect_wu_device = st.sidebar.toggle(
        t("sidebar.autoconnect.label"),
        key="auto_connect_wu_device",
        on_change=_mark_wu_autoconnect_toggle_changed,
    )
    autoconnect_caption = coerce_str(t("sidebar.autoconnect.caption"))
    if autoconnect_caption and autoconnect_caption != "sidebar.autoconnect.caption":
        st.sidebar.caption(autoconnect_caption)

    if "_wu_autoconnect_ui_last_value" not in st.session_state:
        st.session_state["_wu_autoconnect_ui_last_value"] = auto_connect_default
    wu_toggle_changed = bool(st.session_state.pop("_wu_autoconnect_toggle_changed", False))
    if wu_toggle_changed:
        station_for_target = str(st.session_state.get("active_station", "")).strip()
        key_for_target = str(st.session_state.get("active_key", "")).strip()
        z_for_target = str(st.session_state.get("active_z", "")).strip()

        if auto_connect_wu_device:
            current_target = get_stored_autoconnect_target() or {}
            current_kind = str(current_target.get("kind", "")).strip().upper()
            provider_takeover_guard_late = bool(
                provider_takeover_guard_this_run
                or st.session_state.get("_provider_autoconnect_takeover_grace", 0)
            )

            # Defensa contra callback fantasma del toggle WU: si en
            # localStorage ya hay un target de PROVIDER activo (el usuario
            # acaba de elegir un proveedor desde el mapa o el station
            # selector), NO sobrescribir con WU. El callback que llega aquí
            # es Streamlit rehidratando el toggle WU con un valor stale.
            # Sin esta guardia, el target de proveedor recién guardado se
            # perdía en el siguiente rerun.
            if (
                current_kind == "PROVIDER"
                and (
                    provider_takeover_guard_late
                    or st.session_state.get("_wu_autoconnect_ui_target_kind") != "PROVIDER"
                )
            ):
                # Marcamos el estado coherente con PROVIDER y saltamos el
                # bloque que sobrescribe localStorage. NO usamos `st.rerun()`
                # aquí porque cortaría el sidebar a medio renderizar.
                st.session_state["auto_connect_wu_device"] = False
                st.session_state["_wu_autoconnect_ui_target_kind"] = "PROVIDER"
                st.session_state["_wu_autoconnect_ui_last_value"] = False
                st.session_state["_wu_autoconnect_disable_armed"] = False
            else:
                if not station_for_target:
                    station_for_target = str(
                        st.session_state.get("wu_connected_station")
                        or (current_target.get("station", "") if current_kind == "WU" else "")
                        or get_stored_station()
                        or ""
                    ).strip()
                if not key_for_target:
                    key_for_target = str(
                        st.session_state.get("wu_connected_api_key")
                        or (current_target.get("api_key", "") if current_kind == "WU" else "")
                        or get_stored_apikey()
                        or ""
                    ).strip()
                if not z_for_target:
                    z_for_target = str(
                        st.session_state.get("wu_connected_z")
                        or (current_target.get("z", "") if current_kind == "WU" else "")
                        or get_stored_z()
                        or ""
                    ).strip()

                if station_for_target and key_for_target:
                    # Persistir también las credenciales individuales para que la
                    # próxima carga las recupere aunque el usuario no haya pulsado
                    # "Guardar". Esto evita que quede una API key vieja en
                    # localStorage cuando solo se ha cambiado el input pero no se
                    # ha guardado explícitamente.
                    set_local_storage(LS_STATION, station_for_target, "save")
                    set_local_storage(LS_APIKEY, key_for_target, "save")
                    set_local_storage(LS_Z, z_for_target, "save")
                    set_local_storage(LS_AUTOCONNECT, "1", "save")
                    set_local_storage(LS_WU_FORGOTTEN, "0", "save")
                    clear_provider_autoconnect_widget_state()
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
            has_runtime_wu_credentials = bool(
                str(st.session_state.get("wu_connected_station", "")).strip()
                and str(st.session_state.get("wu_connected_api_key", "")).strip()
            )
            current_target_has_credentials = bool(
                str(current_target.get("station", "")).strip()
                and str(current_target.get("api_key", "")).strip()
            )
            disable_event_is_safe = bool(
                st.session_state.get("_wu_autoconnect_disable_armed", False)
                and station_for_target
                and key_for_target
            )
            if (
                current_kind == "WU"
                and not disable_event_is_safe
                and (has_runtime_wu_credentials or current_target_has_credentials)
            ):
                # Defensa extra para el arranque: si la estación ya se ha
                # conectado automáticamente, o el target WU guardado aún no se
                # ha reflejado en los inputs, un falso callback del toggle no
                # debe desactivar nada.
                st.session_state["auto_connect_wu_device"] = True
                st.session_state["_wu_autoconnect_ui_target_kind"] = "WU"
                st.session_state["_wu_autoconnect_ui_last_value"] = True
            elif current_kind == "WU":
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
                st.session_state["_wu_autoconnect_ui_target_kind"] = ""
                st.sidebar.info(t("sidebar.autoconnect.disabled"))

        # Evita autoconectar en caliente en esta misma sesión.
        st.session_state[AUTOCONNECT_ATTEMPTED] = True
        st.session_state["_wu_autoconnect_ui_last_value"] = bool(
            st.session_state.get("auto_connect_wu_device", auto_connect_wu_device)
        )

    st.session_state["_wu_autoconnect_event_armed"] = True
    st.session_state["_wu_autoconnect_disable_armed"] = bool(
        st.session_state.get("auto_connect_wu_device", False)
        and str(st.session_state.get("active_station", "")).strip()
        and str(st.session_state.get("active_key", "")).strip()
    )

    cS, cF = st.sidebar.columns(2)
    with cS:
        save_clicked = st.button(t("sidebar.buttons.save"), width="stretch")
    with cF:
        forget_clicked = st.button(t("sidebar.buttons.forget"), width="stretch")

    if save_clicked:
        station_to_save = str(st.session_state.get("active_station", "")).strip()
        key_to_save = str(st.session_state.get("active_key", "")).strip()
        z_to_save = str(st.session_state.get("active_z", "")).strip()
        autoconnect_to_save = bool(st.session_state.get("auto_connect_wu_device", False))
        current_target = get_stored_autoconnect_target() or {}
        current_kind = str(current_target.get("kind", "")).strip().upper()
        save_warning_shown = False

        if autoconnect_to_save:
            if not station_to_save:
                station_to_save = str(
                    st.session_state.get("wu_connected_station")
                    or current_target.get("station", "")
                    or ""
                ).strip()
            if not key_to_save:
                key_to_save = str(
                    st.session_state.get("wu_connected_api_key")
                    or current_target.get("api_key", "")
                    or ""
                ).strip()
            if not z_to_save:
                z_to_save = str(
                    st.session_state.get("wu_connected_z")
                    or current_target.get("z", "")
                    or ""
                ).strip()

        set_local_storage(LS_STATION, station_to_save, "save")
        set_local_storage(LS_APIKEY, key_to_save, "save")
        set_local_storage(LS_Z, z_to_save, "save")
        set_local_storage(LS_WU_FORGOTTEN, "0", "save")

        # Guardar también la auto-conexión aquí evita que un rerun inmediato
        # relea un localStorage todavía no sincronizado y apague el toggle.
        if autoconnect_to_save and station_to_save and key_to_save:
            set_local_storage(LS_AUTOCONNECT, "1", "save")
            clear_provider_autoconnect_widget_state()
            set_stored_autoconnect_target(
                {
                    "kind": "WU",
                    "station": station_to_save,
                    "api_key": key_to_save,
                    "z": z_to_save,
                }
            )
            st.session_state["_wu_autoconnect_ui_target_kind"] = "WU"
            st.session_state["_wu_autoconnect_ui_last_value"] = True
        elif autoconnect_to_save:
            # No desactives la preferencia por una lectura tardía del formulario
            # en producción; conserva el toggle y pide completar credenciales.
            st.session_state["_wu_autoconnect_ui_last_value"] = True
            st.sidebar.warning(t("sidebar.autoconnect.missing_credentials"))
            save_warning_shown = True
        else:
            if current_kind == "WU":
                set_local_storage(LS_AUTOCONNECT, "0", "save")
                set_stored_autoconnect_target(None)
            st.session_state["_wu_autoconnect_ui_target_kind"] = ""
            st.session_state["_wu_autoconnect_ui_last_value"] = False

        if not save_warning_shown:
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
        auto_dark_local = _browser_prefers_dark()
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

    if disconnect_clicked:
        disconnect_active_station()

    if connect_clicked:
        station = str(st.session_state.get("active_station", "")).strip()
        api_key = str(st.session_state.get("active_key", "")).strip()
        z_raw = str(st.session_state.get("active_z", "")).strip()

        if not station or not api_key:
            st.sidebar.error(t("sidebar.messages.missing_station_or_key"))
        else:
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
                        apply_wu_station_state(station, api_key, z_raw, connected=True)
                except Exception:
                    st.sidebar.error(t("sidebar.messages.altitude_invalid"))
            else:  # Sin altitud manual, confiar en la API
                apply_wu_station_state(station, api_key, "", connected=True)

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
                clear_provider_autoconnect_widget_state()
                set_stored_autoconnect_target(
                    {
                        "kind": "WU",
                        "station": station,
                        "api_key": api_key,
                        "z": z_raw,
                    }
                )
                st.session_state[AUTOCONNECT_ATTEMPTED] = True

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
        if not storage_ready:
            st.sidebar.caption(t("sidebar.calibration.loading"))
        elif isinstance(sensor_presence, dict) and any(bool(sensor_presence.get(sensor)) for sensor in WU_CALIBRATION_ORDER):
            widget_station_key = "_wu_calibration_widget_station"
            if st.session_state.get(widget_station_key) != wu_station_id:
                for sensor in WU_CALIBRATION_ORDER:
                    st.session_state[f"wu_calibration_{wu_station_id}_{sensor}"] = float(
                        stored_calibration.get(sensor, 0.0)
                    )
                st.session_state[widget_station_key] = wu_station_id

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
        default_value = DEFAULT_UNIT_PREFERENCES[category]
        current_value = str(
            st.session_state.get(
                widget_key,
                saved_unit_preferences.get(category, default_value),
            )
        ).strip().lower()
        if current_value not in options:
            current_value = default_value
            st.session_state[widget_key] = current_value
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
    auto_dark = _browser_prefers_dark()
    
    if theme_mode == "auto":
        dark = auto_dark
    elif theme_mode == "dark":
        dark = True
    else:
        dark = False

    flush_local_storage_writes("mlx_local_storage_sidebar_flush")

    return theme_mode, dark

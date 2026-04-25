"""
Helpers para centralizar el estado de conexión y metadata de proveedores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st
from config import LS_AUTOCONNECT
from utils.state_keys import (
    ACTIVE_KEY,
    ACTIVE_STATION,
    ACTIVE_Z,
    AUTOCONNECT_ATTEMPTED,
    CONNECTED,
    CONNECTION_LOADING,
    CONNECTION_TYPE,
    ELEVATION_SOURCE,
    LAST_UPDATE_TIME,
    PENDING_ACTIVE_TAB,
    PROVIDER_STATION_ALT,
    PROVIDER_STATION_ID,
    PROVIDER_STATION_LAT,
    PROVIDER_STATION_LON,
    PROVIDER_STATION_NAME,
    PROVIDER_STATION_TZ,
    SHOW_RESULTS,
    STATION_ELEVATION,
    STATION_LAT,
    STATION_LON,
)
from utils.storage import set_local_storage, set_stored_autoconnect_target


def _meteocat_locality(meta: dict[str, Any]) -> str:
    municipi = meta.get("municipi")
    if isinstance(municipi, dict):
        return str(municipi.get("nom", "")).strip()
    return ""


def _euskalmet_locality(meta: dict[str, Any]) -> str:
    municipality = meta.get("municipality")
    if isinstance(municipality, dict):
        town = str(municipality.get("SPANISH", "")).strip() or str(municipality.get("BASQUE", "")).strip()
        return town.replace("[eu] ", "").strip()
    return ""


def _meteofrance_locality(meta: dict[str, Any]) -> str:
    return str(meta.get("pack", "")).strip()


def _frost_locality(meta: dict[str, Any]) -> str:
    return str(meta.get("municipality", "")).strip()


def _meteogalicia_locality(meta: dict[str, Any]) -> str:
    return str(meta.get("concello", "")).strip()


def _nws_locality(meta: dict[str, Any]) -> str:
    return str(meta.get("tz", "")).strip()


def _poem_locality(meta: dict[str, Any]) -> str:
    return str(meta.get("tipo", "")).strip()


PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "WU": {
        "label": "WU",
        "station_id_keys": ("active_station", "wu_connected_station"),
        "api_key_keys": ("active_key", "wu_connected_api_key"),
        "station_id_upper": False,
    },
    "AEMET": {
        "label": "AEMET",
        "session_prefix": "aemet",
        "station_id_keys": ("aemet_station_id", "provider_station_id"),
        "station_id_upper": True,
    },
    "METEOCAT": {
        "label": "Meteocat",
        "session_prefix": "meteocat",
        "station_id_keys": ("meteocat_station_id", "provider_station_id"),
        "station_id_upper": True,
        "locality_resolver": _meteocat_locality,
    },
    "EUSKALMET": {
        "label": "Euskalmet",
        "session_prefix": "euskalmet",
        "station_id_keys": ("euskalmet_station_id", "provider_station_id"),
        "station_id_upper": False,
        "locality_resolver": _euskalmet_locality,
    },
    "FROST": {
        "label": "Frost",
        "session_prefix": "frost",
        "station_id_keys": ("frost_station_id", "provider_station_id"),
        "station_id_upper": True,
        "default_tz": "Europe/Oslo",
        "locality_resolver": _frost_locality,
    },
    "METEOFRANCE": {
        "label": "Meteo-France",
        "session_prefix": "meteofrance",
        "station_id_keys": ("meteofrance_station_id", "provider_station_id"),
        "station_id_upper": False,
        "locality_resolver": _meteofrance_locality,
    },
    "METEOGALICIA": {
        "label": "MeteoGalicia",
        "session_prefix": "meteogalicia",
        "station_id_keys": ("meteogalicia_station_id", "provider_station_id"),
        "station_id_upper": False,
        "locality_resolver": _meteogalicia_locality,
    },
    "NWS": {
        "label": "NWS",
        "session_prefix": "nws",
        "station_id_keys": ("nws_station_id", "provider_station_id"),
        "station_id_upper": True,
        "locality_resolver": _nws_locality,
    },
    "POEM": {
        "label": "POEM",
        "session_prefix": "poem",
        "station_id_keys": ("poem_station_id", "provider_station_id"),
        "station_id_upper": False,
        "locality_resolver": _poem_locality,
    },
}

PROVIDER_RUNTIME_PREFIXES = (
    "aemet_",
    "provider_station_",
    "meteocat_",
    "euskalmet_",
    "frost_",
    "meteofrance_",
    "meteogalicia_",
    "nws_",
    "poem_",
)

WU_RUNTIME_KEYS = (
    "wu_connected_station",
    "wu_connected_api_key",
    "wu_connected_z",
    "wu_sensor_presence",
    "wu_sensor_presence_station",
    "wu_station_calibration",
    "wu_station_calibration_station",
)

CONNECTION_SNAPSHOT_BASE_KEYS = (
    CONNECTED,
    CONNECTION_TYPE,
    PROVIDER_STATION_ID,
    PROVIDER_STATION_NAME,
    PROVIDER_STATION_LAT,
    PROVIDER_STATION_LON,
    PROVIDER_STATION_ALT,
    PROVIDER_STATION_TZ,
    STATION_LAT,
    STATION_LON,
    STATION_ELEVATION,
    ELEVATION_SOURCE,
    LAST_UPDATE_TIME,
)

WIDGET_BOUND_CONNECTION_KEYS = {
    ACTIVE_STATION,
    ACTIVE_KEY,
    ACTIVE_Z,
}

PROVIDER_ERROR_KEYS = {
    "AEMET": "aemet_last_error",
    "EUSKALMET": "euskalmet_last_error",
    "FROST": "frost_last_error",
    "METEOFRANCE": "meteofrance_last_error",
    "POEM": "poem_last_error",
}


@dataclass(frozen=True)
class ConnectionSnapshot:
    provider_id: str
    station_name: str
    station_id: str
    lat: Any
    lon: Any
    elevation_m: Any


def resolve_state(state: Any = None) -> Any:
    return state if state is not None else st.session_state


def get_provider_spec(provider_id: str) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip().upper()
    return PROVIDER_SPECS.get(provider_id, {})


def get_provider_label(provider_id: str) -> str:
    provider_id = str(provider_id or "").strip().upper()
    spec = get_provider_spec(provider_id)
    return str(spec.get("label", provider_id or "Proveedor")).strip() or "Proveedor"


def get_provider_station_id(state: Any, provider_id: str) -> str:
    provider_id = str(provider_id or "").strip().upper()
    spec = get_provider_spec(provider_id)
    keys = tuple(spec.get("station_id_keys", ("provider_station_id",)))
    uppercase = bool(spec.get("station_id_upper"))
    for key in keys:
        try:
            value = str(state.get(key, "")).strip()
        except Exception:
            value = ""
        if value:
            return value.upper() if uppercase else value
    return ""


def get_connected_provider_station_id(provider_id: str, state: Any = None) -> str:
    state = resolve_state(state)
    return get_provider_station_id(state, provider_id) if is_provider_connection(provider_id, state) else ""


def current_connection_type(state: Any = None) -> str:
    state = resolve_state(state)
    try:
        return str(state.get(CONNECTION_TYPE, "")).strip().upper()
    except Exception:
        return ""


def is_provider_connection(provider_id: str, state: Any = None) -> bool:
    return current_connection_type(state) == str(provider_id or "").strip().upper()


def clear_provider_runtime_cache(provider_id: str) -> None:
    """
    Limpia cachés runtime del proveedor cuando existe soporte específico.
    """
    provider_id = str(provider_id or "").strip().upper()
    if provider_id == "AEMET":
        try:
            from services.aemet import clear_aemet_runtime_cache

            clear_aemet_runtime_cache()
        except Exception:
            pass


def _capture_connection_state() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in CONNECTION_SNAPSHOT_BASE_KEYS:
        if key in st.session_state:
            snapshot[key] = st.session_state.get(key)
    for key in WU_RUNTIME_KEYS:
        if key in st.session_state:
            snapshot[key] = st.session_state.get(key)
    for state_key in list(st.session_state.keys()):
        if state_key.startswith(PROVIDER_RUNTIME_PREFIXES):
            snapshot[state_key] = st.session_state.get(state_key)
    return snapshot


def restore_connection_state(snapshot: dict[str, Any] | None) -> None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    keys_to_clear = set(CONNECTION_SNAPSHOT_BASE_KEYS).union(WU_RUNTIME_KEYS)
    for state_key in list(st.session_state.keys()):
        if state_key.startswith(PROVIDER_RUNTIME_PREFIXES):
            keys_to_clear.add(state_key)
    keys_to_clear.difference_update(WIDGET_BOUND_CONNECTION_KEYS)
    for key in keys_to_clear:
        if key not in snapshot:
            st.session_state.pop(key, None)
    for key, value in snapshot.items():
        if key in WIDGET_BOUND_CONNECTION_KEYS:
            continue
        st.session_state[key] = value


def restore_connection_state_from_loading_payload(payload: dict[str, Any] | None = None) -> None:
    info = payload if isinstance(payload, dict) else st.session_state.get(CONNECTION_LOADING)
    snapshot = info.get("previous_state") if isinstance(info, dict) else None
    restore_connection_state(snapshot if isinstance(snapshot, dict) else {})


def set_connection_loading(
    provider_id: str,
    station_id: str,
    station_name: str,
    *,
    previous_state: dict[str, Any] | None = None,
) -> None:
    provider_id = str(provider_id or "").strip().upper()
    station_id = str(station_id or "").strip()
    station_name = str(station_name or station_id).strip() or station_id
    if not provider_id or not station_id:
        return
    st.session_state[CONNECTION_LOADING] = {
        "provider": provider_id,
        "station_id": station_id,
        "station_name": station_name,
        "started_at": time.time(),
        "previous_state": previous_state if isinstance(previous_state, dict) else _capture_connection_state(),
    }


def _clear_prefixed_session_keys(prefixes: tuple[str, ...]) -> None:
    for state_key in list(st.session_state.keys()):
        if state_key.startswith(prefixes):
            del st.session_state[state_key]


def disconnect_active_station(*, clear_runtime_cache: bool = True) -> None:
    """
    Limpia el estado runtime de la conexión actual, sea WU o proveedor.
    """
    current_provider = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
    if clear_runtime_cache and current_provider:
        clear_provider_runtime_cache(current_provider)

    st.session_state[CONNECTED] = False
    st.session_state[CONNECTION_TYPE] = None
    st.session_state.pop(CONNECTION_LOADING, None)

    for key in WU_RUNTIME_KEYS:
        st.session_state.pop(key, None)

    _clear_prefixed_session_keys(PROVIDER_RUNTIME_PREFIXES)


def apply_wu_station_state(
    station_id: str,
    api_key: str,
    altitude_text: str = "",
    *,
    connected: bool = True,
) -> bool:
    """
    Sincroniza en session_state una conexión explícita de Weather Underground.
    """
    station_id = str(station_id or "").strip()
    api_key = str(api_key or "").strip()
    altitude_text = str(altitude_text or "").strip()
    if not station_id or not api_key:
        return False

    previous_state = _capture_connection_state() if connected else None
    _clear_prefixed_session_keys(PROVIDER_RUNTIME_PREFIXES)
    st.session_state[CONNECTION_TYPE] = "WU"
    st.session_state["wu_connected_station"] = station_id
    st.session_state["wu_connected_api_key"] = api_key
    st.session_state["wu_connected_z"] = altitude_text
    st.session_state[CONNECTED] = bool(connected)
    if connected:
        set_connection_loading("WU", station_id, station_id, previous_state=previous_state)
    return True


def _station_value(station: Any, key: str, default: Any = None) -> Any:
    if isinstance(station, dict):
        return station.get(key, default)
    return getattr(station, key, default)


def apply_provider_station_state(
    provider_id: str,
    station_id: str,
    station_name: str,
    lat: Any,
    lon: Any,
    elevation_m: Any,
    *,
    station_tz: str = "",
    connected: bool | None = None,
    show_results: bool | None = None,
    pending_active_tab: str | None = None,
    clear_runtime_cache: bool = False,
) -> bool:
    """
    Sincroniza en session_state la estación seleccionada de un proveedor.
    """
    provider_id = str(provider_id or "").strip().upper()
    station_id = str(station_id or "").strip()
    station_name = str(station_name or station_id).strip() or station_id
    spec = get_provider_spec(provider_id)
    prefix = str(spec.get("session_prefix", "")).strip()

    if not provider_id or not station_id or not prefix:
        return False

    previous_state = _capture_connection_state() if connected else None

    if clear_runtime_cache:
        clear_provider_runtime_cache(provider_id)

    st.session_state[CONNECTION_TYPE] = provider_id
    st.session_state[PROVIDER_STATION_ID] = station_id
    st.session_state[PROVIDER_STATION_NAME] = station_name
    st.session_state[PROVIDER_STATION_LAT] = lat
    st.session_state[PROVIDER_STATION_LON] = lon
    st.session_state[PROVIDER_STATION_ALT] = elevation_m

    resolved_tz = str(station_tz or spec.get("default_tz", "")).strip()
    st.session_state[PROVIDER_STATION_TZ] = resolved_tz

    st.session_state[f"{prefix}_station_id"] = station_id
    st.session_state[f"{prefix}_station_name"] = station_name
    st.session_state[f"{prefix}_station_lat"] = lat
    st.session_state[f"{prefix}_station_lon"] = lon
    st.session_state[f"{prefix}_station_alt"] = elevation_m

    if connected is not None:
        st.session_state[CONNECTED] = connected
        if connected:
            set_connection_loading(provider_id, station_id, station_name, previous_state=previous_state)
    if show_results is not None:
        st.session_state[SHOW_RESULTS] = show_results
    if pending_active_tab is not None:
        st.session_state[PENDING_ACTIVE_TAB] = pending_active_tab

    return True


def apply_station_selection(
    station: Any,
    *,
    connected: bool | None = True,
    show_results: bool | None = None,
    pending_active_tab: str | None = None,
    clear_runtime_cache: bool = False,
    clear_search_coords: bool = False,
) -> bool:
    """
    Aplica al session_state una estación seleccionada, venga de dict o de objeto.
    """
    success = apply_provider_station_state(
        str(_station_value(station, "provider_id", "") or "").strip().upper(),
        str(_station_value(station, "station_id", "") or "").strip(),
        str(_station_value(station, "name", "") or _station_value(station, "station_id", "") or "").strip(),
        _station_value(station, "lat"),
        _station_value(station, "lon"),
        _station_value(station, "elevation_m"),
        station_tz=str(_station_value(station, "station_tz", "") or "").strip(),
        connected=connected,
        show_results=show_results,
        pending_active_tab=pending_active_tab,
        clear_runtime_cache=clear_runtime_cache,
    )
    if success and clear_search_coords:
        for key in ("search_lat", "search_lon"):
            st.session_state.pop(key, None)
    return success


def persist_provider_autoconnect_target(station: Any) -> bool:
    """
    Guarda una estación de proveedor como objetivo de auto-conexión.
    """
    provider_id = str(_station_value(station, "provider_id", "") or "").strip().upper()
    station_id = str(_station_value(station, "station_id", "") or "").strip()
    if not provider_id or not station_id:
        return False

    station_name = str(_station_value(station, "name", "") or station_id).strip() or station_id
    set_stored_autoconnect_target(
        {
            "kind": "PROVIDER",
            "provider_id": provider_id,
            "provider_label": get_provider_label(provider_id),
            "station_id": station_id,
            "station_name": station_name,
            "lat": _station_value(station, "lat"),
            "lon": _station_value(station, "lon"),
            "elevation_m": _station_value(station, "elevation_m"),
            "station_tz": str(_station_value(station, "station_tz", "") or "").strip(),
        }
    )
    set_local_storage(LS_AUTOCONNECT, "1", "save")
    st.session_state[AUTOCONNECT_ATTEMPTED] = False
    return True


def disable_provider_autoconnect(_toggle_prefix: str) -> None:
    """
    Desactiva la autoconexión de proveedor y limpia el estado efímero asociado.
    """
    set_local_storage(LS_AUTOCONNECT, "0", "save")
    set_stored_autoconnect_target(None)
    st.session_state[AUTOCONNECT_ATTEMPTED] = False
    # No borrar aquí los toggles renderizados en este mismo ciclo: Streamlit
    # puede rechazar cambios sobre keys de widgets ya instanciados. El valor
    # visible ya es False y el siguiente rerun se hidrata desde localStorage.


def resolve_provider_locality(provider_id: str, metadata: Any, fallback: str = "") -> str:
    """
    Devuelve la mejor localidad legible para una estación según su proveedor.
    """
    provider_id = str(provider_id or "").strip().upper()
    meta = metadata if isinstance(metadata, dict) else {}

    resolver: Callable[[dict[str, Any]], str] | None = get_provider_spec(provider_id).get("locality_resolver")
    if callable(resolver):
        town = str(resolver(meta) or "").strip()
        if town:
            return town

    provincia = meta.get("provincia")
    if isinstance(provincia, dict):
        province_name = str(provincia.get("nom", "")).strip()
        if province_name:
            return province_name

    province_txt = str(meta.get("provincia", "")).strip()
    if province_txt:
        return province_txt

    return str(fallback or "").strip()


def set_provider_runtime_error(provider_id: str, message: str, state: Any = None) -> None:
    state = resolve_state(state)
    error_key = PROVIDER_ERROR_KEYS.get(str(provider_id or "").strip().upper())
    if error_key:
        state[error_key] = str(message or "")


def clear_provider_runtime_error(provider_id: str, state: Any = None) -> None:
    set_provider_runtime_error(provider_id, "", state=state)


def get_provider_runtime_error(provider_id: str, state: Any = None) -> str:
    state = resolve_state(state)
    error_key = PROVIDER_ERROR_KEYS.get(str(provider_id or "").strip().upper())
    return str(state.get(error_key, "")).strip() if error_key else ""


def build_connection_snapshot(state: Any = None) -> ConnectionSnapshot | None:
    state = resolve_state(state)
    provider_id = current_connection_type(state)
    if not state.get(CONNECTED) or not provider_id:
        return None

    if provider_id == "WU":
        station_name = state.get(PROVIDER_STATION_NAME) or state.get(ACTIVE_STATION) or "Estación WU"
        station_id = state.get(PROVIDER_STATION_ID) or state.get(ACTIVE_STATION) or "—"
    elif provider_id == "AEMET":
        station_name = state.get("aemet_station_name") or state.get(PROVIDER_STATION_NAME) or "Estación AEMET"
        station_id = state.get("aemet_station_id") or state.get(PROVIDER_STATION_ID) or "—"
    else:
        station_name = state.get(PROVIDER_STATION_NAME) or "Estación"
        station_id = state.get(PROVIDER_STATION_ID) or "—"

    lat = state.get(f"{provider_id.lower()}_station_lat", state.get(PROVIDER_STATION_LAT, state.get("station_lat")))
    lon = state.get(f"{provider_id.lower()}_station_lon", state.get(PROVIDER_STATION_LON, state.get("station_lon")))
    alt = state.get(f"{provider_id.lower()}_station_alt", state.get(PROVIDER_STATION_ALT, state.get("station_elevation")))
    return ConnectionSnapshot(
        provider_id=provider_id,
        station_name=str(station_name),
        station_id=str(station_id),
        lat=lat,
        lon=lon,
        elevation_m=alt,
    )

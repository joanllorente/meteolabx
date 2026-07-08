"""
Helpers para centralizar el estado de conexión y metadata de proveedores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
import math
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
from utils.helpers import coerce_str


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


def _metoffice_locality(meta: dict[str, Any]) -> str:
    country = str(meta.get("country", "")).strip()
    region = str(meta.get("region", "")).strip()
    if region and region.lower() != "none":
        return f"{country} · {region}" if country else region
    return country


def _meteohub_locality(meta: dict[str, Any]) -> str:
    network = str(meta.get("network_name") or meta.get("network") or "").strip()
    attribution = str(meta.get("attribution") or "").strip()
    if network and attribution and attribution not in network:
        return f"{network} · {attribution}"
    return network or attribution


PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "WU": {
        "label": "WU",
        "station_id_keys": ("wu_connected_station", "active_station"),
        "api_key_keys": ("wu_connected_api_key", "active_key"),
        "station_id_upper": False,
    },
    "WEATHERLINK": {
        "label": "WeatherLink",
        "session_prefix": "weatherlink",
        "station_id_keys": ("weatherlink_station_id", "provider_station_id"),
        "api_key_keys": ("weatherlink_api_key",),
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
    "METOFFICE": {
        "label": "Met Office",
        "session_prefix": "metoffice",
        "station_id_keys": ("metoffice_station_id", "provider_station_id"),
        "station_id_upper": False,
        "default_tz": "Europe/London",
        "locality_resolver": _metoffice_locality,
    },
    "METEOHUB_IT": {
        "label": "MeteoHub IT",
        "session_prefix": "meteohub_it",
        "station_id_keys": ("meteohub_it_station_id", "meteohub_station_id", "provider_station_id"),
        "station_id_upper": False,
        "default_tz": "Europe/Rome",
        "locality_resolver": _meteohub_locality,
    },
    "IEM": {
        "label": "IEM",
        "session_prefix": "iem",
        "station_id_keys": ("iem_station_id", "provider_station_id"),
        "station_id_upper": False,
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
    "metoffice_",
    "meteohub_",
    "weatherlink_",
)

PROVIDER_AUTOCONNECT_WIDGET_PREFIXES = (
    "autoconnect_toggle_",
    "map_autoconnect_toggle_",
)

PROVIDER_AUTOCONNECT_CHANGED_KEYS = (
    "_provider_autoconnect_toggle_changed",
    "_map_provider_autoconnect_toggle_changed",
    "_provider_autoconnect_takeover_pending",
    "_provider_autoconnect_takeover_grace",
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

# Caches con datos de WU que se mantenían en ``st.session_state`` aunque el
# usuario hubiera desconectado, lo cual dejaba en memoria observaciones
# privadas. Se borran en :func:`disconnect_active_station`.
WU_CACHE_KEYS = (
    "wu_cache_current",
    "wu_cache_daily",
    "wu_cache_hourly7d",
    "wu_history_cache",
    "chart_series",
    "chart_series_provider_id",
    "chart_series_station_id",
    "trend_hourly_series_provider_id",
    "trend_hourly_series_station_id",
    "trend_hourly_epochs",
    "trend_hourly_temps",
    "trend_hourly_humidities",
    "trend_hourly_dewpts",
    "trend_hourly_pressures",
    "trend_hourly_uv_indexes",
    "trend_hourly_solar_radiations",
    "trend_hourly_winds",
    "trend_hourly_gusts",
    "trend_hourly_wind_dirs",
    "trend_hourly_precips",
    "has_trend_hourly_data",
    "chart_series_t",
    "chart_et0",
    "chart_balance",
    "last_update_time",
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
    "wu_input_station",
    "wu_input_api_key",
    "wu_input_altitude",
    "weatherlink_input_api_key",
    "weatherlink_input_api_secret",
    "weatherlink_input_altitude",
    "weatherlink_station_selector",
    "connection_source_selector",
    "auto_connect_wu_device",
    "auto_connect_weatherlink_device",
}

PROVIDER_ERROR_KEYS = {
    "AEMET": "aemet_last_error",
    "EUSKALMET": "euskalmet_last_error",
    "FROST": "frost_last_error",
    "METEOFRANCE": "meteofrance_last_error",
    "POEM": "poem_last_error",
    "METOFFICE": "metoffice_last_error",
    "METEOHUB_IT": "meteohub_last_error",
    "WEATHERLINK": "weatherlink_last_error",
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
    provider_id = coerce_str(provider_id, upper=True)
    return PROVIDER_SPECS.get(provider_id, {})


def get_provider_label(provider_id: str) -> str:
    provider_id = coerce_str(provider_id, upper=True)
    spec = get_provider_spec(provider_id)
    return str(spec.get("label", provider_id or "Proveedor")).strip() or "Proveedor"


def get_provider_station_id(state: Any, provider_id: str) -> str:
    provider_id = coerce_str(provider_id, upper=True)
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


def current_connection_type(state: Any = None) -> str:
    state = resolve_state(state)
    try:
        return str(state.get(CONNECTION_TYPE, "")).strip().upper()
    except Exception:
        return ""


def is_provider_connection(provider_id: str, state: Any = None) -> bool:
    return current_connection_type(state) == coerce_str(provider_id, upper=True)


def clear_provider_runtime_cache(provider_id: str) -> None:
    """Compatibility no-op: provider caches are owned by FastAPI."""


def _capture_connection_state() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in CONNECTION_SNAPSHOT_BASE_KEYS:
        if key in st.session_state and key not in WIDGET_BOUND_CONNECTION_KEYS:
            snapshot[key] = st.session_state.get(key)
    for key in WU_RUNTIME_KEYS:
        if key in st.session_state and key not in WIDGET_BOUND_CONNECTION_KEYS:
            snapshot[key] = st.session_state.get(key)
    for state_key in list(st.session_state.keys()):
        if state_key in WIDGET_BOUND_CONNECTION_KEYS:
            continue
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
        try:
            st.session_state[key] = value
        except Exception:
            # Si `key` es de un widget ya instanciado este run, Streamlit
            # prohíbe modificarlo (StreamlitAPIException). Se omite: conserva
            # el valor actual del widget en vez de petar la conexión.
            pass


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
    provider_id = coerce_str(provider_id, upper=True)
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
        if state_key in WIDGET_BOUND_CONNECTION_KEYS:
            continue
        if state_key.startswith(prefixes):
            del st.session_state[state_key]


def clear_provider_autoconnect_widget_state() -> None:
    """
    Limpia toggles efímeros de auto-conexión de proveedores.

    Se usa cuando WU pasa a ser el objetivo de auto-conexión para que ningún
    toggle antiguo de proveedor pueda volver a guardar su target en un rerun
    posterior solo porque Streamlit conservó su valor frontend.
    """
    _clear_prefixed_session_keys(PROVIDER_AUTOCONNECT_WIDGET_PREFIXES)
    for state_key in PROVIDER_AUTOCONNECT_CHANGED_KEYS:
        st.session_state.pop(state_key, None)


def disconnect_active_station(*, clear_runtime_cache: bool = True) -> None:
    """
    Limpia el estado runtime de la conexión actual, sea WU o proveedor.

    Además de las claves de credenciales (``WU_RUNTIME_KEYS``) y los
    prefijos de proveedor (``PROVIDER_RUNTIME_PREFIXES``), borra los caches
    de observaciones en memoria (``WU_CACHE_KEYS``) para que al desconectar
    no queden datos privados de la estación previa en ``st.session_state``.
    """
    current_provider = coerce_str(st.session_state.get(CONNECTION_TYPE, ""), upper=True)
    if clear_runtime_cache and current_provider:
        clear_provider_runtime_cache(current_provider)

    st.session_state[CONNECTED] = False
    st.session_state[CONNECTION_TYPE] = None
    st.session_state.pop(CONNECTION_LOADING, None)

    for key in WU_RUNTIME_KEYS:
        st.session_state.pop(key, None)

    for key in WU_CACHE_KEYS:
        st.session_state.pop(key, None)

    _clear_prefixed_session_keys(PROVIDER_RUNTIME_PREFIXES)


def _track_station_visit(provider_id: str, station_id: str, name: str = "") -> None:
    """Registra la conexión en las estadísticas internas (fire-and-forget).
    Cubre todas las vías de entrada: selector, mapa, ranking, deep links y
    autoconexión, porque se llama desde los ``apply_*_station_state``."""
    try:
        from utils.api_client import track_station_visit_via_api

        track_station_visit_via_api(provider_id, station_id, name)
    except Exception:
        pass


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

    # Si la sesión venía de otra estación o de otro proveedor, descartamos
    # los caches en memoria de la conexión anterior. Esto evita que queden
    # observaciones privadas de otra estación accesibles en session_state.
    prev_station = str(st.session_state.get("wu_connected_station", "") or "").strip()
    prev_provider = str(st.session_state.get(CONNECTION_TYPE, "") or "").strip().upper()
    if prev_provider != "WU" or (prev_station and prev_station != station_id):
        for cache_key in WU_CACHE_KEYS:
            st.session_state.pop(cache_key, None)

    st.session_state[CONNECTION_TYPE] = "WU"
    st.session_state["wu_connected_station"] = station_id
    st.session_state["wu_connected_api_key"] = api_key
    st.session_state["wu_connected_z"] = altitude_text
    st.session_state["_wu_runtime_sync_visible_inputs"] = True
    st.session_state[CONNECTED] = bool(connected)
    if connected:
        set_connection_loading("WU", station_id, station_id, previous_state=previous_state)
        _track_station_visit("WU", station_id)
    return True


def apply_weatherlink_station_state(
    station: dict[str, Any],
    api_key: str,
    api_secret: str,
    altitude_text: str = "",
    stations: list[dict[str, Any]] | None = None,
    *,
    connected: bool = True,
) -> bool:
    """
    Sincroniza en session_state una conexión explícita de WeatherLink.
    """
    station = dict(station or {})
    station_id = str(station.get("station_id") or station.get("station_id_uuid") or "").strip()
    station_name = str(station.get("station_name") or station.get("name") or station_id).strip() or station_id
    api_key = str(api_key or "").strip()
    api_secret = str(api_secret or "").strip()
    altitude_text = str(altitude_text or "").strip()
    if not station_id or not api_key or not api_secret:
        return False

    previous_state = _capture_connection_state() if connected else None
    _clear_prefixed_session_keys(PROVIDER_RUNTIME_PREFIXES)

    prev_provider = str(st.session_state.get(CONNECTION_TYPE, "") or "").strip().upper()
    prev_station = str(st.session_state.get(PROVIDER_STATION_ID, "") or "").strip()
    if prev_provider != "WEATHERLINK" or (prev_station and prev_station != station_id):
        for cache_key in WU_CACHE_KEYS:
            st.session_state.pop(cache_key, None)

    lat = station.get("latitude", station.get("lat"))
    lon = station.get("longitude", station.get("lon"))
    elevation_m = altitude_text if str(altitude_text).strip() else station.get("elevation", 0)
    station_tz = str(station.get("time_zone") or station.get("tz") or "").strip()

    st.session_state[CONNECTION_TYPE] = "WEATHERLINK"
    st.session_state[PROVIDER_STATION_ID] = station_id
    st.session_state[PROVIDER_STATION_NAME] = station_name
    st.session_state[PROVIDER_STATION_LAT] = lat
    st.session_state[PROVIDER_STATION_LON] = lon
    st.session_state[PROVIDER_STATION_ALT] = elevation_m
    st.session_state[PROVIDER_STATION_TZ] = station_tz

    st.session_state["weatherlink_station_id"] = station_id
    st.session_state["weatherlink_station_name"] = station_name
    st.session_state["weatherlink_station_lat"] = lat
    st.session_state["weatherlink_station_lon"] = lon
    st.session_state["weatherlink_station_alt"] = elevation_m
    st.session_state["weatherlink_station_tz"] = station_tz
    st.session_state["weatherlink_api_key"] = api_key
    st.session_state["weatherlink_api_secret"] = api_secret
    st.session_state["weatherlink_stations"] = list(stations or st.session_state.get("weatherlink_stations", []) or [])
    st.session_state["weatherlink_selected_station_id"] = station_id
    st.session_state[CONNECTED] = bool(connected)
    if connected:
        set_connection_loading("WEATHERLINK", station_id, station_name, previous_state=previous_state)
        _track_station_visit("WEATHERLINK", station_id, station_name)
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
    provider_id = coerce_str(provider_id, upper=True)
    station_id = str(station_id or "").strip()
    station_name = str(station_name or station_id).strip() or station_id
    spec = get_provider_spec(provider_id)
    prefix = str(spec.get("session_prefix", "")).strip()

    if not provider_id or not station_id or not prefix:
        return False

    previous_state = _capture_connection_state() if connected else None

    if clear_runtime_cache:
        clear_provider_runtime_cache(provider_id)

    # Si venimos de otro proveedor o estación, vaciamos los caches en memoria
    # para no servir series de la conexión previa al renderizar la nueva.
    prev_provider = str(st.session_state.get(CONNECTION_TYPE, "") or "").strip().upper()
    prev_station = str(st.session_state.get(PROVIDER_STATION_ID, "") or "").strip()
    if (
        prev_provider != provider_id
        or (prev_station and prev_station != station_id)
    ):
        for cache_key in WU_CACHE_KEYS:
            st.session_state.pop(cache_key, None)

    st.session_state[CONNECTION_TYPE] = provider_id
    st.session_state[PROVIDER_STATION_ID] = station_id
    st.session_state[PROVIDER_STATION_NAME] = station_name
    st.session_state[PROVIDER_STATION_LAT] = lat
    st.session_state[PROVIDER_STATION_LON] = lon
    st.session_state[PROVIDER_STATION_ALT] = elevation_m
    st.session_state["provider_station_catalog_alt"] = elevation_m
    st.session_state["provider_station_catalog_station_id"] = station_id

    resolved_tz = str(station_tz or spec.get("default_tz", "")).strip()
    st.session_state[PROVIDER_STATION_TZ] = resolved_tz

    st.session_state[f"{prefix}_station_id"] = station_id
    st.session_state[f"{prefix}_station_name"] = station_name
    st.session_state[f"{prefix}_station_lat"] = lat
    st.session_state[f"{prefix}_station_lon"] = lon
    st.session_state[f"{prefix}_station_alt"] = elevation_m
    st.session_state[f"{prefix}_station_catalog_alt"] = elevation_m
    st.session_state[f"{prefix}_station_catalog_station_id"] = station_id

    if connected is not None:
        st.session_state[CONNECTED] = connected
        if connected:
            set_connection_loading(provider_id, station_id, station_name, previous_state=previous_state)
            _track_station_visit(provider_id, station_id, station_name)
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
        coerce_str(_station_value(station, "provider_id", ""), upper=True),
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
    provider_id = coerce_str(_station_value(station, "provider_id", ""), upper=True)
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
    # Al mover la auto-conexión a un proveedor, el toggle WU de la sidebar ya
    # puede estar instanciado en este ciclo. No tocamos su key visible aquí,
    # pero sí dejamos preparado el siguiente rerun para que lo pinte apagado y
    # no procese un callback WU atrasado.
    st.session_state.pop("_wu_autoconnect_toggle_changed", None)
    st.session_state["_wu_autoconnect_ui_target_kind"] = "PROVIDER"
    st.session_state["_wu_autoconnect_ui_last_value"] = False
    st.session_state["_wu_autoconnect_disable_armed"] = False
    st.session_state["_provider_autoconnect_takeover_pending"] = True
    st.session_state["_provider_autoconnect_takeover_grace"] = 1
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


def is_manual_iem_station(state: Any) -> bool:
    """¿La estación conectada es MANUAL (observador humano vía IEM)?

    Las redes ``*_COOP`` (cooperativos del NWS, p.ej. ``NV_COOP|MOMN2``) y
    ``*COCORAHS*`` (voluntarios CoCoRaHS con pluviómetro) solo publican
    lecturas a mano una vez al día, sin datos en tiempo real ni serie horaria.
    Sirve para ocultar avisos de "datos antiguos / serie no disponible" que
    no aplican a estas estaciones. Mismo criterio que la columna ``manual``
    del catálogo (scripts/build_stations_sqlite.py).
    """
    if coerce_str(state.get(CONNECTION_TYPE, ""), upper=True) != "IEM":
        return False
    network = str(state.get(PROVIDER_STATION_ID, "") or "").split("|", 1)[0].strip().upper()
    return network.endswith("_COOP") or "COCORAHS" in network


def resolve_provider_locality(provider_id: str, metadata: Any, fallback: str = "") -> str:
    """
    Devuelve la mejor localidad legible para una estación según su proveedor.
    """
    provider_id = coerce_str(provider_id, upper=True)
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
    error_key = PROVIDER_ERROR_KEYS.get(coerce_str(provider_id, upper=True))
    if error_key:
        state[error_key] = str(message or "")


def display_provider_station_id(provider_id: str, station_id: Any) -> str:
    """
    Devuelve un identificador corto para UI sin cambiar el id interno.

    MeteoHub IT no expone siempre un station_id único en el inventario público,
    así que internamente usamos ``red|lat|lon|slug``. En pantalla basta con la
    red; lat/lon ya se muestran aparte.
    """
    provider = coerce_str(provider_id, upper=True)
    raw = str(station_id or "").strip()
    if provider == "METEOHUB_IT" and "|" in raw:
        network = raw.split("|", 1)[0].strip()
        return network or raw
    if provider == "IEM" and "|" in raw:
        network, station = (part.strip() for part in raw.split("|", 1))
        return f"{network} · {station}" if network and station else station or network or raw
    return raw


def _meaningful_state_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        return value
    return None


def _catalog_alt_for_station(
    state: Any,
    *,
    prefix: str,
    current_station_id: Any,
) -> Any:
    current = str(current_station_id or "").strip()
    for alt_key, station_key in (
        (f"{prefix}_station_catalog_alt", f"{prefix}_station_catalog_station_id"),
        ("provider_station_catalog_alt", "provider_station_catalog_station_id"),
    ):
        alt = state.get(alt_key)
        if _meaningful_state_value(alt) is None:
            continue
        catalog_station_id = str(state.get(station_key, "") or "").strip()
        if catalog_station_id and catalog_station_id == current:
            return alt
    return None


def build_connection_snapshot(state: Any = None) -> ConnectionSnapshot | None:
    state = resolve_state(state)
    provider_id = current_connection_type(state)
    if not state.get(CONNECTED) or not provider_id:
        return None

    if provider_id == "WU":
        station_id = state.get("wu_connected_station") or state.get(PROVIDER_STATION_ID) or state.get(ACTIVE_STATION) or "—"
        raw_station_id = station_id
        station_name = state.get(PROVIDER_STATION_NAME) or station_id or "Estación WU"
    elif provider_id == "AEMET":
        station_name = state.get("aemet_station_name") or state.get(PROVIDER_STATION_NAME) or "Estación AEMET"
        station_id = state.get("aemet_station_id") or state.get(PROVIDER_STATION_ID) or "—"
        raw_station_id = station_id
    else:
        station_name = state.get(PROVIDER_STATION_NAME) or "Estación"
        station_id = state.get(PROVIDER_STATION_ID) or "—"
        raw_station_id = station_id

    lat = state.get(f"{provider_id.lower()}_station_lat", state.get(PROVIDER_STATION_LAT, state.get("station_lat")))
    lon = state.get(f"{provider_id.lower()}_station_lon", state.get(PROVIDER_STATION_LON, state.get("station_lon")))
    prefix = provider_id.lower()
    alt = _meaningful_state_value(
        _catalog_alt_for_station(state, prefix=prefix, current_station_id=raw_station_id),
        state.get(f"{prefix}_station_alt"),
        state.get(PROVIDER_STATION_ALT),
        state.get("station_elevation"),
    )
    return ConnectionSnapshot(
        provider_id=provider_id,
        station_name=str(station_name),
        station_id=display_provider_station_id(provider_id, station_id),
        lat=lat,
        lon=lon,
        elevation_m=alt,
    )

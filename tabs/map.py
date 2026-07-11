import streamlit as st
import colorsys
from babel import Locale
from typing import Optional
from components.map_viewport import get_map_viewport
from components.geolocation_state import (
    consume_browser_geolocation,
    default_search_coords,
    ensure_geo_state,
    safe_float,
    start_browser_geolocation_request,
)
from providers.types import StationCandidate
from utils.geo import haversine_distance
from utils.helpers import coerce_str
from utils.favorites import favorite_from_provider_station, upsert_favorite
from utils.provider_state import display_provider_station_id


# IEM NO está en el mapa: agrega ~180k estaciones de todo el mundo (sin recorte
# espacial) y el ranking ya lo usa por su lado. En EE.UU. el mapa muestra las
# OFICIALES de NWS. IEM se mantiene como proveedor conectable (ranking/deep link)
# pero no se ofrece como capa del mapa.
ALL_MAP_PROVIDER_OPTIONS = ["AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE", "METEOGALICIA", "NWS", "POEM", "METOFFICE", "METEOHUB_IT"]
IEM_FALLBACK_MAP_PROVIDER = "IEM"
IEM_MAP_EXCLUDED_COUNTRIES = {"ES", "FR", "IT", "NO", "US"}
MAP_SENSOR_FILTER_OPTIONS = [
    "thermometer",
    "hygrometer",
    "barometer",
    "anemometer",
    "wind_vane",
    "rain_gauge",
    "pyranometer",
    "uv",
]
MAP_AUTOCONNECT_CHANGED_KEY = "_map_provider_autoconnect_toggle_changed"
MAP_AUTOCONNECT_SYNC_RERUN_KEY = "_map_provider_autoconnect_sync_rerun"
MAP_COUNTRY_FILTER_INITIALIZED_KEY = "map_country_filter_initialized"
MAP_COUNTRY_COUNTS_CACHE_VERSION = 2
REGIONAL_CATALOG_SPECS = {
    "AEMET": {"lat": 40.4168, "lon": -3.7038, "max_results": 1200},
    "METEOCAT": {"lat": 41.6200, "lon": 1.7500, "max_results": 260},
    "EUSKALMET": {"lat": 43.0000, "lon": -2.6000, "max_results": 160},
    "METEOGALICIA": {"lat": 42.7500, "lon": -8.7000, "max_results": 220},
    "POEM": {"lat": 40.4168, "lon": -3.7038, "max_results": 120},
    "METEOFRANCE": {"lat": 46.6034, "lon": 1.8883, "max_results": 2600},
    "FROST": {"lat": 64.5000, "lon": 11.0000, "max_results": 4000},
    "NWS": {"lat": 39.8283, "lon": -98.5795, "max_results": 38000},
    "METOFFICE": {"lat": 54.0000, "lon": -2.5000, "max_results": 260},
    "METEOHUB_IT": {"lat": 42.5000, "lon": 12.5000, "max_results": 5000},
}
MAP_PROVIDER_COUNTRIES = {
    "AEMET": {"ES"},
    "METEOCAT": {"ES"},
    "EUSKALMET": {"ES"},
    "METEOGALICIA": {"ES"},
    "POEM": {"ES"},
    "METEOFRANCE": {"FR"},
    "FROST": {"NO"},
    "NWS": {"US"},
    "METOFFICE": {"GB"},
    "METEOHUB_IT": {"IT"},
}


@st.cache_data(ttl=900, show_spinner=False)
def _cached_map_country_counts(
    provider_ids: tuple[str, ...],
    cache_version: int = MAP_COUNTRY_COUNTS_CACHE_VERSION,
) -> dict[str, int]:
    from utils.api_client import fetch_station_countries_via_api

    return fetch_station_countries_via_api(list(provider_ids))


def _fallback_map_country_counts(provider_ids: tuple[str, ...]) -> dict[str, int]:
    try:
        from server.services import stations

        return stations.country_counts(providers=list(provider_ids) or None)
    except Exception:
        return {}


DEFAULT_MAP_COUNTRY_BY_CENTER = {
    "iberia": ("ES",),
    "france": ("FR",),
    "norway": ("NO",),
    "uk": ("GB",),
    "italy": ("IT",),
    "us": ("US",),
}
_COUNTRY_LOCALE = Locale.parse("es")
COUNTRY_NAME_OVERRIDES = {
    "AN": "Antillas Neerlandesas",
    "CS": "Serbia y Montenegro",
    "DR": "República Dominicana",
    "KA": "Islas Carolinas (Palau/Micronesia)",
    "RQ": "Puerto Rico",
    "TU": "Turquía",
    "UNSPECIFIED": "Sin país",
}


def country_display_name(country_code: str) -> str:
    code = coerce_str(country_code, upper=True)
    if code in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[code]
    return str(_COUNTRY_LOCALE.territories.get(code) or code)


def country_sort_key(country_code: str) -> str:
    return country_display_name(country_code).casefold()


def country_uses_iem_map_fallback(country_code: str) -> bool:
    code = coerce_str(country_code, upper=True)
    return bool(code) and code not in IEM_MAP_EXCLUDED_COUNTRIES


def provider_country_filter(provider_id: str, selected_countries: list[str]) -> list[str]:
    allowed = MAP_PROVIDER_COUNTRIES.get(coerce_str(provider_id, upper=True))
    countries = [coerce_str(country, upper=True) for country in selected_countries]
    countries = [country for country in countries if country]
    if not allowed:
        return countries
    return [country for country in countries if country in allowed]


def regional_provider_ids_for_countries(
    provider_ids: list[str],
    selected_countries: list[str],
) -> tuple[str, ...]:
    """Providers whose official catalog overlaps the selected countries."""
    return tuple(
        provider_id
        for provider_id in sorted({coerce_str(item, upper=True) for item in provider_ids})
        if provider_id in REGIONAL_CATALOG_SPECS
        and provider_country_filter(provider_id, selected_countries)
    )


def regional_provider_batches(
    provider_ids: list[str],
    selected_countries: list[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Batch providers per country without letting one country crowd another."""
    batches = []
    for country in sorted({coerce_str(item, upper=True) for item in selected_countries}):
        if not country:
            continue
        providers = regional_provider_ids_for_countries(provider_ids, [country])
        if providers:
            batches.append((country, providers))
    return tuple(batches)


def regional_catalog_result_limit(provider_ids: tuple[str, ...]) -> int:
    return max(
        1,
        sum(
            int(REGIONAL_CATALOG_SPECS[provider_id]["max_results"])
            for provider_id in provider_ids
            if provider_id in REGIONAL_CATALOG_SPECS
        ),
    )


def map_country_default_enabled(
    country_code: str,
    default_countries: tuple[str, ...],
    filter_initialized: bool,
) -> bool:
    code = coerce_str(country_code, upper=True)
    defaults = {coerce_str(country, upper=True) for country in default_countries}
    return bool(code) and not filter_initialized and code in defaults


def _handle_map_country_selection_change(selection_key: str) -> None:
    st.session_state[MAP_COUNTRY_FILTER_INITIALIZED_KEY] = True
    selected = [
        coerce_str(item, upper=True)
        for item in st.session_state.get(selection_key, [])
        if coerce_str(item, upper=True)
    ]
    st.session_state["map_country_filter"] = sorted(set(selected), key=country_sort_key)


def _sync_map_autoconnect_toggle(toggle_key: str, is_target_station: bool) -> bool:
    changed_key = str(st.session_state.get(MAP_AUTOCONNECT_CHANGED_KEY, ""))
    if changed_key != toggle_key:
        st.session_state[toggle_key] = bool(is_target_station)
    return changed_key == toggle_key


def _clear_map_autoconnect_toggle_changed(toggle_key: str) -> None:
    if st.session_state.get(MAP_AUTOCONNECT_CHANGED_KEY) == toggle_key:
        st.session_state.pop(MAP_AUTOCONNECT_CHANGED_KEY, None)


def _handle_map_autoconnect_toggle_change(
    toggle_key: str,
    selected_station: dict,
    selected_name: str,
    is_target_station: bool,
    persist_provider_autoconnect_target,
    disable_provider_autoconnect,
    t_func,
) -> None:
    st.session_state[MAP_AUTOCONNECT_CHANGED_KEY] = toggle_key
    toggle_enabled = bool(st.session_state.get(toggle_key, False))
    if toggle_enabled:
        st.session_state["auto_connect_wu_device"] = False
        if persist_provider_autoconnect_target(selected_station):
            st.session_state["_map_provider_autoconnect_flash"] = t_func(
                "map.autoconnect_saved",
                station=selected_name,
            )
            st.session_state["_map_provider_autoconnect_flash_kind"] = "success"
            st.session_state[MAP_AUTOCONNECT_SYNC_RERUN_KEY] = {
                "action": "enable",
                "key": toggle_key,
            }
            _clear_map_autoconnect_toggle_changed(toggle_key)
        else:
            st.session_state["_map_provider_autoconnect_flash"] = t_func("map.autoconnect_save_error")
            st.session_state["_map_provider_autoconnect_flash_kind"] = "error"
    elif is_target_station:
        disable_provider_autoconnect("map_autoconnect_toggle_")
        st.session_state["_map_provider_autoconnect_flash"] = t_func("map.autoconnect_disabled")
        st.session_state["_map_provider_autoconnect_flash_kind"] = "info"
        st.session_state[MAP_AUTOCONNECT_SYNC_RERUN_KEY] = {
            "action": "disable",
            "key": toggle_key,
        }
        _clear_map_autoconnect_toggle_changed(toggle_key)


def is_us_map_center(lat: float, lon: float) -> bool:
    return 17.0 <= float(lat) <= 72.5 and -178.0 <= float(lon) <= -52.0


def is_iberia_map_center(lat: float, lon: float) -> bool:
    return 27.0 <= float(lat) <= 45.5 and -19.5 <= float(lon) <= 5.5


def is_france_map_center(lat: float, lon: float) -> bool:
    return 41.0 <= float(lat) <= 51.8 and -5.8 <= float(lon) <= 10.2


def is_norway_map_center(lat: float, lon: float) -> bool:
    return 57.0 <= float(lat) <= 72.5 and 2.0 <= float(lon) <= 32.5


def is_uk_map_center(lat: float, lon: float) -> bool:
    return 49.0 <= float(lat) <= 61.5 and -9.8 <= float(lon) <= 2.8


def is_italy_map_center(lat: float, lon: float) -> bool:
    return 35.0 <= float(lat) <= 48.5 and 5.0 <= float(lon) <= 19.5


def default_map_countries_for_center(lat: float, lon: float) -> tuple[str, ...]:
    # Un único país por defecto. Si una zona se solapa, gana el caso más
    # específico/esperado para el centro actual.
    if is_iberia_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["iberia"]
    if is_france_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["france"]
    if is_norway_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["norway"]
    if is_uk_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["uk"]
    if is_italy_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["italy"]
    if is_us_map_center(lat, lon):
        return DEFAULT_MAP_COUNTRY_BY_CENTER["us"]
    return ()


def provider_is_near_center(provider_id: str, lat: float, lon: float) -> bool:
    pid = coerce_str(provider_id, upper=True)
    if pid == "NWS":
        return is_us_map_center(lat, lon)
    if pid == "FROST":
        return is_norway_map_center(lat, lon)
    if pid == "METOFFICE":
        return is_uk_map_center(lat, lon)
    if pid == "METEOHUB_IT":
        return is_italy_map_center(lat, lon)
    if pid == "METEOFRANCE":
        return is_iberia_map_center(lat, lon) or is_france_map_center(lat, lon)
    if pid in {"AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "POEM"}:
        return is_iberia_map_center(lat, lon)
    return True


def regional_catalog_spec(provider_id: str) -> Optional[dict]:
    return REGIONAL_CATALOG_SPECS.get(coerce_str(provider_id, upper=True))


def split_map_provider_options(lat: float, lon: float, provider_options=None):
    options = list(provider_options or ALL_MAP_PROVIDER_OPTIONS)
    near = [provider_id for provider_id in options if provider_is_near_center(provider_id, lat, lon)]
    far = [provider_id for provider_id in options if provider_id not in near]
    return near, far


PROVIDER_DISPLAY_NAMES = {
    "AEMET": "AEMET",
    "METEOCAT": "Meteocat",
    "EUSKALMET": "Euskalmet",
    "FROST": "Frost",
    "METEOFRANCE": "Meteo-France",
    "METEOGALICIA": "MeteoGalicia",
    "NWS": "NWS",
    "POEM": "POEM",
    "METOFFICE": "Met Office",
    "METEOHUB_IT": "MeteoHub IT",
    "IEM": "IEM",
}


# Color por PAÍS (no por proveedor): todas las estaciones de un país comparten
# color, p.ej. AEMET + Meteocat + MeteoGalicia + Euskalmet → mismo rojo de España.
COUNTRY_COLORS = {
    "ES": [255, 75, 75, 220],
    "FR": [74, 124, 255, 220],
    "IT": [235, 112, 40, 220],
    "NO": [78, 180, 218, 220],
    "GB": [36, 168, 142, 220],
    "US": [178, 122, 255, 220],
}


def country_color(country_code) -> list:
    """Color RGBA estable por país, consistente entre recargas."""
    code = coerce_str(country_code, upper=True)
    if not code:
        return [180, 180, 180, 190]
    if code in COUNTRY_COLORS:
        return list(COUNTRY_COLORS[code])
    import hashlib

    seed = int(hashlib.md5(code.encode("utf-8")).hexdigest()[:8], 16)
    hue = ((seed % 360) / 360.0)
    saturation = 0.68
    lightness = 0.52
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return [int(red * 255), int(green * 255), int(blue * 255), 220]


def station_matches_sensor_filter(station: dict, selected_sensors: set[str]) -> bool:
    if not selected_sensors:
        return True
    if coerce_str(station.get("provider_id"), upper=True) == "NWS":
        return True
    sensors = station.get("sensors")
    if not isinstance(sensors, dict):
        return False
    return all(bool(sensors.get(sensor_key)) for sensor_key in selected_sensors)


def station_sensor_labels(station: dict, t_func) -> tuple[list[str], bool]:
    sensors = station.get("sensors")
    if not isinstance(sensors, dict):
        return [], False
    known_sensor_keys = [sensor_key for sensor_key in MAP_SENSOR_FILTER_OPTIONS if sensor_key in sensors]
    if not known_sensor_keys:
        return [], False
    return [
        str(t_func(f"map.sensors.{sensor_key}"))
        for sensor_key in MAP_SENSOR_FILTER_OPTIONS
        if bool(sensors.get(sensor_key))
    ], True


def _parse_map_sensor_query(raw_value) -> list[str]:
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    raw = str(raw_value or "").strip()
    if not raw:
        return []
    selected = []
    for item in raw.split(","):
        key = str(item or "").strip()
        if key in MAP_SENSOR_FILTER_OPTIONS and key not in selected:
            selected.append(key)
    return selected


def _map_cache_key(provider_id: str, lat: float, lon: float, catalog_version=()) -> tuple[str, float, float, tuple]:
    return (
        coerce_str(provider_id, upper=True),
        round(float(lat), 4),
        round(float(lon), 4),
        tuple(catalog_version or ()),
    )


def _map_view_anchor_changed(lat: float, lon: float, state: dict) -> bool:
    try:
        anchor_lat = float(state.get("anchor_lat"))
        anchor_lon = float(state.get("anchor_lon"))
    except (TypeError, ValueError):
        return True
    return abs(float(lat) - anchor_lat) > 0.0001 or abs(float(lon) - anchor_lon) > 0.0001


def _map_session_view_state(lat: float, lon: float, default_zoom: float) -> dict:
    """
    Mantiene estable la cámara inicial del mapa entre cambios de filtros.

    ``st.pydeck_chart`` no expone el pan/zoom actual del usuario en Python; si
    enviamos un ``initial_view_state`` distinto en cada rerun, DeckGL vuelve al
    centro base. Congelamos la cámara inicial mientras el centro lógico
    (buscar/usar ubicación) no cambie, de modo que los filtros actualizan capas
    sin forzar un reset de viewport.
    """
    state = st.session_state.get("map_view_state")
    if not isinstance(state, dict) or _map_view_anchor_changed(lat, lon, state):
        state = {
            "anchor_lat": float(lat),
            "anchor_lon": float(lon),
            "latitude": float(lat),
            "longitude": float(lon),
            "zoom": float(default_zoom),
        }
        st.session_state["map_view_state"] = state
    return state


def _deck_frozen_view_state(map_view_state: dict, signature: tuple) -> dict:
    """
    ``initial_view_state`` estable entre reruns solo-viewport.

    Cada emisión del componente de viewport (pan/zoom del usuario) provoca un
    rerun del fragmento. Si ese rerun reenvía el deck con la cámara recién
    capturada como ``initial_view_state``, las props del componente DeckGL
    cambian y el mapa se reinicializa: es el parpadeo visible en cada gesto.

    La cámara renderizada solo debe saltar a la capturada cuando el CONTENIDO
    del deck cambia de verdad (filtros, tema, centro de búsqueda) — en ese
    momento el repintado es inevitable y queremos preservar el pan/zoom del
    usuario. Mientras la firma no cambie, se reenvía exactamente el mismo
    ``initial_view_state`` (deck byte-idéntico → React no toca el mapa y la
    cámara del navegador persiste sola).
    """
    frozen = st.session_state.get("map_render_view_state")
    if not isinstance(frozen, dict) or frozen.get("signature") != signature:
        frozen = {
            "signature": signature,
            "latitude": float(map_view_state["latitude"]),
            "longitude": float(map_view_state["longitude"]),
            "zoom": float(map_view_state["zoom"]),
        }
        st.session_state["map_render_view_state"] = frozen
    return frozen


def _coerce_map_viewport_state(value) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    latitude = safe_float(value.get("latitude"), None)
    longitude = safe_float(value.get("longitude"), None)
    zoom = safe_float(value.get("zoom"), None)
    if latitude is None or longitude is None or zoom is None:
        return None
    if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
        return None
    if zoom < 0 or zoom > 24:
        return None
    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "zoom": float(zoom),
    }


def _sync_map_view_state_from_browser(lat: float, lon: float, theme_mode: str) -> None:
    """
    Captura el pan/zoom real del navegador.

    Streamlit reconstruye el DeckGL al cambiar widgets dentro del fragmento; si
    solo conocemos el centro lógico, un filtro puede devolver el mapa al zoom
    inicial. El componente lee la cámara montada en el DOM y la guardamos como
    nuevo initial_view_state para el siguiente rerun del mismo centro.
    """
    viewport = _coerce_map_viewport_state(
        get_map_viewport(key=f"map_viewport_sync_{theme_mode}")
    )
    if viewport is None:
        return
    state = st.session_state.get("map_view_state")
    if not isinstance(state, dict) or _map_view_anchor_changed(lat, lon, state):
        return
    st.session_state["map_view_state"] = {
        **state,
        **viewport,
        "anchor_lat": float(lat),
        "anchor_lon": float(lon),
    }


def render_map_tab(ctx):
    section_title = ctx["section_title"]
    t = ctx["t"]
    dark = ctx["dark"]
    theme_mode = ctx["theme_mode"]
    html = ctx["html"]
    html_clean = ctx["html_clean"]
    get_browser_geolocation = ctx["get_browser_geolocation"]
    get_stored_autoconnect = ctx["get_stored_autoconnect"]
    get_stored_autoconnect_target = ctx["get_stored_autoconnect_target"]
    resolve_provider_locality = ctx["resolve_provider_locality"]
    apply_station_selection = ctx["apply_station_selection"]
    disable_provider_autoconnect = ctx["disable_provider_autoconnect"]
    persist_provider_autoconnect_target = ctx["persist_provider_autoconnect_target"]
    _cached_map_search_nearby_stations = ctx["_cached_map_search_nearby_stations"]
    _map_catalog_cache_version = ctx.get("_map_catalog_cache_version", lambda provider_ids: ())
    _pydeck_chart_stretch = ctx["_pydeck_chart_stretch"]
    import pydeck as pdk

    section_title(t("map.section_title"))
    favorite_flash = st.session_state.pop("_map_favorite_flash", "")
    if favorite_flash:
        st.success(favorite_flash)

    def _map_default_coords():
        return default_search_coords(
            search_lat_key="map_search_lat",
            search_lon_key="map_search_lon",
            fallback_lat_values=(
                st.session_state.get("provider_station_lat"),
                st.session_state.get("aemet_station_lat"),
                st.session_state.get("station_lat"),
            ),
            fallback_lon_values=(
                st.session_state.get("provider_station_lon"),
                st.session_state.get("aemet_station_lon"),
                st.session_state.get("station_lon"),
            ),
            default_lat=40.4168,
            default_lon=-3.7038,
        )

    def _zoom_for_max_distance(max_distance_km: float) -> float:
        if max_distance_km <= 5:
            return 10.8
        if max_distance_km <= 15:
            return 9.5
        if max_distance_km <= 35:
            return 8.3
        if max_distance_km <= 80:
            return 7.3
        if max_distance_km <= 180:
            return 6.3
        return 5.5

    def _candidate_to_map_row(candidate: StationCandidate) -> dict:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
        return {
            "lat": float(candidate.lat),
            "lon": float(candidate.lon),
            "name": candidate.name,
            "provider": candidate.provider_name,
            "provider_id": candidate.provider_id,
            "network": str(metadata.get("network") or "").strip(),
            "station_id": candidate.station_id,
            "country": coerce_str(metadata.get("country"), upper=True),
            "connectable": bool(getattr(candidate, "connectable", True)),
            "has_historical": bool(metadata.get("has_historical", False)),
            "is_historical_only": bool(metadata.get("is_historical_only", False)),
            "manual": bool(metadata.get("manual", False)),
            "distance_km": float(haversine_distance(search_lat, search_lon, candidate.lat, candidate.lon)),
            "locality": resolve_provider_locality(candidate.provider_id, metadata, candidate.name),
            "elevation_m": float(candidate.elevation_m),
            "station_tz": str(metadata.get("tz", "")).strip(),
            "sensors": dict(metadata.get("sensors", {})) if isinstance(metadata.get("sensors"), dict) else {},
        }

    def _extend_unique_candidates(target: list[dict], candidates: list[dict]) -> None:
        seen = {
            (item["provider_id"], item.get("network", ""), item["station_id"])
            for item in target
        }
        for candidate in candidates:
            key = (candidate["provider_id"], candidate.get("network", ""), candidate["station_id"])
            if key in seen:
                continue
            target.append(candidate)
            seen.add(key)

    def _load_regional_candidates_batch(
        provider_ids: tuple[str, ...],
        country_filter: list[str],
        *,
        historical_only: bool = False,
        hide_historical_only: bool = False,
    ) -> list[dict]:
        if not provider_ids:
            return []
        cache_store = st.session_state.setdefault("map_regional_rows_cache", {})
        catalog_version = _map_catalog_cache_version(provider_ids)
        cache_key = (
            _map_cache_key(
                "REGIONAL:" + ",".join(provider_ids),
                search_lat,
                search_lon,
                catalog_version,
            ),
            tuple(sorted(country_filter)),
            bool(historical_only),
            bool(hide_historical_only),
        )
        cached_rows = cache_store.get(cache_key)
        if (
            isinstance(cached_rows, list)
            and all(isinstance(row, dict) and "sensors" in row for row in cached_rows)
        ):
            return [dict(row) for row in cached_rows]
        regional_candidates = _cached_map_search_nearby_stations(
            float(search_lat),
            float(search_lon),
            regional_catalog_result_limit(provider_ids),
            provider_ids,
            tuple(sorted(country_filter)),
            catalog_version,
            bool(historical_only),
            bool(hide_historical_only),
        )
        rows = [_candidate_to_map_row(candidate) for candidate in regional_candidates]
        rows.sort(key=lambda row: float(row["distance_km"]))
        cache_store[cache_key] = [dict(row) for row in rows]
        return rows

    def _load_iem_country_candidates(
        country_filter: list[str],
        *,
        historical_only: bool = False,
        hide_historical_only: bool = False,
    ) -> list[dict]:
        countries = [coerce_str(country, upper=True) for country in country_filter if coerce_str(country, upper=True)]
        if not countries:
            return []
        cache_store = st.session_state.setdefault("map_iem_country_rows_cache", {})
        provider_ids = (IEM_FALLBACK_MAP_PROVIDER,)
        catalog_version = _map_catalog_cache_version(provider_ids)
        cache_key = (
            _map_cache_key(IEM_FALLBACK_MAP_PROVIDER, search_lat, search_lon, catalog_version),
            tuple(sorted(countries)),
            bool(historical_only),
            bool(hide_historical_only),
        )
        cached_rows = cache_store.get(cache_key)
        if (
            isinstance(cached_rows, list)
            and all(isinstance(row, dict) and "sensors" in row for row in cached_rows)
        ):
            return [dict(row) for row in cached_rows]
        candidates = _cached_map_search_nearby_stations(
            float(search_lat),
            float(search_lon),
            5000,
            provider_ids,
            tuple(sorted(countries)),
            catalog_version,
            bool(historical_only),
            bool(hide_historical_only),
        )
        rows = [_candidate_to_map_row(candidate) for candidate in candidates]
        rows.sort(key=lambda row: float(row["distance_km"]))
        cache_store[cache_key] = [dict(row) for row in rows]
        return rows

    ensure_geo_state("map_geo", request_id_start=10000)

    default_lat, default_lon = _map_default_coords()
    if "map_search_lat" not in st.session_state or safe_float(st.session_state.get("map_search_lat")) is None:
        st.session_state["map_search_lat"] = default_lat
    if "map_search_lon" not in st.session_state or safe_float(st.session_state.get("map_search_lon")) is None:
        st.session_state["map_search_lon"] = default_lon
    # El menú flotante del mapa filtra por país; los proveedores son internos.
    if "map_sensor_filter" not in st.session_state:
        st.session_state["map_sensor_filter"] = _parse_map_sensor_query(
            st.query_params.get("map_sensors", st.query_params.get("_map_sensors", ""))
        )

    browser_geo_result = consume_browser_geolocation(
        "map_geo",
        get_browser_geolocation=get_browser_geolocation,
        timeout_ms=12000,
        high_accuracy=True,
    )
    if isinstance(browser_geo_result, dict):
        if browser_geo_result.get("ok"):
            st.session_state["map_search_lat"] = browser_geo_result["lat"]
            st.session_state["map_search_lon"] = browser_geo_result["lon"]
            acc = browser_geo_result.get("accuracy_m")
            if isinstance(acc, (int, float)):
                st.session_state["map_geo_debug_msg"] = t("map.geo_detected_accuracy", accuracy=acc)
            else:
                st.session_state["map_geo_debug_msg"] = t("map.geo_detected")
            if browser_geo_result.get("swapped"):
                st.session_state["map_geo_debug_msg"] += t("map.coords_swapped")
            st.session_state["map_geo_last_error"] = ""
            st.rerun()
        else:
            error_message = browser_geo_result.get("error_message") or t("map.geo_error_default")
            st.session_state["map_geo_last_error"] = str(error_message)
            st.session_state["map_geo_debug_msg"] = ""

    search_lat = float(st.session_state.get("map_search_lat"))
    search_lon = float(st.session_state.get("map_search_lon"))

    @st.fragment
    def _map_results_area() -> None:
        # Fragmento (st.fragment): los widgets de aquí dentro (filtro de
        # sensores, clic en estaciones del mapa) re-ejecutan SOLO este
        # bloque. Las acciones que afectan al resto de la app (conectar,
        # favoritos, autoconexión) usan st.rerun(scope="app").
        _render_map_results()

    def _render_map_results() -> None:
        _sync_map_view_state_from_browser(search_lat, search_lon, theme_mode)

        # Barra superior: métricas (estaciones visibles / proveedores) a la
        # izquierda y el botón de ubicación a la derecha. Los valores de las
        # métricas se rellenan más abajo, cuando ya está calculado `nearest`.
        metric_col1, metric_col2, _metrics_spacer, loc_btn_col = st.columns(
            [0.5, 0.5, 0.15, 1.1], gap="small",
        )
        with loc_btn_col:
            if st.button(t("map.use_my_location"), type="primary", width="stretch"):
                start_browser_geolocation_request("map_geo", message="Solicitando ubicación al navegador...")
                st.rerun()
            if st.session_state.get("map_geo_pending"):
                st.caption(t("map.waiting_geolocation"))
            geo_last_error = st.session_state.get("map_geo_last_error", "").strip()
            if geo_last_error:
                st.warning(t("map.gps_unavailable"))
                st.caption(t("map.browser_detail", detail=geo_last_error))
            geo_debug_msg = st.session_state.get("map_geo_debug_msg", "")
            if geo_debug_msg:
                st.caption(geo_debug_msg)
            st.caption(
                t(
                    "map.center_current",
                    lat=float(st.session_state.get("map_search_lat")),
                    lon=float(st.session_state.get("map_search_lon")),
                )
            )

        # Menú flotante de países sobre la esquina superior izquierda. Un
        # único multiselect evita montar un widget por cada país y re-filtra
        # el fragmento al instante sin recargar la página completa.
        all_provider_options = list(ALL_MAP_PROVIDER_OPTIONS)
        provider_filter = set(all_provider_options)
        try:
            country_counts = _cached_map_country_counts((), MAP_COUNTRY_COUNTS_CACHE_VERSION)
        except Exception:
            country_counts = _fallback_map_country_counts(())
        country_options = [
            country for country, count in sorted(
                country_counts.items(),
                key=lambda item: country_sort_key(str(item[0])),
            )
            if country and country != "UNSPECIFIED"
            and country != "UN"
        ]
        default_countries = default_map_countries_for_center(search_lat, search_lon)
        default_country_key = ",".join(default_countries)
        country_filter_initialized = bool(
            st.session_state.get(MAP_COUNTRY_FILTER_INITIALIZED_KEY, False)
        )
        if country_options and not country_filter_initialized:
            default_selected_countries: list[str] = []
            for country in country_options:
                enabled = map_country_default_enabled(
                    country,
                    default_countries,
                    country_filter_initialized,
                )
                if enabled:
                    default_selected_countries.append(country)
            country_filter_initialized = True
            st.session_state[MAP_COUNTRY_FILTER_INITIALIZED_KEY] = True
            st.session_state["map_country_default_key"] = default_country_key
            st.session_state["map_country_filter"] = default_selected_countries
            st.session_state["map_country_picker"] = default_selected_countries
        elif "map_country_picker" not in st.session_state:
            stored_countries = {
                coerce_str(country, upper=True)
                for country in st.session_state.get("map_country_filter", [])
                if coerce_str(country, upper=True)
            }
            st.session_state["map_country_picker"] = [
                country for country in country_options if country in stored_countries
            ]

        def _clear_map_country_filter() -> None:
            st.session_state[MAP_COUNTRY_FILTER_INITIALIZED_KEY] = True
            st.session_state["map_country_filter"] = []
            st.session_state["map_country_picker"] = []

        # Popover flotante: permanece cerrado mientras se navega por el mapa
        # y se cierra de forma nativa al interactuar fuera de él.
        with st.container(key="map_country_overlay"):
            with st.popover(str(t("map.country_filter"))):
                st.markdown(
                    f"<div class='mlbx-country-menu-title'>{html.escape(str(t('map.country_filter')).upper())}</div>",
                    unsafe_allow_html=True,
                )
                country_selection = st.multiselect(
                    t("map.country_filter"),
                    options=country_options,
                    key="map_country_picker",
                    placeholder=str(t("map.country_search")),
                    format_func=country_display_name,
                    label_visibility="collapsed",
                    on_change=_handle_map_country_selection_change,
                    args=("map_country_picker",),
                )
                st.button(
                    t("map.country_clear"),
                    key="map_country_clear_btn",
                    on_click=_clear_map_country_filter,
                    width="stretch",
                )
        selected_codes = {
            coerce_str(country, upper=True)
            for country in country_selection
            if coerce_str(country, upper=True)
        }
        selected_countries = [country for country in country_options if country in selected_codes]
        st.session_state["map_country_filter"] = selected_countries

        provider_overlay_bg = "rgba(13,17,24,0.96)" if dark else "rgba(255,255,255,0.97)"
        provider_overlay_border = "rgba(255,255,255,0.14)" if dark else "rgba(15,18,25,0.14)"
        provider_overlay_shadow = "0 10px 24px rgba(0,0,0,0.28)" if dark else "0 10px 24px rgba(0,0,0,0.12)"
        provider_overlay_hover_bg = "rgba(33,40,54,0.98)" if dark else "rgba(236,239,243,0.98)"
        provider_overlay_icon_stroke = "%23f4f7fb" if dark else "%23242933"
        country_badge_css = (
            f"""
            .st-key-map_country_overlay [data-testid="stPopoverButton"]::after {{
                content: "{len(selected_countries)}";
                position: absolute;
                top: -5px;
                right: -5px;
                min-width: 17px;
                height: 17px;
                padding: 0 4px;
                border-radius: 999px;
                background: #ff5a54;
                color: #fff;
                font: 700 10px/17px system-ui, -apple-system, "Segoe UI", sans-serif;
                text-align: center;
            }}
            """
            if selected_countries else ""
        )
        st.markdown(
            f"""
            <style>
            .st-key-map_country_overlay {{
                position: relative;
                height: 0;
                overflow: visible;
                z-index: 61;
            }}
            /* PyDeck tarda unas décimas en montar el canvas. Retrasamos solo
               la pintura; los controles conservan siempre sus eventos para
               no depender de :has() ni del estado interno de Streamlit. */
            .st-key-map_country_overlay,
            .st-key-map_sensor_overlay {{
                animation: mlbxMapControlIn 0.2s ease 0.55s both;
            }}
            @keyframes mlbxMapControlIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}
            .st-key-map_country_overlay [data-testid="stPopover"] {{
                position: absolute;
                left: 5px;
                top: 70px;
                width: auto;
            }}
            .st-key-map_country_overlay [data-testid="stPopoverButton"] {{
                position: relative;
                width: 40px;
                height: 40px;
                min-height: 40px;
                padding: 0;
                border-radius: 12px;
                background: {provider_overlay_bg} !important;
                border: 1px solid {provider_overlay_border} !important;
                box-shadow: {provider_overlay_shadow};
                justify-content: center;
            }}
            .st-key-map_country_overlay [data-testid="stPopoverButton"]:hover,
            .st-key-map_country_overlay [data-testid="stPopoverButton"]:active {{
                background: {provider_overlay_hover_bg} !important;
            }}
            .st-key-map_country_overlay [data-testid="stPopoverButton"] p,
            .st-key-map_country_overlay [data-testid="stPopoverButton"] svg {{
                display: none;
            }}
            .st-key-map_country_overlay [data-testid="stPopoverButton"]::before {{
                content: "";
                display: block;
                width: 23px;
                height: 23px;
                background-repeat: no-repeat;
                background-position: center;
                background-size: 23px 23px;
                background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='{provider_overlay_icon_stroke}' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Cpath d='M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9S14.5 18.5 12 21M12 3c-2.5 2.5-3.8 5.5-3.8 9S9.5 18.5 12 21'/%3E%3C/svg%3E");
            }}
            {country_badge_css}
            .st-key-map_country_clear_btn button {{
                padding: 1px 8px;
                min-height: 0;
                font-size: 11px;
            }}
            .mlbx-country-menu-title {{
                font: 700 11px/1.2 system-ui, -apple-system, "Segoe UI", sans-serif;
                color: var(--text);
                margin-bottom: 4px;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                opacity: 0.82;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )

        def _clear_map_sensor_filter() -> None:
            for sensor_key in MAP_SENSOR_FILTER_OPTIONS:
                st.session_state[f"map_sensor_chk_{sensor_key}"] = False

        # Filtro de sensores: popover nativo flotando sobre el mapa (mismo
        # diseño que el antiguo control inyectado: botón cuadrado bajo el
        # zoom, badge rojo con el nº de filtros activos). Al ser un widget
        # de Streamlit dentro del fragmento, marcar un checkbox re-filtra
        # el mapa al instante sin recargar la página.
        stored_sensor_selection = set(st.session_state.get("map_sensor_filter", []))
        if "map_historical_only" not in st.session_state:
            raw_historical_filter = st.query_params.get("map_historical", "")
            if isinstance(raw_historical_filter, list):
                raw_historical_filter = raw_historical_filter[0] if raw_historical_filter else ""
            st.session_state["map_historical_only"] = str(raw_historical_filter).strip().lower() in {
                "1", "true", "yes", "si", "sí",
            }
        if "map_hide_historical_only" not in st.session_state:
            raw_hide_historical = st.query_params.get("map_hide_historical", "")
            if isinstance(raw_hide_historical, list):
                raw_hide_historical = raw_hide_historical[0] if raw_hide_historical else ""
            st.session_state["map_hide_historical_only"] = str(raw_hide_historical).strip().lower() in {
                "1", "true", "yes", "si", "sí",
            }
        if "map_hide_manual" not in st.session_state:
            raw_hide_manual = st.query_params.get("map_hide_manual", "")
            if isinstance(raw_hide_manual, list):
                raw_hide_manual = raw_hide_manual[0] if raw_hide_manual else ""
            st.session_state["map_hide_manual"] = str(raw_hide_manual).strip().lower() in {
                "1", "true", "yes", "si", "sí",
            }
        for sensor_key in MAP_SENSOR_FILTER_OPTIONS:
            chk_key = f"map_sensor_chk_{sensor_key}"
            if chk_key not in st.session_state:
                st.session_state[chk_key] = sensor_key in stored_sensor_selection

        with st.container(key="map_sensor_overlay"):
            with st.popover(
                str(t("map.sensor_filter")),
            ):
                st.caption(t("map.sensor_filter_caption"))
                historical_only = st.toggle(
                    t("map.historical_only"),
                    key="map_historical_only",
                )
                hide_historical_only = st.toggle(
                    t("map.hide_historical_only"),
                    key="map_hide_historical_only",
                    help=t("map.hide_historical_only_help"),
                )
                hide_manual = st.toggle(
                    t("map.hide_manual"),
                    key="map_hide_manual",
                    help=t("map.hide_manual_help"),
                )
                st.divider()
                selected_sensor_list = [
                    sensor_key
                    for sensor_key in MAP_SENSOR_FILTER_OPTIONS
                    if st.checkbox(
                        str(t(f"map.sensors.{sensor_key}")),
                        key=f"map_sensor_chk_{sensor_key}",
                    )
                ]
                st.button(
                    t("map.sensor_filter_clear"),
                    key="map_sensor_filter_clear_btn",
                    on_click=_clear_map_sensor_filter,
                )
        st.session_state["map_sensor_filter"] = selected_sensor_list
        # Mantener la URL compartible: actualiza la query string sin
        # recargar (al contrario que el antiguo location.assign).
        try:
            if selected_sensor_list:
                st.query_params["map_sensors"] = ",".join(sorted(selected_sensor_list))
            elif "map_sensors" in st.query_params:
                del st.query_params["map_sensors"]
            if st.session_state.get("map_historical_only"):
                st.query_params["map_historical"] = "1"
            elif "map_historical" in st.query_params:
                del st.query_params["map_historical"]
            if st.session_state.get("map_hide_historical_only"):
                st.query_params["map_hide_historical"] = "1"
            elif "map_hide_historical" in st.query_params:
                del st.query_params["map_hide_historical"]
            if st.session_state.get("map_hide_manual"):
                st.query_params["map_hide_manual"] = "1"
            elif "map_hide_manual" in st.query_params:
                del st.query_params["map_hide_manual"]
        except Exception:
            pass

        # CSS del overlay: contenedor de altura 0 (no desplaza el layout) y
        # botón absoluto sobre la esquina superior derecha del mapa, justo
        # bajo los controles de zoom de MapLibre (38px + márgenes).
        overlay_bg = "rgba(13,17,24,0.98)" if dark else "rgba(255,255,255,0.98)"
        overlay_hover_bg = "rgba(33,40,54,0.98)" if dark else "rgba(236,239,243,0.98)"
        overlay_border = "rgba(255,255,255,0.14)" if dark else "rgba(15,18,25,0.14)"
        overlay_hover_border = "rgba(255,255,255,0.28)" if dark else "rgba(15,18,25,0.26)"
        overlay_shadow = "0 10px 24px rgba(0,0,0,0.28)" if dark else "0 10px 24px rgba(0,0,0,0.12)"
        overlay_icon_stroke = "%23f4f7fb" if dark else "%23242933"
        active_count = (
            len(selected_sensor_list)
            + (1 if st.session_state.get("map_historical_only") else 0)
            + (1 if st.session_state.get("map_hide_historical_only") else 0)
            + (1 if st.session_state.get("map_hide_manual") else 0)
        )
        badge_css = (
            f"""
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"]::after {{
                content: "{active_count}";
                position: absolute;
                top: -5px;
                right: -5px;
                min-width: 17px;
                height: 17px;
                padding: 0 4px;
                border-radius: 999px;
                background: #ff5a54;
                color: #fff;
                font: 700 10px/17px system-ui, -apple-system, "Segoe UI", sans-serif;
                text-align: center;
            }}
            """
            if active_count else ""
        )
        st.markdown(
            f"""
            <style>
            .st-key-map_sensor_overlay {{
                position: relative;
                height: 0;
                overflow: visible;
                z-index: 60;
            }}
            /* Colocación idéntica al control original: alineado con el
               grupo de zoom de MapLibre (margen 10px, 2 botones de 38px
               → termina a ~88px) y un hueco de ~14px por debajo. */
            .st-key-map_sensor_overlay [data-testid="stPopover"] {{
                position: absolute;
                right: 2px;
                top: 138px;
                width: auto;
            }}
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"] {{
                position: relative;
                width: 40px;
                height: 40px;
                min-height: 40px;
                padding: 0;
                border-radius: 12px;
                background: {overlay_bg} !important;
                border: 1px solid {overlay_border} !important;
                box-shadow: {overlay_shadow};
                justify-content: center;
                transition: background 0.12s ease, border-color 0.12s ease;
            }}
            /* Sombreado al pasar el ratón / pulsar, igual que los botones de
               zoom +/- de MapLibre. */
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"]:hover {{
                background: {overlay_hover_bg} !important;
                border-color: {overlay_hover_border} !important;
            }}
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"]:active {{
                background: {overlay_hover_bg} !important;
                border-color: {overlay_hover_border} !important;
            }}
            /* Oculta el texto del label y el chevron; el embudo se dibuja
               localmente para no depender del font Material en producción. */
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"] p,
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"] svg {{
                display: none;
            }}
            .st-key-map_sensor_overlay [data-testid="stPopoverButton"]::before {{
                content: "";
                display: block;
                width: 23px;
                height: 23px;
                background-repeat: no-repeat;
                background-position: center;
                background-size: 23px 23px;
                background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='{overlay_icon_stroke}' stroke-width='2.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 5h16l-6.4 7.2v5.3l-3.2 1.7v-7L4 5z'/%3E%3C/svg%3E");
            }}
            {badge_css}
            </style>
            """,
            unsafe_allow_html=True,
        )

        selected_sensors = {
            sensor_key
            for sensor_key in st.session_state.get("map_sensor_filter", [])
            if sensor_key in MAP_SENSOR_FILTER_OPTIONS
        }
        historical_only = bool(st.session_state.get("map_historical_only", False))
        hide_historical_only = bool(st.session_state.get("map_hide_historical_only", False))
        effective_provider_ids = sorted(provider_filter)

        nearest = []
        if effective_provider_ids and selected_countries:
            regional_batches = regional_provider_batches(
                effective_provider_ids,
                selected_countries,
            )
            for regional_country, regional_provider_ids in regional_batches:
                _extend_unique_candidates(
                    nearest,
                    _load_regional_candidates_batch(
                        regional_provider_ids,
                        [regional_country],
                        historical_only=historical_only,
                        hide_historical_only=hide_historical_only,
                    ),
                )

            iem_countries = [
                country for country in selected_countries
                if country_uses_iem_map_fallback(country)
            ]
            if iem_countries:
                _extend_unique_candidates(
                    nearest,
                    _load_iem_country_candidates(
                        iem_countries,
                        historical_only=historical_only,
                        hide_historical_only=hide_historical_only,
                    ),
                )

            allowed_result_providers = set(provider_filter)
            if any(country_uses_iem_map_fallback(country) for country in selected_countries):
                allowed_result_providers.add(IEM_FALLBACK_MAP_PROVIDER)
            nearest = [s for s in nearest if s["provider_id"] in allowed_result_providers]
            if historical_only:
                nearest = [s for s in nearest if bool(s.get("has_historical", False))]
            if hide_historical_only:
                nearest = [s for s in nearest if not bool(s.get("is_historical_only", False))]
            if hide_manual:
                nearest = [s for s in nearest if not bool(s.get("manual", False))]
            nearest = [
                station
                for station in nearest
                if station_matches_sensor_filter(station, selected_sensors)
            ]
            nearest.sort(key=lambda station: float(station["distance_km"]))
        visible_station_count = len(nearest)
        visible_provider_count = len({s["provider_id"] for s in nearest})

        metric_col1.metric(t("map.visible_stations"), visible_station_count)
        metric_col2.metric(t("map.providers"), visible_provider_count)
        if not nearest:
            if selected_countries:
                st.warning(t("map.no_stations"))
            map_view_state = _map_session_view_state(search_lat, search_lon, 7.0)
            map_style = (
                "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
                if dark else
                "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
            )
            frozen_view = _deck_frozen_view_state(
                map_view_state,
                ("empty", map_style, round(search_lat, 6), round(search_lon, 6)),
            )
            deck = pdk.Deck(
                map_style=map_style,
                initial_view_state=pdk.ViewState(
                    latitude=float(frozen_view["latitude"]),
                    longitude=float(frozen_view["longitude"]),
                    zoom=float(frozen_view["zoom"]),
                    pitch=0,
                ),
                layers=[
                    pdk.Layer(
                        "ScatterplotLayer",
                        id="center-layer",
                        data=[{"lat": search_lat, "lon": search_lon}],
                        pickable=False,
                        filled=True,
                        stroked=True,
                        get_position="[lon, lat]",
                        get_fill_color=[255, 255, 255, 230],
                        get_line_color=[25, 25, 25, 230],
                        get_radius=220,
                        radius_min_pixels=6,
                        radius_max_pixels=10,
                    )
                ],
            )
            try:
                _pydeck_chart_stretch(
                    deck,
                    key=f"map_chart_{theme_mode}",
                    height=900,
                )
            except Exception as map_err:
                st.warning(f"No se pudo renderizar el mapa ({map_err}).")
        else:
            point_radius = 70 if visible_station_count > 20000 else 95 if visible_station_count > 10000 else 120 if visible_station_count > 4000 else 140 if visible_station_count > 1800 else 160 if visible_station_count > 900 else 170
            points = [
                {
                    **station,
                    "distance_txt": f"{float(station['distance_km']):.1f} km",
                    "alt_txt": f"{float(station['elevation_m']):.0f} m",
                    "color": country_color(station.get("country")),
                    "radius": point_radius,
                }
                for station in nearest
            ]

            def _connect_station_from_map(selected_station: dict) -> bool:
                if not apply_station_selection(
                    selected_station,
                    connected=True,
                    pending_active_tab="observation",
                    clear_runtime_cache=True,
                ):
                    return False
                st.session_state["map_selected_station"] = dict(selected_station)
                return True

            def _set_provider_autoconnect_from_map(selected_station: dict) -> bool:
                return persist_provider_autoconnect_target(selected_station)

            zoom_reference = points[: min(len(points), 2000)]
            max_distance = max((p["distance_km"] for p in zoom_reference), default=250.0)
            map_view_state = _map_session_view_state(
                search_lat,
                search_lon,
                _zoom_for_max_distance(max_distance),
            )

            # Al deck solo van los campos que usan el render y el tooltip: el
            # dict completo (sensors, tz, locality, flags…) multiplica por ~4
            # el JSON serializado hacia el navegador, y con decenas de miles
            # de estaciones (p. ej. NWS) son varios MB por render. El clic
            # devuelve ``idx`` y la fila completa se resuelve en Python.
            points_for_layer = [
                {
                    "idx": index,
                    "lat": point["lat"],
                    "lon": point["lon"],
                    "color": point["color"],
                    "radius": point["radius"],
                    "name": point["name"],
                    "provider": point["provider"],
                    "station_id": point["station_id"],
                    "distance_txt": point["distance_txt"],
                    "alt_txt": point["alt_txt"],
                }
                for index, point in enumerate(points)
            ]

            map_style = (
                "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
                if dark else
                "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
            )
            map_tooltip_bg = "rgba(18, 18, 18, 0.92)" if dark else "rgba(255, 255, 255, 0.96)"
            map_tooltip_text = "rgba(255, 255, 255, 0.96)" if dark else "rgba(15, 18, 25, 0.96)"
            map_tooltip_border = "1px solid rgba(255,255,255,0.10)" if dark else "1px solid rgba(15,18,25,0.12)"
            map_tooltip_shadow = "0 10px 24px rgba(0,0,0,0.28)" if dark else "0 10px 24px rgba(0,0,0,0.12)"

            map_layers = [
                pdk.Layer(
                    "ScatterplotLayer",
                    id="stations-layer",
                    data=points_for_layer,
                    pickable=True,
                    auto_highlight=True,
                    filled=True,
                    stroked=True,
                    get_position="[lon, lat]",
                    get_fill_color="color",
                    get_line_color=[16, 20, 28, 140],
                    line_width_min_pixels=1,
                    get_radius="radius",
                    radius_min_pixels=4,
                    radius_max_pixels=24,
                ),
            ]
            map_layers.append(
                pdk.Layer(
                    "ScatterplotLayer",
                    id="center-layer",
                    data=[{"lat": search_lat, "lon": search_lon}],
                    pickable=False,
                    filled=True,
                    stroked=True,
                    get_position="[lon, lat]",
                    get_fill_color=[255, 255, 255, 230],
                    get_line_color=[25, 25, 25, 230],
                    get_radius=220,
                    radius_min_pixels=6,
                    radius_max_pixels=10,
                )
            )

            # Identidad del contenido del deck: si nada de esto cambia, el JSON
            # reenviado es idéntico y no hay repintado. El hash de las ids es
            # estable dentro del proceso (vive solo en session_state).
            stations_signature = (
                "stations",
                map_style,
                round(search_lat, 6),
                round(search_lon, 6),
                point_radius,
                hash(tuple((p["provider_id"], p.get("network", ""), p["station_id"]) for p in nearest)),
            )
            frozen_view = _deck_frozen_view_state(map_view_state, stations_signature)
            deck = pdk.Deck(
                map_style=map_style,
                initial_view_state=pdk.ViewState(
                    latitude=float(frozen_view["latitude"]),
                    longitude=float(frozen_view["longitude"]),
                    zoom=float(frozen_view["zoom"]),
                    pitch=0,
                ),
                layers=map_layers,
                tooltip={
                    "html": "<b>{name}</b><br/>{provider} · ID {station_id}<br/>Distancia: {distance_txt}<br/>Altitud: {alt_txt}",
                    "style": {
                        "backgroundColor": map_tooltip_bg,
                        "color": map_tooltip_text,
                        "fontSize": "12px",
                        "border": map_tooltip_border,
                        "borderRadius": "10px",
                        "boxShadow": map_tooltip_shadow,
                        "padding": "10px 12px",
                    },
                },
            )

            deck_event = None
            try:
                deck_event = _pydeck_chart_stretch(
                    deck,
                    key=f"map_chart_{theme_mode}",
                    height=900,
                )
            except Exception as map_err:
                st.warning(f"No se pudo renderizar el mapa ({map_err}). Mostrando tabla de estaciones.")
            st.markdown("<div style='height:0.35rem;'></div>", unsafe_allow_html=True)

            selected_station = st.session_state.get("map_selected_station")
            selection_state = {}
            try:
                if hasattr(deck_event, "get"):
                    selection_state = deck_event.get("selection", {}) or {}
                elif hasattr(deck_event, "selection"):
                    selection_state = getattr(deck_event, "selection", {}) or {}
            except Exception:
                selection_state = {}
            try:
                selected_objects = selection_state.get("objects", {}) if hasattr(selection_state, "get") else {}
            except Exception:
                selected_objects = {}
            if isinstance(selected_objects, dict):
                selected_in_layer = selected_objects.get("stations-layer", [])
                if isinstance(selected_in_layer, list) and selected_in_layer:
                    selected_station = selected_in_layer[0]
                    # El objeto del deck es la versión recortada; recupera la
                    # fila completa (sensors, tz, connectable…) por índice.
                    if isinstance(selected_station, dict):
                        selected_idx = selected_station.get("idx")
                        if isinstance(selected_idx, int) and 0 <= selected_idx < len(points):
                            selected_station = points[selected_idx]
                    st.session_state["map_selected_station"] = dict(selected_station)

            st.markdown(f"#### {t('map.selected_station')}")
            if isinstance(selected_station, dict):
                def _meta_chip(value: str) -> str:
                    return f"<span class='mlbx-map-chip'>{html.escape(str(value))}</span>"

                selected_name = str(selected_station.get("name", "Estación"))
                selected_provider = str(selected_station.get("provider", "Proveedor"))
                selected_provider_id = str(selected_station.get("provider_id") or selected_provider)
                selected_station_id = str(selected_station.get("station_id", "—"))
                selected_station_id_display = display_provider_station_id(selected_provider_id, selected_station_id)
                selected_locality = str(selected_station.get("locality", "—"))
                selected_alt = safe_float(selected_station.get("elevation_m"), default=None)
                selected_dist = safe_float(selected_station.get("distance_km"), default=None)
                selected_lat = safe_float(selected_station.get("lat"), default=None)
                selected_lon = safe_float(selected_station.get("lon"), default=None)
                selected_connectable = bool(selected_station.get("connectable", True))
                selected_type_txt = str(
                    t("map.station_type_manual")
                    if bool(selected_station.get("manual", False))
                    else t("map.station_type_automatic")
                )
                selected_alt_txt = "—" if selected_alt is None else f"{selected_alt:.0f} m"
                selected_dist_txt = "—" if selected_dist is None else f"{selected_dist:.1f} km"
                selected_coords_txt = (
                    "—"
                    if selected_lat is None or selected_lon is None
                    else f"{selected_lat:.4f}, {selected_lon:.4f}"
                )
                selected_sensor_labels, selected_sensor_metadata_available = station_sensor_labels(selected_station, t)
                selected_sensor_chips = " ".join(_meta_chip(label) for label in selected_sensor_labels)
                selected_sensor_meta_html = ""
                if selected_sensor_chips or selected_sensor_metadata_available:
                    selected_sensor_value = selected_sensor_chips if selected_sensor_chips else _meta_chip("—")
                    selected_sensor_meta_html = f"""
                        <div class="mlbx-map-meta" style="margin-top: 0.55rem;">
                            <span class="mlbx-map-meta-item">{html.escape(t('map.sensor_filter'))}: {selected_sensor_value}</span>
                        </div>
                    """

                info_col, action_col = st.columns([0.78, 0.22], gap="small")
                with info_col:
                    st.markdown(
                        html_clean(
                            f"""
                            <div style="color: var(--text); font-size: 1.05rem; font-weight: 700; margin-bottom: 0.3rem;">
                                {html.escape(selected_name)} · {html.escape(selected_provider)}
                            </div>
                            <div class="mlbx-map-meta">
                                <span class="mlbx-map-meta-item">ID: {_meta_chip(selected_station_id_display)}</span>
                                <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.locality'))}: {_meta_chip(selected_locality)}</span>
                                <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.altitude').replace(' (m)', ''))}: {_meta_chip(selected_alt_txt)}</span>
                                <span class="mlbx-map-meta-item">{html.escape(t('map.table_columns.distance').replace(' (km)', ''))}: {_meta_chip(selected_dist_txt)}</span>
                                <span class="mlbx-map-meta-item">Lat/Lon: {_meta_chip(selected_coords_txt)}</span>
                                <span class="mlbx-map-meta-item">{html.escape(t('map.station_type'))}: {_meta_chip(selected_type_txt)}</span>
                            </div>
                            {selected_sensor_meta_html}
                            """
                        ),
                        unsafe_allow_html=True,
                    )
                    saved_autoconnect = bool(get_stored_autoconnect())
                    saved_target = get_stored_autoconnect_target() or {}
                    is_target_station = bool(
                        saved_autoconnect
                        and str(saved_target.get("kind", "")).strip().upper() == "PROVIDER"
                        and str(saved_target.get("provider_id", "")).strip().upper() == str(selected_station.get("provider_id", "")).strip().upper()
                        and str(saved_target.get("station_id", "")).strip() == selected_station_id
                    )
                    map_toggle_key = f"map_autoconnect_toggle_{selected_provider}_{selected_station_id}"
                    if selected_connectable:
                        map_toggle_changed = _sync_map_autoconnect_toggle(
                            map_toggle_key,
                            is_target_station,
                        )
                        map_toggle_enabled = st.toggle(
                            t("map.autoconnect"),
                            key=map_toggle_key,
                            on_change=_handle_map_autoconnect_toggle_change,
                            args=(
                                map_toggle_key,
                                dict(selected_station),
                                selected_name,
                                is_target_station,
                                _set_provider_autoconnect_from_map,
                                disable_provider_autoconnect,
                                t,
                            ),
                        )
                        sync_payload = st.session_state.get(MAP_AUTOCONNECT_SYNC_RERUN_KEY)
                        if (
                            isinstance(sync_payload, dict)
                            and sync_payload.get("key") == map_toggle_key
                        ):
                            st.session_state.pop(MAP_AUTOCONNECT_SYNC_RERUN_KEY, None)
                            _clear_map_autoconnect_toggle_changed(map_toggle_key)
                            if sync_payload.get("action") == "enable":
                                _set_provider_autoconnect_from_map(selected_station)
                            elif sync_payload.get("action") == "disable":
                                disable_provider_autoconnect("map_autoconnect_toggle_")
                            st.rerun(scope="app")
                        if map_toggle_changed and map_toggle_enabled and not is_target_station:
                            st.session_state["auto_connect_wu_device"] = False
                            if _set_provider_autoconnect_from_map(selected_station):
                                _clear_map_autoconnect_toggle_changed(map_toggle_key)
                                st.success(t("map.autoconnect_saved", station=selected_name))
                                st.rerun(scope="app")
                            else:
                                _clear_map_autoconnect_toggle_changed(map_toggle_key)
                                st.error(t("map.autoconnect_save_error"))
                        elif map_toggle_changed and (not map_toggle_enabled) and is_target_station:
                            disable_provider_autoconnect("map_autoconnect_toggle_")
                            _clear_map_autoconnect_toggle_changed(map_toggle_key)
                            st.info(t("map.autoconnect_disabled"))
                            st.rerun(scope="app")
                        elif map_toggle_changed:
                            _clear_map_autoconnect_toggle_changed(map_toggle_key)
                    else:
                        st.caption("Inventario: estación visible en catálogo, sin conexión directa todavía.")
                    map_flash = st.session_state.pop("_map_provider_autoconnect_flash", "")
                    map_flash_kind = st.session_state.pop("_map_provider_autoconnect_flash_kind", "success")
                    if map_flash:
                        if map_flash_kind == "info":
                            st.info(map_flash)
                        elif map_flash_kind == "error":
                            st.error(map_flash)
                        else:
                            st.success(map_flash)
                with action_col:
                    favorite_key = f"map_favorite_btn_{selected_provider}_{selected_station_id}"
                    if str(selected_provider_id).strip().upper() != "WEATHERLINK":
                        if st.button(t("favorites.save"), key=favorite_key, width="stretch"):
                            favorite = favorite_from_provider_station(selected_station)
                            if favorite and upsert_favorite(favorite):
                                # NO flusheamos aquí: un flush efímero seguido de
                                # st.rerun() inmediato desmonta el iframe del bridge
                                # antes de que escriba en el navegador y vacía la cola,
                                # así que el favorito no se persiste (se perdía al
                                # recargar). Dejamos la escritura encolada; el bootstrap
                                # estable del sidebar (key fija) la entrega en el rerun
                                # siguiente, igual que las credenciales WU.
                                st.session_state["_map_favorite_flash"] = t("favorites.saved", station=selected_name)
                                st.rerun(scope="app")
                            else:
                                st.error(t("favorites.save_error"))
                    connect_key = f"map_connect_btn_{selected_provider}_{selected_station_id}"
                    if st.button(
                        t("sidebar.buttons.connect"),
                        key=connect_key,
                        type="primary",
                        width="stretch",
                        disabled=not selected_connectable,
                    ):
                        if _connect_station_from_map(selected_station):
                            st.success(t("map.connect_success", station=selected_name))
                            st.rerun(scope="app")
                        else:
                            st.error(t("map.connect_error"))
            else:
                st.caption(t("map.select_station_hint"))

    _map_results_area()

# ============================================================

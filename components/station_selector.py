"""
Componente genérico para seleccionar estación meteorológica cercana.
Independiente del proveedor (AEMET/WU/futuros).
"""

import unicodedata
import time
from typing import Optional, Tuple

import requests
import streamlit as st

from config import LS_AUTOCONNECT
from providers import search_nearby_stations
from utils.storage import (
    set_local_storage,
    set_stored_autoconnect_target,
    get_stored_autoconnect,
    get_stored_autoconnect_target,
)
from .browser_geolocation import get_browser_geolocation

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "MeteoLabX/1.0 (contact: meteolabx@gmail.com)"

CITY_COORDS_ES = {
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3851, 2.1734),
    "valencia": (39.4699, -0.3763),
    "sevilla": (37.3891, -5.9845),
    "zaragoza": (41.6488, -0.8891),
    "malaga": (36.7213, -4.4214),
    "málaga": (36.7213, -4.4214),
    "bilbao": (43.2630, -2.9350),
    "murcia": (37.9922, -1.1307),
    "palma": (39.5696, 2.6502),
    "las palmas": (28.1235, -15.4363),
    "valladolid": (41.6523, -4.7245),
    "albacete": (38.9943, -1.8585),
    "alicante": (38.3452, -0.4810),
    "almeria": (36.8340, -2.4637),
    "avila": (40.6564, -4.7003),
    "badajoz": (38.8786, -6.9707),
    "burgos": (42.3439, -3.6969),
    "caceres": (39.4765, -6.3722),
    "cadiz": (36.5271, -6.2886),
    "castellon": (39.9864, -0.0513),
    "castellon de la plana": (39.9864, -0.0513),
    "ciudad real": (38.9848, -3.9274),
    "cordoba": (37.8882, -4.7794),
    "a coruna": (43.3623, -8.4115),
    "la coruna": (43.3623, -8.4115),
    "cuenca": (40.0704, -2.1374),
    "girona": (41.9794, 2.8214),
    "granada": (37.1773, -3.5986),
    "guadalajara": (40.6333, -3.1667),
    "huelva": (37.2614, -6.9447),
    "huesca": (42.1361, -0.4089),
    "jaen": (37.7796, -3.7849),
    "leon": (42.5987, -5.5671),
    "lleida": (41.6176, 0.6200),
    "logrono": (42.4627, -2.4449),
    "lugo": (43.0121, -7.5558),
    "ourense": (42.3350, -7.8639),
    "oviedo": (43.3614, -5.8494),
    "palencia": (42.0095, -4.5284),
    "palma de mallorca": (39.5696, 2.6502),
    "las palmas de gran canaria": (28.1235, -15.4363),
    "pontevedra": (42.4338, -8.6480),
    "salamanca": (40.9701, -5.6635),
    "san sebastian": (43.3183, -1.9812),
    "santander": (43.4623, -3.8099),
    "segovia": (40.9429, -4.1088),
    "soria": (41.7660, -2.4790),
    "tarragona": (41.1189, 1.2445),
    "santa cruz de tenerife": (28.4636, -16.2518),
    "teruel": (40.3441, -1.1069),
    "toledo": (39.8628, -4.0273),
    "vitoria": (42.8467, -2.6726),
    "vitoria-gasteiz": (42.8467, -2.6726),
    "zamora": (41.5033, -5.7446),
    "ceuta": (35.8894, -5.3213),
    "melilla": (35.2923, -2.9381),
    "santiago de compostela": (42.8782, -8.5448),
    "merida": (38.9161, -6.3437),
    "pamplona": (42.8125, -1.6458),
}


def _in_lat_lon_range(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _normalize_coords_order(lat: float, lon: float):
    """Corrige lat/lon invertidas usando rango y distancia a estaciones reales."""
    lat = float(lat)
    lon = float(lon)

    if (lat < -90.0 or lat > 90.0) and (-90.0 <= lon <= 90.0) and (-180.0 <= lat <= 180.0):
        return lon, lat, True
    if (lon < -180.0 or lon > 180.0) and (-180.0 <= lat <= 180.0) and (-90.0 <= lon <= 90.0):
        return lon, lat, True
    if abs(lon) <= 90.0 and abs(lat) > 90.0 and abs(lat) <= 180.0:
        return lon, lat, True

    normal_ok = _in_lat_lon_range(lat, lon)
    swapped_ok = _in_lat_lon_range(lon, lat)

    if normal_ok and not swapped_ok:
        return lat, lon, False
    if swapped_ok and not normal_ok:
        return lon, lat, True
    if not normal_ok and not swapped_ok:
        return lat, lon, False

    best_normal = search_nearby_stations(lat, lon, max_results=1)
    d_normal = best_normal[0].distance_km if best_normal else float("inf")

    if d_normal > 500.0 and swapped_ok:
        best_swapped = search_nearby_stations(lon, lat, max_results=1)
        d_swapped = best_swapped[0].distance_km if best_swapped else float("inf")
        if d_swapped < (d_normal * 0.5):
            return lon, lat, True

    return lat, lon, False


def _default_search_coords():
    """Coordenadas por defecto para búsqueda."""
    lat = (
        st.session_state.get("search_lat")
        or st.session_state.get("provider_station_lat")
        or st.session_state.get("aemet_station_lat")
        or 41.39
    )
    lon = (
        st.session_state.get("search_lon")
        or st.session_state.get("provider_station_lon")
        or st.session_state.get("aemet_station_lon")
        or 2.17
    )
    return float(lat), float(lon)


def _coords_from_city(city_name: str):
    if not city_name:
        return None
    key = city_name.strip().lower()
    key = "".join(c for c in unicodedata.normalize("NFD", key) if unicodedata.category(c) != "Mn")
    return CITY_COORDS_ES.get(key)


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _nominatim_geocode_cached(query: str, accept_language: str = "es,en") -> dict:
    """Geocodifica texto con Nominatim y cachea resultados para reducir tráfico."""
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
    }
    if accept_language:
        params["accept-language"] = accept_language

    headers = {
        "User-Agent": NOMINATIM_USER_AGENT,
        "Accept": "application/json",
    }

    response = requests.get(
        NOMINATIM_SEARCH_URL,
        params=params,
        headers=headers,
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) == 0:
        return {}
    first = payload[0]
    return first if isinstance(first, dict) else {}


def _geocode_with_nominatim(query: str) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Devuelve (lat, lon) desde Nominatim para una consulta textual.
    Incluye limitación de 1 req/s según policy pública.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return None, "Consulta vacía"

    last_ts = float(st.session_state.get("nominatim_last_request_ts", 0.0))
    now_ts = time.monotonic()
    wait_s = 1.05 - (now_ts - last_ts)
    if wait_s > 0:
        time.sleep(wait_s)

    st.session_state["nominatim_last_request_ts"] = time.monotonic()

    try:
        result = _nominatim_geocode_cached(clean_query, accept_language="es,en")
    except requests.HTTPError as err:
        status = getattr(err.response, "status_code", None)
        if status == 429:
            return None, "Nominatim ha limitado temporalmente las peticiones (429)."
        return None, f"Error HTTP de Nominatim ({status})."
    except requests.RequestException:
        return None, "No se pudo conectar con Nominatim."
    except Exception:
        return None, "Error inesperado consultando Nominatim."

    if not result:
        return None, "Nominatim no encontró resultados para ese texto."

    try:
        lat = float(result.get("lat"))
        lon = float(result.get("lon"))
    except (TypeError, ValueError):
        return None, "Nominatim devolvió un resultado sin coordenadas válidas."

    display_name = str(result.get("display_name", "")).strip()
    if display_name:
        st.session_state["nominatim_last_match"] = display_name
    else:
        st.session_state["nominatim_last_match"] = ""
    return (lat, lon), ""


def _apply_selected_station(station):
    """Guarda estación seleccionada en session_state, con compatibilidad legacy."""
    st.session_state["connection_type"] = station.provider_id
    st.session_state["provider_station_id"] = station.station_id
    st.session_state["provider_station_name"] = station.name
    st.session_state["provider_station_lat"] = station.lat
    st.session_state["provider_station_lon"] = station.lon
    st.session_state["provider_station_alt"] = station.elevation_m

    if station.provider_id == "AEMET":
        st.session_state["aemet_station_id"] = station.station_id
        st.session_state["aemet_station_name"] = station.name
        st.session_state["aemet_station_lat"] = station.lat
        st.session_state["aemet_station_lon"] = station.lon
        st.session_state["aemet_station_alt"] = station.elevation_m
    elif station.provider_id == "METEOCAT":
        st.session_state["meteocat_station_id"] = station.station_id
        st.session_state["meteocat_station_name"] = station.name
        st.session_state["meteocat_station_lat"] = station.lat
        st.session_state["meteocat_station_lon"] = station.lon
        st.session_state["meteocat_station_alt"] = station.elevation_m
    elif station.provider_id == "EUSKALMET":
        st.session_state["euskalmet_station_id"] = station.station_id
        st.session_state["euskalmet_station_name"] = station.name
        st.session_state["euskalmet_station_lat"] = station.lat
        st.session_state["euskalmet_station_lon"] = station.lon
        st.session_state["euskalmet_station_alt"] = station.elevation_m
    elif station.provider_id == "METEOGALICIA":
        st.session_state["meteogalicia_station_id"] = station.station_id
        st.session_state["meteogalicia_station_name"] = station.name
        st.session_state["meteogalicia_station_lat"] = station.lat
        st.session_state["meteogalicia_station_lon"] = station.lon
        st.session_state["meteogalicia_station_alt"] = station.elevation_m
    elif station.provider_id == "NWS":
        st.session_state["nws_station_id"] = station.station_id
        st.session_state["nws_station_name"] = station.name
        st.session_state["nws_station_lat"] = station.lat
        st.session_state["nws_station_lon"] = station.lon
        st.session_state["nws_station_alt"] = station.elevation_m

    st.session_state["connected"] = True
    st.session_state["show_results"] = False

    for key in ("search_lat", "search_lon"):
        if key in st.session_state:
            del st.session_state[key]


def _set_provider_autoconnect(station):
    """Guarda en localStorage la estación de proveedor para auto-conexión."""
    provider_id = str(getattr(station, "provider_id", "") or "").strip().upper()
    station_id = str(getattr(station, "station_id", "") or "").strip()
    if not provider_id or not station_id:
        return False

    set_stored_autoconnect_target(
        {
            "kind": "PROVIDER",
            "provider_id": provider_id,
            "station_id": station_id,
            "station_name": str(getattr(station, "name", "") or station_id).strip(),
            "lat": float(getattr(station, "lat", 0.0)),
            "lon": float(getattr(station, "lon", 0.0)),
            "elevation_m": float(getattr(station, "elevation_m", 0.0)),
        }
    )
    set_local_storage(LS_AUTOCONNECT, "1", "save")
    # Evita autoconectar inmediatamente en esta misma sesión;
    # la auto-conexión se aplica al volver a entrar.
    st.session_state["_autoconnect_attempted"] = True
    return True


def _reset_autoconnect_toggle_state():
    """Limpia estado de toggles para resincronizar con la estación objetivo."""
    for state_key in list(st.session_state.keys()):
        if state_key.startswith("autoconnect_toggle_"):
            del st.session_state[state_key]


def render_station_selector():
    """Renderiza el selector de estaciones cercano, independiente del proveedor."""
    if st.session_state.get("connected"):
        return

    if "geo_request_id" not in st.session_state:
        st.session_state["geo_request_id"] = 0
    if "geo_pending" not in st.session_state:
        st.session_state["geo_pending"] = False
    if "geo_last_error" not in st.session_state:
        st.session_state["geo_last_error"] = ""
    if "geo_debug_msg" not in st.session_state:
        st.session_state["geo_debug_msg"] = ""
    if "nominatim_last_match" not in st.session_state:
        st.session_state["nominatim_last_match"] = ""
    if "nominatim_last_error" not in st.session_state:
        st.session_state["nominatim_last_error"] = ""

    browser_geo_result = None
    # Renderizar el componente custom solo cuando hay solicitud activa.
    # Evita huecos verticales cuando no se está pidiendo geolocalización.
    if st.session_state.get("geo_pending"):
        browser_geo_result = get_browser_geolocation(
            request_id=st.session_state["geo_request_id"],
            timeout_ms=12000,
            high_accuracy=True,
        )

    if st.session_state.get("geo_pending") and isinstance(browser_geo_result, dict):
        st.session_state["geo_pending"] = False
        if browser_geo_result.get("ok"):
            lat = browser_geo_result.get("lat")
            lon = browser_geo_result.get("lon")
            if lat is not None and lon is not None:
                lat, lon, swapped = _normalize_coords_order(lat, lon)
                st.session_state["search_lat"] = lat
                st.session_state["search_lon"] = lon
                st.session_state["show_results"] = True
                acc = browser_geo_result.get("accuracy_m")
                if isinstance(acc, (int, float)):
                    st.session_state["geo_debug_msg"] = f"Ubicación GPS obtenida (±{acc:.0f} m)."
                else:
                    st.session_state["geo_debug_msg"] = "Ubicación GPS obtenida."
                if swapped:
                    st.session_state["geo_debug_msg"] += " Se corrigieron coordenadas invertidas."
                st.session_state["geo_last_error"] = ""
                st.rerun()

        error_message = browser_geo_result.get("error_message") or "No se pudo obtener tu ubicación GPS."
        st.session_state["geo_last_error"] = str(error_message)
        st.session_state["geo_debug_msg"] = ""

    st.markdown("<div class='station-selector-gap'></div>", unsafe_allow_html=True)
    if st.button("📍 Buscar estaciones cerca de mí", type="primary", width="stretch"):
        st.session_state["geo_request_id"] += 1
        st.session_state["geo_pending"] = True
        st.session_state["geo_last_error"] = ""
        st.session_state["geo_debug_msg"] = "Solicitando ubicación GPS al navegador..."
        st.rerun()

    if st.session_state.get("geo_pending"):
        st.caption("Esperando respuesta de geolocalización del navegador...")

    geo_last_error = st.session_state.get("geo_last_error", "").strip()
    if geo_last_error:
        st.warning("No pude leer tu ubicación GPS del navegador. Revisa permisos de ubicación del sitio.")
        st.caption(f"Detalle navegador: {geo_last_error}")

    geo_debug_msg = st.session_state.get("geo_debug_msg", "")
    if geo_debug_msg:
        st.caption(geo_debug_msg)

    with st.expander("O buscar por ciudad/coordenadas", expanded=False):
        city_input = st.text_input(
            "Ciudad (opcional)",
            value=st.session_state.get("search_city", ""),
            placeholder="Ej: Madrid",
            help="Escribe un lugar para geocodificarlo con Nominatim u ajusta lat/lon manualmente",
        )
        st.caption("Búsqueda textual geocodificada por Nominatim (OpenStreetMap).")
        st.caption("Datos geográficos © OpenStreetMap contributors (ODbL).")

        base_lat, base_lon = _default_search_coords()
        city_coords = _coords_from_city(city_input)
        if city_coords is not None:
            base_lat, base_lon = city_coords

        lat_manual = st.number_input(
            "Latitud",
            min_value=-90.0,
            max_value=90.0,
            value=float(base_lat),
            step=0.01,
            help="Ejemplo: 40.42",
        )
        lon_manual = st.number_input(
            "Longitud",
            min_value=-180.0,
            max_value=180.0,
            value=float(base_lon),
            step=0.01,
            help="Ejemplo: -3.70",
        )

        if st.button("🔎 Buscar con estos datos", width="stretch"):
            st.session_state["search_city"] = city_input.strip()
            st.session_state["nominatim_last_error"] = ""

            chosen_lat = float(lat_manual)
            chosen_lon = float(lon_manual)
            city_query = city_input.strip()

            if city_query:
                coords, nom_err = _geocode_with_nominatim(city_query)
                if coords is not None:
                    chosen_lat, chosen_lon = coords
                else:
                    fallback = _coords_from_city(city_query)
                    if fallback is not None:
                        chosen_lat, chosen_lon = fallback
                        st.session_state["nominatim_last_match"] = ""
                        st.session_state["nominatim_last_error"] = (
                            f"{nom_err} Se usaron coordenadas locales de respaldo."
                        )
                    else:
                        st.session_state["nominatim_last_match"] = ""
                        st.session_state["nominatim_last_error"] = nom_err

            st.session_state["search_lat"] = float(chosen_lat)
            st.session_state["search_lon"] = float(chosen_lon)
            st.session_state["show_results"] = True
            st.rerun()

        nominatim_match = str(st.session_state.get("nominatim_last_match", "")).strip()
        nominatim_error = str(st.session_state.get("nominatim_last_error", "")).strip()
        if nominatim_match:
            st.caption(f"Resultado Nominatim: {nominatim_match}")
        if nominatim_error:
            st.warning(nominatim_error)

    if st.session_state.get("show_results"):
        st.markdown("---")
        st.markdown("### 🎯 Estaciones cercanas")

        lat, lon = _default_search_coords()
        nearest = search_nearby_stations(lat, lon, max_results=5)

        if not nearest:
            st.warning("⚠️ No se encontraron estaciones cercanas")
            return

        for station in nearest:
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 1])
                saved_autoconnect = bool(get_stored_autoconnect())
                saved_target = get_stored_autoconnect_target() or {}
                is_target_station = bool(
                    saved_autoconnect
                    and str(saved_target.get("kind", "")).strip().upper() == "PROVIDER"
                    and str(saved_target.get("provider_id", "")).strip().upper() == str(station.provider_id).strip().upper()
                    and str(saved_target.get("station_id", "")).strip() == str(station.station_id).strip()
                )

                with col1:
                    st.markdown(f"**{station.name}**")
                    st.caption(
                        f"{station.provider_name} | ID: {station.station_id} | Alt: {station.elevation_m:.0f}m"
                    )
                    toggle_key = f"autoconnect_toggle_{station.provider_id}_{station.station_id}"
                    if toggle_key not in st.session_state:
                        st.session_state[toggle_key] = is_target_station
                    toggle_enabled = st.checkbox("Conectar automáticamente al iniciar", key=toggle_key)
                    if toggle_enabled and not is_target_station:
                        if _set_provider_autoconnect(station):
                            _reset_autoconnect_toggle_state()
                            st.success(f"✅ Auto-conexión guardada para {station.name}")
                            st.rerun()
                        st.error("No se pudo guardar la auto-conexión")
                    elif (not toggle_enabled) and is_target_station:
                        set_local_storage(LS_AUTOCONNECT, "0", "save")
                        set_stored_autoconnect_target(None)
                        st.session_state["_autoconnect_attempted"] = True
                        _reset_autoconnect_toggle_state()
                        st.info("Auto-conexión desactivada en este dispositivo.")
                        st.rerun()

                with col2:
                    st.metric("Distancia", f"{station.distance_km:.1f} km")

                with col3:
                    if st.button(
                        "Conectar",
                        key=f"connect_{station.provider_id}_{station.station_id}",
                        width="stretch",
                    ):
                        _apply_selected_station(station)
                        st.success(f"✅ Conectado a {station.name} ({station.provider_name})")
                        st.rerun()

                st.markdown("---")


def show_provider_connection_status():
    """Muestra estado de conexión en sidebar para proveedores que lo requieren."""
    if not st.session_state.get("connected"):
        return

    provider_id = st.session_state.get("connection_type", "")
    if provider_id != "AEMET":
        return

    station_name = st.session_state.get("aemet_station_name")
    station_id = st.session_state.get("aemet_station_id")
    station_alt = st.session_state.get("aemet_station_alt")

    st.sidebar.markdown(f"### 📡 Conectado a {provider_id}")
    st.sidebar.markdown(f"**{station_name}**")
    st.sidebar.caption(f"ID: {station_id}")
    st.sidebar.caption(f"Alt: {station_alt}m")

    if st.sidebar.button("🔄 Actualizar", width="stretch", help="Forzar actualización de datos (bypass caché)"):
        st.cache_data.clear()
        st.rerun()

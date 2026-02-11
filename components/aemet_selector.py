"""
Componente para seleccionar estación meteorológica más cercana.
Mantiene compatibilidad con el flujo actual de AEMET.
"""
import streamlit as st
from providers import search_nearby_stations
import unicodedata
import requests

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
    # Capitales de provincia (y ciudades autónomas)
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
    "malaga": (36.7213, -4.4214),
    "murcia": (37.9922, -1.1307),
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
    "zaragoza": (41.6488, -0.8891),
    "ceuta": (35.8894, -5.3213),
    "melilla": (35.2923, -2.9381),
    # Capitales no provinciales (útiles para usuario final)
    "santiago de compostela": (42.8782, -8.5448),
    "merida": (38.9161, -6.3437),
    "pamplona": (42.8125, -1.6458),
}


def _browser_geolocation():
    """
    Intenta obtener geolocalización del navegador.
    Devuelve (lat, lon) o None si no hay permiso/no está disponible.
    """
    try:
        from streamlit_js_eval import get_geolocation
    except Exception:
        return None, "No se encontró 'streamlit-js-eval' en este entorno."

    try:
        geo = get_geolocation()
        if not isinstance(geo, dict):
            return None, "El navegador no devolvió datos de geolocalización."

        coords = geo.get("coords", geo)
        if not isinstance(coords, dict):
            err = geo.get("error") if isinstance(geo, dict) else None
            if err:
                return None, f"Error del navegador: {err}"
            return None, "No se pudieron leer coordenadas (permiso bloqueado o no disponible)."

        lat = coords.get("latitude", coords.get("lat"))
        lon = coords.get("longitude", coords.get("lng", coords.get("lon")))
        if lat is None or lon is None:
            return None, "Coordenadas incompletas; revisa permisos de ubicación del navegador."
        return (float(lat), float(lon)), None
    except Exception as e:
        return None, f"Excepción leyendo geolocalización: {type(e).__name__}"


@st.cache_data(ttl=1800)
def _ip_geolocation():
    """
    Fallback por IP (aproximado). No requiere permisos del navegador.
    Devuelve (lat, lon) o None.
    """
    providers = [
        "https://ipapi.co/json/",
        "https://ipwho.is/",
    ]
    for url in providers:
        try:
            response = requests.get(url, timeout=4)
            response.raise_for_status()
            data = response.json()
            lat = data.get("latitude", data.get("lat"))
            lon = data.get("longitude", data.get("lon", data.get("lng")))
            if lat is not None and lon is not None:
                return float(lat), float(lon)
        except Exception:
            continue
    return None


def _default_search_coords():
    """Obtiene coordenadas por defecto sin pedir input en la vista principal."""
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
    key = "".join(
        c for c in unicodedata.normalize("NFD", key)
        if unicodedata.category(c) != "Mn"
    )
    return CITY_COORDS_ES.get(key)


def render_aemet_selector():
    """
    Renderiza el selector de estación en la pantalla principal.
    Solo se muestra si NO hay conexión activa
    """
    # Solo mostrar si no está conectado
    if st.session_state.get("connected"):
        return

    # Botón principal (CTA) en rojo para búsqueda rápida
    st.markdown(
        """
        <style>
        div[data-testid="stButton"] > button[kind="primary"] {
            background: linear-gradient(135deg, #d62828, #b51717) !important;
            border: 1px solid #a41212 !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: linear-gradient(135deg, #e63946, #c1121f) !important;
            border: 1px solid #b10f1a !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if st.button("📍 Buscar estaciones cerca de mí", type="primary", use_container_width=True):
        geo_coords, geo_error = _browser_geolocation()
        if geo_coords is None:
            ip_coords = _ip_geolocation()
            if ip_coords is not None:
                lat, lon = ip_coords
                st.session_state["search_lat"] = lat
                st.session_state["search_lon"] = lon
                st.session_state["show_results"] = True
                st.session_state["geo_debug_msg"] = (
                    "Tu explorador no devolvió geolocalización. "
                    "Se está usando ubicación aproximada por IP."
                )
                st.rerun()

            st.session_state["geo_debug_msg"] = (
                "No se pudo obtener tu ubicación desde el navegador. "
                "Revisa los permisos y ajustes de localización del navegador/sistema. "
                + (geo_error or "")
            )
            st.warning("No pude leer tu ubicación. Usa ciudad o coordenadas.")
        else:
            lat, lon = geo_coords
            st.session_state["search_lat"] = lat
            st.session_state["search_lon"] = lon
            st.session_state["show_results"] = True
            st.session_state["geo_debug_msg"] = ""
            st.rerun()

    geo_debug_msg = st.session_state.get("geo_debug_msg", "")
    if geo_debug_msg:
        st.caption(geo_debug_msg)

    with st.expander("O buscar por ciudad/coordenadas", expanded=False):
        city_input = st.text_input(
            "Ciudad (opcional)",
            value=st.session_state.get("search_city", ""),
            placeholder="Ej: Madrid",
            help="Usa una ciudad conocida o escribe lat/lon manualmente",
        )

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

        if st.button("🔎 Buscar con estos datos", use_container_width=True):
            st.session_state["search_city"] = city_input.strip()
            st.session_state["search_lat"] = float(lat_manual)
            st.session_state["search_lon"] = float(lon_manual)
            st.session_state["show_results"] = True
            st.rerun()
    
    # Mostrar resultados si se ha buscado
    if st.session_state.get('show_results'):
        st.markdown("---")
        st.markdown("### 🎯 Estaciones cercanas")
        
        lat, lon = _default_search_coords()

        # Buscar en todos los proveedores habilitados
        nearest = search_nearby_stations(lat, lon, max_results=5)
        
        if not nearest:
            st.warning("⚠️ No se encontraron estaciones cercanas")
            return
        
        # Mostrar cada estación como una card
        for station in nearest:
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 1])
                
                with col1:
                    st.markdown(f"**{station.name}**")
                    st.caption(
                        f"{station.provider_name} | ID: {station.station_id} | Alt: {station.elevation_m:.0f}m"
                    )
                
                with col2:
                    st.metric("Distancia", f"{station.distance_km:.1f} km")
                
                with col3:
                    if st.button(
                        "Conectar",
                        key=f"connect_{station.provider_id}_{station.station_id}",
                        use_container_width=True
                    ):
                        # Guardar conexión en claves genéricas
                        st.session_state['connection_type'] = station.provider_id
                        st.session_state['provider_station_id'] = station.station_id
                        st.session_state['provider_station_name'] = station.name
                        st.session_state['provider_station_lat'] = station.lat
                        st.session_state['provider_station_lon'] = station.lon
                        st.session_state['provider_station_alt'] = station.elevation_m

                        # Compatibilidad con pipeline actual (AEMET)
                        if station.provider_id == "AEMET":
                            st.session_state['aemet_station_id'] = station.station_id
                            st.session_state['aemet_station_name'] = station.name
                            st.session_state['aemet_station_lat'] = station.lat
                            st.session_state['aemet_station_lon'] = station.lon
                            st.session_state['aemet_station_alt'] = station.elevation_m

                        st.session_state['connected'] = True
                        st.session_state['show_results'] = False
                        
                        # Limpiar búsqueda
                        if 'search_lat' in st.session_state:
                            del st.session_state['search_lat']
                        if 'search_lon' in st.session_state:
                            del st.session_state['search_lon']
                        
                        st.success(f"✅ Conectado a {station.name} ({station.provider_name})")
                        st.rerun()
                
                st.markdown("---")


def show_aemet_connection_status():
    """
    Muestra el estado de conexión en la sidebar.
    """
    if not st.session_state.get("connected"):
        return

    provider_id = st.session_state.get("connection_type", "")
    # Este bloque extra solo aplica a AEMET (evitar ruido visual en WU)
    if provider_id != "AEMET":
        return

    station_name = st.session_state.get('aemet_station_name')
    station_id = st.session_state.get('aemet_station_id')
    station_alt = st.session_state.get('aemet_station_alt')

    st.sidebar.markdown(f"### 📡 Conectado a {provider_id}")
    st.sidebar.markdown(f"**{station_name}**")
    st.sidebar.caption(f"ID: {station_id}")
    st.sidebar.caption(f"Alt: {station_alt}m")
    
    if st.sidebar.button("🔄 Actualizar", use_container_width=True, help="Forzar actualización de datos (bypass caché)"):
        # Limpiar caché de AEMET
        st.cache_data.clear()
        st.rerun()

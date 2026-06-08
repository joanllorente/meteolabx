import streamlit as st
from typing import Optional
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
from utils.storage import flush_local_storage_writes


ALL_MAP_PROVIDER_OPTIONS = ["AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE", "METEOGALICIA", "NWS", "POEM", "METOFFICE", "METEOHUB_IT"]
MAP_AUTOCONNECT_CHANGED_KEY = "_map_provider_autoconnect_toggle_changed"
MAP_AUTOCONNECT_SYNC_RERUN_KEY = "_map_provider_autoconnect_sync_rerun"
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


def _mark_map_autoconnect_toggle_changed(toggle_key: str) -> None:
    st.session_state[MAP_AUTOCONNECT_CHANGED_KEY] = toggle_key


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


PROVIDER_COLORS = {
    "AEMET": [255, 75, 75],
    "METEOCAT": [58, 145, 255],
    "EUSKALMET": [55, 198, 124],
    "FROST": [78, 180, 218],
    "METEOFRANCE": [74, 124, 255],
    "METEOGALICIA": [255, 184, 64],
    "NWS": [178, 122, 255],
    "POEM": [14, 188, 212],
    "METOFFICE": [36, 168, 142],
    "METEOHUB_IT": [235, 112, 40],
}


def _map_cache_key(provider_id: str, lat: float, lon: float, catalog_version=()) -> tuple[str, float, float, tuple]:
    return (
        coerce_str(provider_id, upper=True),
        round(float(lat), 4),
        round(float(lon), 4),
        tuple(catalog_version or ()),
    )


def render_map_tab(ctx):
    section_title = ctx["section_title"]
    t = ctx["t"]
    dark = ctx["dark"]
    theme_mode = ctx["theme_mode"]
    math = ctx["math"]
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
            "station_id": candidate.station_id,
            "distance_km": float(haversine_distance(search_lat, search_lon, candidate.lat, candidate.lon)),
            "locality": resolve_provider_locality(candidate.provider_id, metadata, candidate.name),
            "elevation_m": float(candidate.elevation_m),
            "station_tz": str(metadata.get("tz", "")).strip(),
        }

    def _extend_unique_candidates(target: list[dict], candidates: list[dict]) -> None:
        seen = {(item["provider_id"], item["station_id"]) for item in target}
        for candidate in candidates:
            key = (candidate["provider_id"], candidate["station_id"])
            if key in seen:
                continue
            target.append(candidate)
            seen.add(key)

    def _load_regional_candidates(provider_id: str) -> list[dict]:
        spec = regional_catalog_spec(provider_id)
        if spec is None:
            return []
        cache_store = st.session_state.setdefault("map_regional_rows_cache", {})
        provider_ids = (provider_id,)
        catalog_version = _map_catalog_cache_version(provider_ids)
        cache_key = _map_cache_key(provider_id, search_lat, search_lon, catalog_version)
        cached_rows = cache_store.get(cache_key)
        if isinstance(cached_rows, list):
            return [dict(row) for row in cached_rows]
        regional_candidates = _cached_map_search_nearby_stations(
            float(spec["lat"]),
            float(spec["lon"]),
            int(spec["max_results"]),
            provider_ids,
            catalog_version,
        )
        rows = [_candidate_to_map_row(candidate) for candidate in regional_candidates]
        rows.sort(key=lambda row: float(row["distance_km"]))
        cache_store[cache_key] = [dict(row) for row in rows]
        return rows

    ensure_geo_state("map_geo", request_id_start=10000)

    default_lat, default_lon = _map_default_coords()
    if "map_search_lat" not in st.session_state or safe_float(st.session_state.get("map_search_lat")) is None:
        st.session_state["map_search_lat"] = default_lat
    if "map_search_lon" not in st.session_state or safe_float(st.session_state.get("map_search_lon")) is None:
        st.session_state["map_search_lon"] = default_lon
    if "map_provider_filter_near" not in st.session_state:
        st.session_state["map_provider_filter_near"] = []
    if "map_provider_filter_far" not in st.session_state:
        st.session_state["map_provider_filter_far"] = []

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
    all_provider_options = list(ALL_MAP_PROVIDER_OPTIONS)
    near_provider_options, far_provider_options = split_map_provider_options(search_lat, search_lon, all_provider_options)

    selected_near_state = [
        provider_id
        for provider_id in st.session_state.get("map_provider_filter_near", [])
        if provider_id in near_provider_options
    ]
    if selected_near_state != st.session_state.get("map_provider_filter_near", []):
        st.session_state["map_provider_filter_near"] = selected_near_state
    if not selected_near_state:
        st.session_state["map_provider_filter_near"] = list(near_provider_options)

    selected_far_state = [
        provider_id
        for provider_id in st.session_state.get("map_provider_filter_far", [])
        if provider_id in far_provider_options
    ]
    if selected_far_state != st.session_state.get("map_provider_filter_far", []):
        st.session_state["map_provider_filter_far"] = selected_far_state

    controls_col, filters_col = st.columns([1.1, 1], gap="large")
    with controls_col:
        st.markdown(f"#### {t('map.location_title')}")
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

    with filters_col:
        st.markdown(f"#### {t('map.filters_title')}")
        st.multiselect(
            t("map.nearby_providers"),
            options=near_provider_options,
            key="map_provider_filter_near",
        )
        if far_provider_options:
            st.multiselect(
                t("map.far_providers"),
                options=far_provider_options,
                key="map_provider_filter_far",
            )
        st.caption(t("map.filters_caption"))

    selected_near = set(st.session_state.get("map_provider_filter_near", []))
    selected_far = set(st.session_state.get("map_provider_filter_far", []))
    provider_filter = selected_near.union(selected_far)
    effective_provider_ids = sorted(provider_filter)

    nearest = []
    if effective_provider_ids:
        for provider_id in sorted(selected_near):
            _extend_unique_candidates(nearest, _load_regional_candidates(provider_id))

        regional_far_provider_ids = sorted(
            provider_id for provider_id in selected_far if regional_catalog_spec(provider_id) is not None
        )
        for provider_id in regional_far_provider_ids:
            _extend_unique_candidates(nearest, _load_regional_candidates(provider_id))

        nearest = [s for s in nearest if s["provider_id"] in provider_filter]
        nearest.sort(key=lambda station: float(station["distance_km"]))
    visible_station_count = len(nearest)
    visible_provider_count = len({s["provider_id"] for s in nearest})

    with controls_col:
        metric_col1, metric_col2 = st.columns(2)
        metric_col1.metric(t("map.visible_stations"), visible_station_count)
        metric_col2.metric(t("map.providers"), visible_provider_count)
        if selected_far:
            st.caption("Los proveedores cercanos al centro se cargan automáticamente. Los lejanos añadidos manualmente cargan su catálogo regional completo cuando está disponible.")

    if not nearest:
        st.warning(t("map.no_stations"))
    else:
        point_radius = 70 if visible_station_count > 20000 else 95 if visible_station_count > 10000 else 120 if visible_station_count > 4000 else 140 if visible_station_count > 1800 else 160 if visible_station_count > 900 else 170
        points = [
            {
                **station,
                "distance_txt": f"{float(station['distance_km']):.1f} km",
                "alt_txt": f"{float(station['elevation_m']):.0f} m",
                "color": PROVIDER_COLORS.get(station["provider_id"], [180, 180, 180]),
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

        points_for_layer = list(points)

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

        deck = pdk.Deck(
            map_style=map_style,
            initial_view_state=pdk.ViewState(
                latitude=search_lat,
                longitude=search_lon,
                zoom=_zoom_for_max_distance(max_distance),
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
                key=f"map_stations_chart_{theme_mode}",
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
            selected_alt_txt = "—" if selected_alt is None else f"{selected_alt:.0f} m"
            selected_dist_txt = "—" if selected_dist is None else f"{selected_dist:.1f} km"
            selected_coords_txt = (
                "—"
                if selected_lat is None or selected_lon is None
                else f"{selected_lat:.4f}, {selected_lon:.4f}"
            )

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
                        </div>
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
                    st.rerun()
                if map_toggle_changed and map_toggle_enabled and not is_target_station:
                    st.session_state["auto_connect_wu_device"] = False
                    if _set_provider_autoconnect_from_map(selected_station):
                        _clear_map_autoconnect_toggle_changed(map_toggle_key)
                        st.success(t("map.autoconnect_saved", station=selected_name))
                        st.rerun()
                    else:
                        _clear_map_autoconnect_toggle_changed(map_toggle_key)
                        st.error(t("map.autoconnect_save_error"))
                elif map_toggle_changed and (not map_toggle_enabled) and is_target_station:
                    disable_provider_autoconnect("map_autoconnect_toggle_")
                    _clear_map_autoconnect_toggle_changed(map_toggle_key)
                    st.info(t("map.autoconnect_disabled"))
                    st.rerun()
                elif map_toggle_changed:
                    _clear_map_autoconnect_toggle_changed(map_toggle_key)
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
                            flush_local_storage_writes(f"mlx_favorite_map_{selected_provider}")
                            st.session_state["_map_favorite_flash"] = t("favorites.saved", station=selected_name)
                            st.rerun()
                        else:
                            st.error(t("favorites.save_error"))
                connect_key = f"map_connect_btn_{selected_provider}_{selected_station_id}"
                if st.button(t("sidebar.buttons.connect"), key=connect_key, type="primary", width="stretch"):
                    if _connect_station_from_map(selected_station):
                        st.success(t("map.connect_success", station=selected_name))
                        st.rerun()
                    else:
                        st.error(t("map.connect_error"))
        else:
            st.caption(t("map.select_station_hint"))

# ============================================================

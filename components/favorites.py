"""
Barra horizontal de estaciones favoritas.
"""

from __future__ import annotations

import hashlib
import html
from typing import Any

import streamlit as st

from utils.favorites import favorite_key, get_stored_favorites, remove_favorite
from utils.provider_state import (
    apply_provider_station_state,
    apply_weatherlink_station_state,
    apply_wu_station_state,
    current_connection_type,
    get_provider_station_id,
)


def _favorite_meta_text(t, favorite: dict[str, Any]) -> str:
    provider_id = str(favorite.get("provider_id") or "").strip().upper()
    return str(favorite.get("provider_label") or provider_id).strip() or provider_id


def _connect_favorite(favorite: dict[str, Any]) -> bool:
    provider_id = str(favorite.get("provider_id") or "").strip().upper()
    station_id = str(favorite.get("station_id") or "").strip()
    if provider_id == "WU":
        return apply_wu_station_state(
            station_id,
            str(favorite.get("api_key") or ""),
            str(favorite.get("z") or ""),
            connected=True,
        )
    if provider_id == "WEATHERLINK":
        # Reconstruimos el ``station`` dict que espera el helper. El
        # favorito guarda station_id, station_id_uuid (opcional), name,
        # lat/lon y elevation; eso es suficiente para que
        # ``get_weatherlink_data`` haga el primer fetch sin tener que
        # volver a llamar al endpoint ``/stations`` (más rápido + ahorra
        # una request).
        station = {
            "station_id": station_id,
            "station_id_uuid": str(favorite.get("station_id_uuid") or ""),
            "station_name": str(favorite.get("station_name") or station_id),
        }
        for key in ("lat", "lon"):
            value = favorite.get(key)
            if value is not None:
                station[key] = value
        if favorite.get("elevation_m") is not None:
            station["elevation"] = favorite["elevation_m"]
        # Pasamos ``stations=[station]`` para que el helper poblace
        # ``weatherlink_stations`` en session_state. Sin esto,
        # ``find_weatherlink_station`` en el primer fetch no encuentra
        # la estación y se cae al fallback (que también funciona, pero
        # esto es más limpio y permite que el dropdown de estaciones
        # arranque ya con la actual marcada).
        return apply_weatherlink_station_state(
            station,
            str(favorite.get("api_key") or ""),
            str(favorite.get("api_secret") or ""),
            str(favorite.get("z") or ""),
            stations=[station],
            connected=True,
        )
    return apply_provider_station_state(
        provider_id,
        station_id,
        str(favorite.get("station_name") or station_id),
        favorite.get("lat"),
        favorite.get("lon"),
        favorite.get("elevation_m", 0),
        station_tz=str(favorite.get("station_tz") or ""),
        connected=True,
        show_results=False,
        pending_active_tab="observation",
        clear_runtime_cache=True,
    )


def _current_favorite_key(state: Any = None) -> str:
    state = state if state is not None else st.session_state
    try:
        if not state.get("connected", False):
            return ""
    except Exception:
        return ""

    provider_id = current_connection_type(state)
    if not provider_id:
        return ""
    # Resolución del station_id según proveedor. WeatherLink usa una
    # clave de session_state propia porque no pasa por
    # ``PROVIDER_STATION_ID`` legacy.
    if provider_id == "WEATHERLINK":
        station_id = str(state.get("weatherlink_station_id") or "").strip()
    else:
        station_id = get_provider_station_id(state, provider_id)
    if not station_id:
        return ""
    return favorite_key({"provider_id": provider_id, "station_id": station_id})


def render_favorites_bar(*, t, dark: bool) -> None:
    favorites = get_stored_favorites()
    if len(favorites) < 2:
        return

    card_bg = "rgba(255,255,255,0.58)" if not dark else "rgba(22,25,31,0.72)"
    card_border = "rgba(18,18,18,0.16)" if not dark else "rgba(255,255,255,0.16)"
    card_shadow = "0 8px 18px rgba(18,18,18,0.05)" if not dark else "0 12px 24px rgba(0,0,0,0.22)"
    active_border = "rgba(37, 99, 235, 0.54)" if not dark else "rgba(125, 211, 252, 0.66)"
    active_bg = "rgba(37, 99, 235, 0.055)" if not dark else "rgba(80, 150, 230, 0.12)"
    active_ring = "rgba(37, 99, 235, 0.24)" if not dark else "rgba(125, 211, 252, 0.28)"
    active_shadow = f"inset 0 0 0 1px {active_ring}, 0 8px 18px rgba(18,18,18,0.05)" if not dark else f"inset 0 0 0 1px {active_ring}, 0 12px 24px rgba(0,0,0,0.22)"
    expander_border = "rgba(18,18,18,0.11)" if not dark else "rgba(255,255,255,0.12)"
    expander_bg = "rgba(255,255,255,0.28)" if not dark else "rgba(22,25,31,0.34)"
    expander_summary_bg = "rgba(255,255,255,0.30)" if not dark else "rgba(17,22,30,0.36)"
    expander_divider = "rgba(18,18,18,0.08)" if not dark else "rgba(255,255,255,0.08)"
    remove_color = "rgba(75,82,96,0.78)" if not dark else "rgba(210,218,232,0.70)"
    remove_color_hover = "rgba(20,24,32,0.95)" if not dark else "rgba(255,255,255,0.94)"
    st.markdown(
        f"""
        <style>
        .mlbx-favorites-title {{
          margin: 0.32rem 0 0.35rem 0;
          color: var(--muted);
          font-size: 0.86rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0;
        }}
        .mlbx-favorite-scroll-anchor {{
          display: none;
        }}
        .mlbx-favorite-column-anchor {{
          display: none;
        }}
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) {{
          background: {expander_bg} !important;
          border: 1px solid {expander_border} !important;
          border-radius: 8px !important;
          box-shadow: none !important;
        }}
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) details,
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) > div {{
          background: transparent !important;
        }}
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) summary {{
          min-height: 2.12rem !important;
          padding: 0.34rem 0.66rem !important;
          background: {expander_summary_bg} !important;
          border-radius: 7px !important;
          border-bottom: 1px solid {expander_divider} !important;
        }}
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) summary p,
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) summary span {{
          color: var(--muted) !important;
          font-size: 0.92rem !important;
          font-weight: 650 !important;
        }}
        div[data-testid="stExpander"]:has(.mlbx-favorites-title) summary svg {{
          opacity: 0.62 !important;
          transform: scale(0.88) !important;
        }}
        .mlbx-favorite-body {{
          box-sizing: border-box;
          min-height: 1.75rem;
          padding-top: 0;
          padding-right: 1.45rem;
        }}
        .mlbx-favorite-name {{
          color: var(--text);
          font-size: 0.95rem;
          font-weight: 750;
          line-height: 1.22;
          margin-bottom: 0.18rem;
          overflow-wrap: anywhere;
        }}
        .mlbx-favorite-meta {{
          color: var(--muted);
          font-size: 0.78rem;
          font-weight: 600;
          line-height: 1.22;
          overflow-wrap: anywhere;
        }}
        div[class*="st-key-mlbx_favorite_card_"] {{
          position: relative !important;
        }}
        div[class*="st-key-mlbx_favorite_card_"] div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mlbx-favorite-scroll-anchor) {{
          position: relative !important;
          background: {card_bg} !important;
          border: 1px solid {card_border} !important;
          border-radius: 8px !important;
          box-shadow: {card_shadow} !important;
        }}
        div[class*="st-key-mlbx_favorite_card_active_"] div[data-testid="stVerticalBlockBorderWrapper"] {{
          background: {active_bg} !important;
          border: 1px solid {active_border} !important;
          box-shadow: {active_shadow} !important;
        }}
        div[class*="st-key-mlbx_favorite_card_active_"] div[data-testid="stVerticalBlockBorderWrapper"]::before {{
          content: "" !important;
          position: absolute !important;
          top: 0 !important;
          bottom: 0 !important;
          left: 0 !important;
          width: 0.18rem !important;
          border-radius: 8px 0 0 8px !important;
          background: {active_border} !important;
          pointer-events: none !important;
          z-index: 2 !important;
        }}
        div[class*="st-key-mlbx_favorite_card_active_"] .mlbx-favorite-name {{
          color: rgb(43, 91, 179) !important;
        }}
        div[class*="st-key-mlbx_favorite_card_"] div[data-testid="stVerticalBlockBorderWrapper"] > div,
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mlbx-favorite-scroll-anchor) > div {{
          border: 0 !important;
          padding: 0.48rem 0.6rem 0.48rem 0.6rem !important;
        }}
        div[class*="st-key-mlbx_favorite_card_"] [data-testid="stVerticalBlock"],
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mlbx-favorite-scroll-anchor) [data-testid="stVerticalBlock"] {{
          gap: 0.22rem !important;
        }}
        div[data-testid="stHorizontalBlock"]:has(> div .mlbx-favorite-column-anchor) {{
          overflow-x: auto;
          overflow-y: hidden;
          flex-wrap: nowrap;
          padding-bottom: 0.35rem;
          scrollbar-width: thin;
        }}
        div[data-testid="stHorizontalBlock"]:has(> div .mlbx-favorite-column-anchor) > div {{
          min-width: 17rem;
          width: 17rem !important;
          flex: 0 0 17rem !important;
        }}
        div[data-testid="stHorizontalBlock"]:has(> div .mlbx-favorite-column-anchor) > div button {{
          white-space: nowrap;
        }}
        div[class*="st-key-mlbx_favorite_card_"] div[data-testid="stButton"] > button {{
          min-height: 2.12rem !important;
          height: 2.12rem !important;
          padding: 0 0.65rem !important;
        }}
        div[class*="st-key-mlbx_favorite_remove_"] {{
          position: absolute !important;
          top: 0.36rem !important;
          right: 0.42rem !important;
          display: block !important;
          width: 1rem !important;
          min-width: 1rem !important;
          height: 1rem !important;
          z-index: 3 !important;
          padding: 0 !important;
          margin: 0 !important;
        }}
        div[class*="st-key-mlbx_favorite_remove_"] [data-testid="stVerticalBlock"] {{
          gap: 0 !important;
        }}
        div[class*="st-key-mlbx_favorite_remove_"] div[data-testid="stButton"] {{
          width: 1rem !important;
          height: 1rem !important;
          margin: 0 !important;
        }}
        div[class*="st-key-mlbx_favorite_remove_"] div[data-testid="stButton"] > button {{
          width: 1rem !important;
          height: 1rem !important;
          min-height: 1rem !important;
          padding: 0 !important;
          border-radius: 999px !important;
          background: transparent !important;
          border: 0 !important;
          box-shadow: none !important;
          color: {remove_color} !important;
          font-size: 0.9rem !important;
          font-weight: 600 !important;
          line-height: 1 !important;
        }}
        div[class*="st-key-mlbx_favorite_remove_"] div[data-testid="stButton"] > button:hover {{
          background: transparent !important;
          color: {remove_color_hover} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    active_key = _current_favorite_key()

    with st.expander(t("favorites.title"), expanded=False):
        st.markdown("<span class='mlbx-favorites-title'></span>", unsafe_allow_html=True)
        columns = st.columns(max(len(favorites), 1), gap="small")
        for idx, favorite in enumerate(favorites):
            key = favorite_key(favorite)
            key_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
            is_active = key == active_key
            with columns[idx]:
                st.markdown("<span class='mlbx-favorite-column-anchor'></span>", unsafe_allow_html=True)
                card_key = (
                    f"mlbx_favorite_card_active_{key_hash}"
                    if is_active
                    else f"mlbx_favorite_card_{key_hash}"
                )
                with st.container(border=True, key=card_key):
                    st.markdown("<span class='mlbx-favorite-scroll-anchor'></span>", unsafe_allow_html=True)
                    st.markdown(
                        f"""
                        <div class="mlbx-favorite-body">
                          <div class="mlbx-favorite-name">{html.escape(str(favorite.get('station_name') or favorite.get('station_id') or ''))}</div>
                          <div class="mlbx-favorite-meta">{html.escape(_favorite_meta_text(t, favorite))}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    with st.container(key=f"mlbx_favorite_remove_{key_hash}"):
                        if st.button(
                            "×",
                            key=f"favorite_remove_{key_hash}",
                            help=t("favorites.remove"),
                            type="tertiary",
                            width="content",
                        ):
                            if remove_favorite(favorite):
                                st.rerun()
                            else:
                                st.error(t("favorites.remove_error"))
                    if st.button(t("favorites.connect"), key=f"favorite_connect_{key_hash}", width="stretch"):
                        if _connect_favorite(favorite):
                            st.rerun()
                        else:
                            st.error(t("favorites.connect_error"))

"""
Renderizado de cabecera y estado de conexión principal.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from utils import html_clean


def render_app_header(*, t, dark: bool, header_refresh_label: str, total_station_count: int) -> None:
    st.markdown(
        html_clean(f"""
        <div class="header">
          <h1>MeteoLabx <span style="opacity:0.6; font-size:0.7em;">Release Candidate</span></h1>
          <div class="meta">
            {t("header.beta_notice")} ·
            {t("header.theme_label")}: {t(f"sidebar.theme.options.{'dark' if dark else 'light'}")} ·
            {t("header.refresh_label")}: {header_refresh_label}
          </div>
        </div>
        <div class="header-sub station-count">{t("header.available_stations", count=total_station_count)}</div>
        """),
        unsafe_allow_html=True,
    )


def render_connection_banner(
    *,
    t,
    dark: bool,
    snapshot,
    is_nan: Callable[[Any], bool],
    disconnect_callback: Callable[[], None],
) -> bool:
    if snapshot is None:
        return False

    def _fmt_num(value, ndigits=2):
        try:
            v = float(value)
            if is_nan(v):
                return "—"
            return f"{v:.{ndigits}f}"
        except Exception:
            return "—"

    alt_txt = _fmt_num(snapshot.elevation_m, ndigits=0)
    lat_txt = _fmt_num(snapshot.lat, ndigits=4)
    lon_txt = _fmt_num(snapshot.lon, ndigits=4)

    badge_bg = "rgba(56, 92, 132, 0.35)" if dark else "rgba(51, 126, 215, 0.12)"
    badge_border = "rgba(92, 158, 230, 0.45)" if dark else "rgba(51, 126, 215, 0.28)"
    badge_text = "rgba(142, 201, 255, 0.96)" if dark else "rgba(34, 93, 170, 0.96)"

    station_col, action_col = st.columns([0.84, 0.16], gap="small")
    with station_col:
        st.markdown(
            html_clean(
                f"""
                <div style="
                    margin: 0.2rem 0 0.75rem 0;
                    display: inline-block;
                    padding: 0.52rem 0.82rem;
                    border-radius: 14px;
                    border: 1px solid {badge_border};
                    background: {badge_bg};
                    color: {badge_text};
                    font-size: 0.88rem;
                    font-weight: 600;
                    line-height: 1.45;
                ">
                    <div>📡 {snapshot.provider_id} · <b>{snapshot.station_name}</b></div>
                    <div style="font-weight:500; opacity:0.92;">{t('header.station_id_short')}: {snapshot.station_id} · {t('header.altitude_short')}: {alt_txt} m · {t('header.latitude_short')}: {lat_txt} · {t('header.longitude_short')}: {lon_txt}</div>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )
    with action_col:
        st.markdown("<div style='height:0.28rem;'></div>", unsafe_allow_html=True)
        if st.button(t("sidebar.buttons.disconnect"), key="disconnect_header_btn", width="stretch"):
            disconnect_callback()
    return True

"""
Sincronización del contexto del navegador y overlays de conexión.
"""

from __future__ import annotations

import html
import time
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from utils import html_clean
from utils.state_keys import BROWSER_COLOR_SCHEME, BROWSER_TZ, BROWSER_VIEWPORT_WIDTH


def sync_browser_context_early() -> None:
    components.html(
        """
        <script>
        (function () {
          const appWin = window;
          const hostWin = window.parent || window;
          try {
            const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
            const vw = Math.round(hostWin.innerWidth || appWin.innerWidth || 0);
            const mediaWin = (hostWin && typeof hostWin.matchMedia === 'function') ? hostWin : appWin;
            const cs = mediaWin.matchMedia && mediaWin.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            const url = new URL(hostWin.location.href);
            const tzChanged = url.searchParams.get("_tz") !== tz;
            const csChanged = url.searchParams.get("_cs") !== cs;
            const vwChanged = url.searchParams.get("_vw") !== String(vw);
            const missingBootstrapParams = !url.searchParams.get("_tz") || !url.searchParams.get("_cs") || !url.searchParams.get("_vw");
            if (tzChanged || csChanged || vwChanged) {
              url.searchParams.set("_tz", tz);
              url.searchParams.set("_vw", String(vw));
              url.searchParams.set("_cs", cs);
              if (missingBootstrapParams) {
                hostWin.location.replace(url.toString());
              } else if (hostWin.history && typeof hostWin.history.replaceState === 'function') {
                hostWin.history.replaceState(null, "", url.toString());
              }
            } else if (vwChanged && hostWin.history && typeof hostWin.history.replaceState === 'function') {
              url.searchParams.set("_vw", String(vw));
              hostWin.history.replaceState(null, "", url.toString());
            }
          } catch (_e) {}
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def hydrate_browser_context_live(get_browser_context) -> None:
    value = get_browser_context(
        listen_changes=True,
        listen_viewport_changes=False,
        key="browser_context_sync",
    )
    if not isinstance(value, dict):
        return

    tz_name = str(value.get("tz", "")).strip()
    if tz_name and st.session_state.get(BROWSER_TZ) != tz_name:
        st.session_state[BROWSER_TZ] = tz_name

    color_scheme = str(value.get("cs", "")).strip().lower()
    if color_scheme in ("dark", "light") and st.session_state.get(BROWSER_COLOR_SCHEME) != color_scheme:
        st.session_state[BROWSER_COLOR_SCHEME] = color_scheme

    try:
        viewport_width = int(value.get("vw", 0) or 0)
    except Exception:
        viewport_width = 0
    if viewport_width and st.session_state.get(BROWSER_VIEWPORT_WIDTH) != viewport_width:
        st.session_state[BROWSER_VIEWPORT_WIDTH] = viewport_width


def render_connection_loading_overlay(payload: Optional[dict], *, title_text: str, dark: bool = True) -> None:
    info = payload if isinstance(payload, dict) else {}
    provider = html.escape(str(info.get("provider", "Estación") or "Estación"))
    station_name = html.escape(str(info.get("station_name", "") or info.get("station_id", "") or "").strip())
    subtitle = f"{provider} · {station_name}" if station_name else provider
    safe_subtitle = html.escape(subtitle)
    safe_title = html.escape(str(title_text or "Conectando estación…"))
    host_id = f"mlx-connection-loading-{int(time.time() * 1000)}"
    if dark:
        overlay_bg = "rgba(9, 13, 20, 0.42)"
        card_bg = "linear-gradient(180deg, rgba(22, 29, 42, 0.96), rgba(10, 14, 22, 0.96))"
        card_border = "rgba(139, 190, 255, 0.22)"
        card_shadow = "0 20px 60px rgba(0, 0, 0, 0.28)"
        card_text = "rgba(245, 248, 255, 0.98)"
        spinner_track = "rgba(140, 180, 255, 0.16)"
        spinner_active = "rgba(140, 180, 255, 0.92)"
        dot_bg = "rgba(140, 180, 255, 0.95)"
    else:
        overlay_bg = "rgba(244, 248, 255, 0.62)"
        card_bg = "linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(242, 246, 252, 0.98))"
        card_border = "rgba(51, 126, 215, 0.20)"
        card_shadow = "0 20px 55px rgba(44, 70, 112, 0.16)"
        card_text = "rgba(15, 18, 25, 0.96)"
        spinner_track = "rgba(51, 126, 215, 0.14)"
        spinner_active = "rgba(51, 126, 215, 0.88)"
        dot_bg = "rgba(51, 126, 215, 0.90)"
    st.markdown(
        html_clean(
            f"""
            <style>
              #{host_id} {{
                position: fixed;
                inset: 0;
                z-index: 9998;
                display: flex;
                align-items: center;
                justify-content: center;
                background: {overlay_bg};
                backdrop-filter: blur(3px);
                pointer-events: none;
                animation: mlbxConnectionFadeIn .18s ease-out;
              }}
              #{host_id} .mlbx-connection-card {{
                min-width: min(340px, calc(100vw - 40px));
                max-width: min(420px, calc(100vw - 40px));
                padding: 22px 22px 18px;
                border-radius: 22px;
                border: 1px solid {card_border};
                background: {card_bg};
                box-shadow: {card_shadow};
                color: {card_text};
                text-align: center;
              }}
              #{host_id} .mlbx-connection-spinner {{
                width: 48px;
                height: 48px;
                margin: 0 auto 14px;
                border-radius: 999px;
                border: 3px solid {spinner_track};
                border-top-color: {spinner_active};
                animation: mlbxConnectionSpin .8s linear infinite;
              }}
              #{host_id} .mlbx-connection-title {{
                font-size: 1.08rem;
                font-weight: 800;
                letter-spacing: 0.01em;
              }}
              #{host_id} .mlbx-connection-subtitle {{
                margin-top: 0.32rem;
                font-size: 0.9rem;
                opacity: 0.82;
              }}
              #{host_id} .mlbx-connection-dots {{
                display: inline-flex;
                gap: 6px;
                margin-top: 12px;
              }}
              #{host_id} .mlbx-connection-dots span {{
                width: 7px;
                height: 7px;
                border-radius: 999px;
                background: {dot_bg};
                animation: mlbxConnectionPulse 1.05s ease-in-out infinite;
              }}
              #{host_id} .mlbx-connection-dots span:nth-child(2) {{ animation-delay: .15s; }}
              #{host_id} .mlbx-connection-dots span:nth-child(3) {{ animation-delay: .30s; }}
              @keyframes mlbxConnectionSpin {{
                from {{ transform: rotate(0deg); }}
                to {{ transform: rotate(360deg); }}
              }}
              @keyframes mlbxConnectionPulse {{
                0%, 80%, 100% {{ transform: scale(0.7); opacity: 0.45; }}
                40% {{ transform: scale(1.0); opacity: 1; }}
              }}
              @keyframes mlbxConnectionFadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
              }}
            </style>
            <div id="{host_id}" class="mlbx-connection-overlay">
              <div class="mlbx-connection-card">
                <div class="mlbx-connection-spinner"></div>
                <div class="mlbx-connection-title">{safe_title}</div>
                <div class="mlbx-connection-subtitle">{safe_subtitle}</div>
                <div class="mlbx-connection-dots"><span></span><span></span><span></span></div>
              </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def clear_connection_loading_overlay() -> None:
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const docs = [];
          try { if (document) docs.push(document); } catch (_e) {}
          try {
            if (host.document && host.document !== document) docs.push(host.document);
          } catch (_e) {}
          docs.forEach(function (doc) {
            try {
              Array.from(doc.querySelectorAll('.mlbx-connection-overlay')).forEach(function (overlay) {
                if (!overlay) return;
                overlay.style.opacity = "0";
                overlay.style.visibility = "hidden";
                overlay.style.pointerEvents = "none";
                overlay.style.display = "none";
              });
            } catch (_hideErr) {}
          });
        })();
        </script>
        """,
        height=0,
        width=0,
    )

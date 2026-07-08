"""
Panel INTERNO de estadísticas de uso (solo administración).

Se abre introduciendo en el formulario de conexión WU el id especial
``Statics_admin`` y la contraseña de administración en el campo API key
(``METEOLABX_STATS_ADMIN_PASSWORD`` en el backend). No es una página
pública: no tiene i18n ni enlaces desde la UI.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

# Id especial que dispara el panel desde el formulario WU (case-insensitive).
STATS_ADMIN_STATION_ID = "statics_admin"
SESSION_OPEN_KEY = "internal_stats_open"
SESSION_PASSWORD_KEY = "internal_stats_password"


def maybe_intercept_wu_connect(station_id: str, api_key: str) -> bool:
    """Si las credenciales WU son las del panel interno, lo abre en vez de
    conectar. Devuelve True si ha interceptado (el caller no debe conectar)."""
    if str(station_id or "").strip().lower() != STATS_ADMIN_STATION_ID:
        return False
    st.session_state[SESSION_OPEN_KEY] = True
    st.session_state[SESSION_PASSWORD_KEY] = str(api_key or "").strip()
    return True


def _fmt_epoch(epoch: int) -> str:
    if not epoch:
        return "—"
    try:
        return datetime.fromtimestamp(int(epoch)).astimezone().strftime("%d %b %H:%M")
    except Exception:
        return "—"


def render_internal_stats() -> None:
    """Página del panel. El caller hace ``st.stop()`` después: el panel
    sustituye a las pestañas normales mientras está abierto."""
    from utils.api_client import BackendApiError, fetch_usage_stats_via_api

    st.markdown("## 📊 Estadísticas internas")
    if st.button("✕ Cerrar panel", key="internal_stats_close"):
        st.session_state[SESSION_OPEN_KEY] = False
        st.session_state.pop(SESSION_PASSWORD_KEY, None)
        st.rerun()

    password = str(st.session_state.get(SESSION_PASSWORD_KEY, "") or "")
    try:
        data = fetch_usage_stats_via_api(password)
    except BackendApiError as exc:
        if exc.kind == "unauthorized":
            st.error("Contraseña incorrecta.")
        elif exc.status_code == 404:
            st.error("Panel deshabilitado (METEOLABX_STATS_ADMIN_PASSWORD vacía).")
        else:
            st.error(f"No se pudieron cargar las estadísticas ({exc.kind}).")
        st.stop()
        return

    totals = data.get("totals", {})
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Hoy (24 h)", totals.get("d1", 0))
    c2.metric("7 días", totals.get("d7", 0))
    c3.metric("30 días", totals.get("d30", 0))
    c4.metric("Total", totals.get("total", 0))
    c5.metric("Estaciones distintas", totals.get("stations", 0))

    stations = data.get("stations", [])
    if not stations:
        st.info(
            "Sin visitas registradas todavía. Se registra una visita cada vez "
            "que alguien se conecta a una estación (selector, mapa, ranking, "
            "deep link o autoconexión)."
        )
        return

    st.caption(
        "Conexiones por estación. Ordenable pulsando en las cabeceras; "
        "por defecto, por total descendente."
    )
    rows = [
        {
            "Estación": s.get("name") or s.get("station_id"),
            "Proveedor": s.get("provider", ""),
            "ID": s.get("station_id", ""),
            "Hoy (24 h)": s.get("d1", 0),
            "7 días": s.get("d7", 0),
            "30 días": s.get("d30", 0),
            "Total": s.get("total", 0),
            "Última visita": _fmt_epoch(s.get("last_epoch", 0)),
        }
        for s in stations
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

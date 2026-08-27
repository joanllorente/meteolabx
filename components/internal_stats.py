"""
Panel INTERNO de estadísticas de uso (solo administración).

Se abre introduciendo en el formulario de conexión WU el id especial
``Statics_admin`` y la contraseña de administración en el campo API key
(``METEOLABX_STATS_ADMIN_PASSWORD`` en el backend). No es una página
pública: no tiene i18n ni enlaces desde la UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st

# Id especial que dispara el panel desde el formulario WU (case-insensitive).
STATS_ADMIN_STATION_ID = "statics_admin"
SESSION_OPEN_KEY = "internal_stats_open"
SESSION_PASSWORD_KEY = "internal_stats_password"

SECTION_LABELS = {
    "observation": "Observación",
    "trends": "Tendencias",
    "historical": "Histórico",
    "map.stations": "Mapa · Estaciones",
    "map.temperature": "Mapa · Temperatura",
    "map.wind": "Mapa · Viento",
    "map.precipitation": "Mapa · Precipitación",
    "forecast.streamlit": "Predicción · Desde Streamlit",
    "forecast.direct": "Predicción · Enlace directo",
    "ranking": "Ranking",
}


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


def _datetime_from_epoch(epoch: int) -> Optional[datetime]:
    """Timestamp local para que Streamlit ordene por fecha, no por texto."""
    if not epoch:
        return None
    try:
        # Streamlit ordena correctamente datetime; se elimina tzinfo después
        # de convertir a la zona local para conservar el formato mostrado.
        return datetime.fromtimestamp(int(epoch)).astimezone().replace(tzinfo=None)
    except Exception:
        return None


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
    error_totals = totals.get("errors", {}) or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Hoy (24 h)", totals.get("d1", 0))
    c2.metric("7 días", totals.get("d7", 0))
    c3.metric("30 días", totals.get("d30", 0))
    c4.metric("Total", totals.get("total", 0))
    c5.metric("Estaciones distintas", totals.get("stations", 0))
    c6.metric("Errores (30 d)", error_totals.get("d30", 0))

    sources = totals.get("sources", {}) or {}
    app_source = sources.get("app", {}) or {}
    seo_source = sources.get("seo", {}) or {}
    legacy_source = sources.get("legacy", {}) or {}
    panel_clicks = totals.get("panel_clicks", {}) or {}
    st.markdown("### 🔎 Origen de las conexiones")
    st.caption(
        "La ficha SEO cuenta cuando carga los datos de una estación. "
        "Abrir panel completo se registra aparte y no duplica la conexión."
    )
    origin_1, origin_2, origin_3, origin_4 = st.columns(4)
    origin_1.metric("Web MeteoLabX · 30 días", app_source.get("d30", 0))
    origin_1.caption(f"{app_source.get('total', 0)} desde el inicio")
    origin_2.metric("Fichas SEO · 30 días", seo_source.get("d30", 0))
    origin_2.caption(f"{seo_source.get('total', 0)} desde el inicio")
    origin_3.metric("Panel completo desde SEO · 30 días", panel_clicks.get("d30", 0))
    origin_3.caption(f"{panel_clicks.get('total', 0)} desde el inicio")
    origin_4.metric("Anteriores sin origen · 30 días", legacy_source.get("d30", 0))
    origin_4.caption(f"{legacy_source.get('total', 0)} desde el inicio")

    sections = data.get("sections", [])
    if sections:
        st.markdown("### 🧭 Uso de pestañas y mapas")
        st.caption(
            "Entradas reales a cada sección; los refrescos internos no cuentan. "
            "Observación, Tendencias e Histórico solo se registran con una "
            "estación conectada. Predicción distingue el acceso desde Streamlit "
            "de la apertura directa del enlace. La apertura automática inicial "
            "de Ranking se omite."
        )
        st.dataframe(
            [
                {
                    "Sección": SECTION_LABELS.get(
                        row.get("section", ""), row.get("section", "")
                    ),
                    "Hoy (24 h)": row.get("d1", 0),
                    "7 días": row.get("d7", 0),
                    "30 días": row.get("d30", 0),
                    "Total": row.get("total", 0),
                    "Última entrada": _datetime_from_epoch(row.get("last_epoch", 0)),
                }
                for row in sections
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Última entrada": st.column_config.DatetimeColumn(
                    "Última entrada", format="DD MMM HH:mm"
                ),
            },
        )

    stations = data.get("stations", [])
    if not stations:
        st.info(
            "Sin visitas registradas todavía. Se registra una visita cada vez "
            "que alguien se conecta a una estación (selector, mapa, ranking, "
            "deep link o autoconexión), y un error cada vez que una conexión "
            "falla."
        )
        return

    st.caption(
        "Conexiones y errores por estación. Ordenable pulsando en las "
        "cabeceras; por defecto, por total de conexiones descendente."
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
            "Web 30 d": s.get("app_d30", 0),
            "Web total": s.get("app_total", 0),
            "Ficha SEO 30 d": s.get("seo_d30", 0),
            "Ficha SEO total": s.get("seo_total", 0),
            "Sin origen 30 d": s.get("legacy_d30", 0),
            "Sin origen total": s.get("legacy_total", 0),
            "Panel completo 30 d": (s.get("panel_clicks") or {}).get("d30", 0),
            "Panel completo total": (s.get("panel_clicks") or {}).get("total", 0),
            "Última visita": _datetime_from_epoch(s.get("last_epoch", 0)),
            "Err 30 d": (s.get("errors") or {}).get("d30", 0),
            "Err total": (s.get("errors") or {}).get("total", 0),
            "Último error": (
                f"{(s.get('errors') or {}).get('last_kind', '')} · "
                f"{_fmt_epoch((s.get('errors') or {}).get('last_epoch', 0))}"
                if (s.get("errors") or {}).get("total", 0)
                else "—"
            ),
        }
        for s in stations
    ]
    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Última visita": st.column_config.DatetimeColumn(
                "Última visita", format="DD MMM HH:mm"
            ),
        },
    )

    error_kinds = data.get("error_kinds", [])
    if error_kinds:
        st.markdown("### ⚠️ Errores de conexión por tipo")
        st.caption(
            "Categorías de error registradas al fallar una conexión "
            "(timeout, unauthorized, network, notfound…)."
        )
        st.dataframe(
            [
                {
                    "Tipo": k.get("kind", ""),
                    "30 días": k.get("d30", 0),
                    "Total": k.get("total", 0),
                }
                for k in error_kinds
            ],
            use_container_width=True,
            hide_index=True,
        )

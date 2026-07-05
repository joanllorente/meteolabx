"""
Pestaña Ranking.

Top-10 de estaciones del día por Tmáx / Tmín / viento (ráfaga) / lluvia.
Dos secciones:

1. **País del usuario**: el proveedor nacional según la ubicación que ya
   maneja el mapa (``map_search_lat/lon``, geolocalización del navegador o
   centro por defecto). Sin permisos extra.
2. **Global**: combinado de todas las estaciones disponibles de todos los
   proveedores con datos en el backend.

Los datos vienen del endpoint ``GET /v1/ranking`` (el backend los mantiene
agregados con un job horario; aquí solo se pintan). Cacheado en frontend
~2 min (el dato real solo cambia cada hora; TTL corto para que cambios y
reinicios se reflejen pronto).
"""

from __future__ import annotations

import html as _html
import urllib.parse as _urllib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import streamlit as st

from tabs.map import (
    is_france_map_center,
    is_iberia_map_center,
    is_italy_map_center,
    is_norway_map_center,
)
from utils.api_client import (
    BackendApiError,
    fetch_country_by_tz_via_api,
    fetch_ranking_countries_via_api,
    fetch_ranking_via_api,
)
from utils.i18n import get_language

RANKING_METRICS = ["tmax", "tmin", "gust", "rain"]


def _safe_float(value: Any, default: float) -> float:
    try:
        f = float(value)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _country_from_center(lat: float, lon: float) -> Optional[str]:
    # El orden importa: Iberia y Francia pueden solapar en los Pirineos;
    # se prioriza Iberia (proveedor AEMET cubre toda España).
    if is_iberia_map_center(lat, lon):
        return "ES"
    if is_france_map_center(lat, lon):
        return "FR"
    if is_italy_map_center(lat, lon):
        return "IT"
    if is_norway_map_center(lat, lon):
        return "NO"
    return None


@st.cache_data(ttl=120, show_spinner=False)
def _cached_ranking(
    providers: Optional[str], limit: int, day: Optional[str] = None, exclude: Optional[str] = None
) -> Dict[str, Any]:
    try:
        return fetch_ranking_via_api(providers=providers, day=day, exclude=exclude, limit=limit)
    except BackendApiError:
        return {}


@st.cache_data(ttl=120, show_spinner=False)
def _cached_country_ranking(country: Optional[str], limit: int, day: Optional[str] = None) -> Dict[str, Any]:
    """Ranking del país ``country`` (ISO2). Incluye IEM + proveedores
    nacionales (el backend filtra por país de la estación)."""
    if not country:
        return {}
    try:
        return fetch_ranking_via_api(country=country, day=day, limit=limit)
    except BackendApiError:
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def _cached_ranking_countries() -> List[str]:
    return fetch_ranking_countries_via_api()


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_country_from_tz(tz: str) -> Optional[str]:
    return fetch_country_by_tz_via_api(tz)


def _browser_tz() -> str:
    """Zona horaria IANA del navegador (ya capturada por la app)."""
    tz = str(st.session_state.get("browser_tz") or "").strip()
    if tz:
        return tz
    raw = st.query_params.get("_tz", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").strip()


def _user_today_iso() -> str:
    """Fecha de HOY en el huso del navegador del usuario (fallback: huso del
    proceso, que en local coincide con el del usuario)."""
    tz_name = _browser_tz()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).date().isoformat()
        except Exception:
            pass
    return datetime.now().astimezone().date().isoformat()


def _resolve_user_country(lat: float, lon: float) -> Optional[str]:
    """País del usuario: heurística precisa de bounding-box (ES/FR/IT/NO) y, si
    no, aproximación por la zona horaria del navegador (cobertura mundial)."""
    precise = _country_from_center(lat, lon)
    if precise:
        return precise
    tz = _browser_tz()
    return _cached_country_from_tz(tz) if tz else None


def _country_name(code: str) -> str:
    """Nombre del país localizado al idioma de la UI (vía babel); cae al ISO2."""
    iso = str(code or "").strip().upper()
    if not iso:
        return ""
    try:
        from babel import Locale

        lang = (get_language() or "es").split("-")[0]
        name = Locale(lang).territories.get(iso)
        if name:
            return str(name)
    except Exception:
        pass
    return iso


def _country_options(available: List[str], detected: Optional[str]) -> List[str]:
    """Lista de ISO2 para el selector: países con datos + el detectado,
    ordenados por nombre localizado."""
    codes = {c for c in available if c and c != "UN"}
    if detected:
        codes.add(detected)
    return sorted(codes, key=lambda c: _country_name(c).casefold())


def _fmt_value(metric: str, value: float) -> str:
    return f"{float(value):.1f}"


def _updated_caption(data: Dict[str, Any], t) -> Optional[str]:
    """Texto 'última actualización' a partir del ``updated_at`` (ISO UTC) del
    backend, mostrado en la hora local DEL NAVEGADOR con los minutos
    transcurridos. ``astimezone()`` a secas usaría el huso del proceso, que en
    producción (Railway) es UTC → mostraba la hora UTC a todos los usuarios."""
    iso = data.get("updated_at") if isinstance(data, dict) else None
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()  # fallback: huso del proceso (dev local)
    tz_name = _browser_tz()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            local = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    mins = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 60))
    return t("ranking.updated", time=local.strftime("%H:%M"), mins=mins)


def _format_day_label(iso_day: str) -> str:
    """ISO ``YYYY-MM-DD`` → 'domingo 28 jun' localizado (la fecha es universal)."""
    try:
        from datetime import date as _date

        from babel.dates import format_date

        lang = (get_language() or "es").split("-")[0]
        return format_date(_date.fromisoformat(str(iso_day)), "EEEE d MMM", locale=lang)
    except Exception:
        return str(iso_day or "")


def _render_day_nav(data: Dict[str, Any], state_key: str) -> None:
    """Selector de día ◀ fecha ▶ + reloj UTC. Las fechas vienen del backend
    (``days``); la mostrada es ``day``. Cambia ``state_key`` en session_state."""
    days = data.get("days", []) if isinstance(data, dict) else []
    current = str(data.get("day", "")) if isinstance(data, dict) else ""
    if not days or current not in days:
        return
    idx = days.index(current)
    prev, label, nxt = st.columns([0.12, 0.76, 0.12], vertical_alignment="center")
    with prev:
        if st.button("◀", key=f"{state_key}_prev", disabled=idx <= 0, use_container_width=True):
            st.session_state[state_key] = days[idx - 1]
            st.rerun()
    with label:
        utc_now = datetime.now(timezone.utc).strftime("%H:%M")
        st.markdown(
            f"<div style='text-align:center;line-height:1.15;'>"
            f"<b>{_html.escape(_format_day_label(current))}</b>"
            f"<br><span style='opacity:0.55;font-size:0.72rem;'>{utc_now} UTC · "
            f"{idx + 1}/{len(days)}</span></div>",
            unsafe_allow_html=True,
        )
    with nxt:
        if st.button("▶", key=f"{state_key}_next", disabled=idx >= len(days) - 1, use_container_width=True):
            st.session_state[state_key] = days[idx + 1]
            st.rerun()


def _render_metric_block(
    metric: str,
    entries: List[dict],
    unit: str,
    t,
    *,
    show_provider: bool,
    dark: bool,
    show_country: bool = False,
) -> None:
    title = t(f"ranking.metrics.{metric}")
    st.markdown(f"**{title}**")
    if not entries:
        st.caption("—")
        return
    rows = []
    for e in entries:
        name = _html.escape(str(e.get("name", "")))
        locality = _html.escape(str(e.get("locality", "")))
        provider = _html.escape(str(e.get("provider", "")))
        rank = int(e.get("rank", 0))
        value = _fmt_value(metric, e.get("value", 0.0))
        # En el global mostramos el PAÍS (el proveedor "IEM" no dice de dónde
        # es la estación); en la sección de país, el proveedor.
        if show_country:
            sub = _html.escape(_country_name(str(e.get("country", ""))))
        else:
            sub = locality
            if show_provider and provider:
                sub = f"{locality} · {provider}" if locality else provider
        # Hora local de la estación (su huso), útil al mezclar zonas en el global.
        local_time = _html.escape(str(e.get("local_time", "")).strip())
        if local_time:
            sub = f"{sub} · 🕐 {local_time}" if sub else f"🕐 {local_time}"
        sub_html = f"<div class='mlbx-rank-sub'>{sub}</div>" if sub else ""
        # Nombre clicable → conecta a la estación (pasa proveedor+id por query
        # param; el handler de la pestaña lo recoge y llama a apply_station_selection).
        rc = (
            _urllib.quote(str(e.get("provider", "")), safe="")
            + "~"
            + _urllib.quote(str(e.get("station_id", "")), safe="")
        )
        name_html = (
            f"<a class='mlbx-rank-name mlbx-rank-link' "
            f"href='?rank_connect={rc}' target='_self' title='Conectar a {name}'>{name}</a>"
        )
        rows.append(
            f"<li><span class='mlbx-rank-pos'>{rank}</span>"
            f"<span class='mlbx-rank-body'>{name_html}{sub_html}</span>"
            f"<span class='mlbx-rank-val'>{value}<span class='mlbx-rank-unit'>{_html.escape(unit)}</span></span></li>"
        )
    st.markdown(
        f"<ol class='mlbx-rank-list'>{''.join(rows)}</ol>",
        unsafe_allow_html=True,
    )


def _render_section(
    data: Dict[str, Any],
    t,
    *,
    show_provider: bool,
    dark: bool,
    empty_msg: str,
    show_country: bool = False,
) -> None:
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(metrics, dict) or not any(metrics.get(m) for m in RANKING_METRICS):
        st.caption(empty_msg)
        return
    units = data.get("units", {}) if isinstance(data.get("units"), dict) else {}
    cols = st.columns(4, gap="medium")
    for col, metric in zip(cols, RANKING_METRICS):
        with col:
            _render_metric_block(
                metric,
                metrics.get(metric, []) or [],
                str(units.get(metric, "")),
                t,
                show_provider=show_provider,
                dark=dark,
                show_country=show_country,
            )


_RANKING_CSS = """
<style>
.mlbx-rank-list { list-style: none; margin: 0.2rem 0 0; padding: 0; }
.mlbx-rank-list li {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 2px; border-bottom: 1px solid var(--mlbx-rank-border);
}
.mlbx-rank-pos {
    flex: 0 0 22px; text-align: center; font-weight: 700; font-size: 0.8rem;
    color: var(--text); opacity: 0.55;
}
.mlbx-rank-body { flex: 1 1 auto; min-width: 0; }
.mlbx-rank-name {
    display: block; font-weight: 600; font-size: 0.9rem; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mlbx-rank-link { color: var(--text); text-decoration: none; cursor: pointer; }
.mlbx-rank-link:hover { text-decoration: underline; color: #ff5a54; }
.mlbx-rank-sub { font-size: 0.72rem; opacity: 0.6; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mlbx-rank-val { flex: 0 0 auto; font-weight: 700; font-size: 0.95rem; color: var(--text); }
.mlbx-rank-unit { font-size: 0.68rem; opacity: 0.6; margin-left: 2px; font-weight: 500; }
</style>
"""


def _find_ranking_entry(*sources: Dict[str, Any], provider: str, station_id: str) -> Optional[dict]:
    for src in sources:
        metrics = src.get("metrics", {}) if isinstance(src, dict) else {}
        for metric in RANKING_METRICS:
            for e in metrics.get(metric, []) or []:
                if str(e.get("provider")) == provider and str(e.get("station_id")) == station_id:
                    return e
    return None


def _handle_rank_connect(*sources: Dict[str, Any], apply_station_selection) -> None:
    """Si llega ?rank_connect=<proveedor>~<station_id> (clic en un nombre del
    ranking), conecta a esa estación y salta a Observación."""
    rc = st.query_params.get("rank_connect")
    if not rc or not callable(apply_station_selection):
        return
    try:
        prov_q, _, sid_q = str(rc).partition("~")
        provider = _urllib.unquote(prov_q)
        station_id = _urllib.unquote(sid_q)
        # La entrada (lat/lon/nombre) es solo para pre-rellenar; puede no estar en
        # la caché (p.ej. la estación se vio en otro día con las flechas). Aun así
        # conectamos con proveedor+id: la Observación rellena el resto al cargar.
        entry = _find_ranking_entry(*sources, provider=provider, station_id=station_id) or {}
        if provider and station_id:
            apply_station_selection(
                {
                    "provider_id": provider,
                    "station_id": station_id,
                    "name": entry.get("name") or station_id,
                    "lat": entry.get("lat"),
                    "lon": entry.get("lon"),
                    "elevation_m": entry.get("elevation_m") or entry.get("elevation") or entry.get("alt"),
                    "station_tz": entry.get("station_tz") or entry.get("timezone") or "",
                },
                connected=True,
                pending_active_tab="observation",
                clear_runtime_cache=True,
            )
    finally:
        if "rank_connect" in st.query_params:
            del st.query_params["rank_connect"]
        st.rerun()


def _selected_country(lat: float, lon: float, available: List[str]) -> Optional[str]:
    """País activo del selector: respeta la elección previa del usuario; si no,
    cae al detectado (bbox/zona horaria) y, en último término, al primero con
    datos."""
    options = _country_options(available, _resolve_user_country(lat, lon))
    current = st.session_state.get("ranking_country")
    if current in options:
        return current
    detected = _resolve_user_country(lat, lon)
    return detected if detected in options else (options[0] if options else None)


def handle_rank_connect_query(ctx) -> None:
    """Procesa un clic de ranking aunque la pestaña activa ya no sea Ranking."""
    apply_station_selection = ctx.get("apply_station_selection") if isinstance(ctx, dict) else None
    rc = st.query_params.get("rank_connect")
    if not rc or not callable(apply_station_selection):
        return

    gdata = _cached_ranking(None, 10)
    lat = _safe_float(st.session_state.get("map_search_lat"), 40.4168)
    lon = _safe_float(st.session_state.get("map_search_lon"), -3.7038)
    country = _selected_country(lat, lon, _cached_ranking_countries())
    cdata = _cached_country_ranking(country, 10) if country else {}
    _handle_rank_connect(cdata, gdata, apply_station_selection=apply_station_selection)


def render_ranking_tab(ctx) -> None:
    section_title = ctx["section_title"]
    t = ctx["t"]
    dark = bool(ctx.get("dark", True))
    apply_station_selection = ctx.get("apply_station_selection")

    border = "rgba(255,255,255,0.10)" if dark else "rgba(15,18,25,0.10)"
    st.markdown(
        f"<style>:root {{ --mlbx-rank-border: {border}; }}</style>" + _RANKING_CSS,
        unsafe_allow_html=True,
    )

    # Datos (cacheados). Cada sección muestra UNA fecha local (sin mezclar
    # husos); el día se elige con las flechas ◀▶ (state en session_state).
    # Toggle "sin Antártida": excluye AQ del global (domina siempre las mínimas).
    # Al abrir (sin día elegido aún) se pide explícitamente el HOY del usuario:
    # sin esto el backend elige su fecha principal, y un día pasado completo
    # puede ganar al día en curso. Si el hoy local todavía no tiene datos, el
    # backend cae solo a su fecha principal (el request lleva el día como
    # preferencia, no como exigencia).
    today_local = _user_today_iso()
    st.session_state.setdefault("ranking_global_day", today_local)
    st.session_state.setdefault("ranking_country_day", today_local)
    g_exclude = "AQ" if st.session_state.get("ranking_no_antarctica") else None
    gdata = _cached_ranking(None, 10, st.session_state.get("ranking_global_day"), g_exclude)
    lat = _safe_float(st.session_state.get("map_search_lat"), 40.4168)
    lon = _safe_float(st.session_state.get("map_search_lon"), -3.7038)
    available = _cached_ranking_countries()
    options = _country_options(available, _resolve_user_country(lat, lon))
    # Default del selector de país = país detectado (lo fija antes de instanciar
    # el widget; si el usuario ya eligió otro, se respeta).
    if options and st.session_state.get("ranking_country") not in options:
        detected = _resolve_user_country(lat, lon)
        st.session_state["ranking_country"] = detected if detected in options else options[0]
    selected = st.session_state.get("ranking_country")
    cdata = _cached_country_ranking(selected, 10, st.session_state.get("ranking_country_day"))

    # Clic en un nombre del ranking → conectar (antes de renderizar).
    _handle_rank_connect(cdata, gdata, apply_station_selection=apply_station_selection)

    section_title(t("ranking.section_title"))
    updated = _updated_caption(gdata, t)
    if updated:
        st.caption(f":material/schedule: {updated}")

    # --- Sección país (por defecto el del usuario; se puede elegir cualquiera) ---
    st.markdown(f"#### {t('ranking.country_section')}")
    if options:
        selected = st.selectbox(
            t("ranking.country_label"),
            options,
            format_func=_country_name,
            key="ranking_country",
        )
        # Refetch por si cambió el país (las fechas disponibles dependen de él).
        cdata = _cached_country_ranking(selected, 10, st.session_state.get("ranking_country_day"))
        _render_day_nav(cdata, "ranking_country_day")
        country_name = _country_name(selected)
        _render_section(
            cdata, t,
            show_provider=True,
            dark=dark,
            empty_msg=t("ranking.country_empty", provider=country_name),
        )
    else:
        st.info(t("ranking.no_country"))

    st.divider()

    # --- Sección global ---
    st.markdown(f"#### {t('ranking.global_title')}")
    st.toggle(t("ranking.exclude_antarctica"), key="ranking_no_antarctica")
    _render_day_nav(gdata, "ranking_global_day")
    _render_section(
        gdata, t,
        show_provider=False,
        show_country=True,
        dark=dark,
        empty_msg=t("ranking.global_empty"),
    )
    providers = gdata.get("providers", []) if isinstance(gdata, dict) else []
    if providers:
        st.caption(t("ranking.providers_note", providers=", ".join(providers)))

"""
MeteoLabx - Panel meteorológico avanzado
Aplicación principal
"""
import time as _time_boot
import os as _os_boot
_BOOT_T0 = _time_boot.perf_counter()
_BOOT_LAST = _BOOT_T0
_BOOT_ENABLED = _os_boot.environ.get("MLX_BOOT_PROFILE", "1") != "0"

def _boot_mark(label: str) -> None:
    global _BOOT_LAST
    if not _BOOT_ENABLED:
        return
    now = _time_boot.perf_counter()
    total_ms = (now - _BOOT_T0) * 1000.0
    delta_ms = (now - _BOOT_LAST) * 1000.0
    _BOOT_LAST = now
    print(f"[BOOT] +{total_ms:7.1f}ms (Δ {delta_ms:7.1f}ms) {label}", flush=True)

_boot_mark("script start")

import streamlit as st
_boot_mark("import streamlit")
import streamlit.components.v1 as components
from pathlib import Path as _Path
_boot_mark("import streamlit.components / pathlib")


def _resolve_favicon_data_url() -> str:
    """
    Devuelve el favicon como data-URL base64.

    Si a ``page_icon`` se le pasa un path, Streamlit lo procesa en cada
    rerun vía ``image_to_url`` (importa PIL/numpy la primera vez y registra
    el fichero en el MediaFileManager en las siguientes). Con una URL de
    esquema ``data:`` entra en el fast path de ``image_to_url`` y se
    devuelve tal cual, sin tocar PIL. El PNG son ~2 KB: leer + codificar
    en base64 cuesta microsegundos por rerun.
    """
    candidates = [
        _Path(__file__).parent / "static" / "favicon.png",
        _Path(__file__).parent / "favicon.png",
        _Path(__file__).parent / "static" / "favicon-32x32.png",
        _Path(__file__).parent / "favicon-32x32.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            import base64
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
    return ""


_FAVICON_PATH = _resolve_favicon_data_url()

st.set_page_config(
    page_title="MeteoLabX",
    # Favicon de la pestaña del navegador: usamos el favicon "neutro" (no el
    # icono azul de webapp). El icono azul con isobaras se reserva para PWA
    # / home screen via apple-touch-icon.
    page_icon=_FAVICON_PATH or "🌤️",
    layout="wide",
    initial_sidebar_state="collapsed"  # Sidebar colapsada por defecto en móvil
)
_boot_mark("st.set_page_config")

# ============================================================
# PWA METADATA — se inyecta lo antes posible para que iOS Safari encuentre
# los <link rel="apple-touch-icon"> y rel="manifest" antes de que el usuario
# pulse "Añadir a pantalla de inicio". Si se inyecta tarde, iOS toma un
# screenshot de la página como icono porque ya ha leído el DOM inicial sin
# los tags. También ayuda a Safari de escritorio a pintar el favicon
# correctamente en la primera carga.
# ============================================================
# Bump esta versión cada vez que cambien los iconos / manifest para forzar
# que el navegador (y la pantalla de inicio de iOS) recarguen los assets en
# vez de servir el icono cacheado.


from components.web_injectors import (
    inject_pwa_metadata as _inject_pwa_metadata,
    inject_mobile_plotly_compactor as _inject_mobile_plotly_compactor,
    inject_live_age_updater as _inject_live_age_updater,
)


_inject_pwa_metadata()
_boot_mark("_inject_pwa_metadata")

import time
import math
import logging
import html
import hashlib
from typing import Optional
from datetime import datetime
_boot_mark("stdlib imports")

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES, RD
_boot_mark("import config")
from data_files import METOFFICE_STATIONS_PATH, STATION_CATALOG_TOTAL, STATIONS_DB_PATH
_boot_mark("import data_files")
from utils import html_clean, is_nan, coerce_str, es_datetime_from_epoch, age_string, month_name, t, t_list
_boot_mark("import utils")
from utils.storage import (
    flush_local_storage_writes,
    get_stored_autoconnect,
    get_stored_autoconnect_target,
)
from utils.provider_state import (
    apply_station_selection,
    apply_weatherlink_station_state,
    build_connection_snapshot,
    disable_provider_autoconnect,
    disconnect_active_station,
    persist_provider_autoconnect_target,
    get_provider_label as resolve_provider_label,
    get_provider_station_id as resolve_provider_station_id,
    is_manual_iem_station,
    resolve_provider_locality,
    restore_connection_state_from_loading_payload,
)
from utils.provider_features import get_provider_feature
from utils.browser_sync import (
    clear_connection_loading_overlay,
    hydrate_browser_context_live,
    render_connection_loading_overlay,
    sync_browser_context_early,
)
from utils.historical_dispatch import fetch_historical_dataset
from utils.station_metadata import aemet_series_start, iem_series_start, meteocat_series_start, meteofrance_series_start
from utils.station_slug import slugify as _station_slug
from utils.series_state import (
    chart_series_has_backend_derivatives,
    clear_series_owner,
    normalize_chart_series,
    series_from_state,
    series_owner_matches,
    set_series_owner,
    store_chart_series,
    store_series_state,
    store_trend_hourly_series,
)
from utils.state_keys import (
    ACTIVE_KEY,
    ACTIVE_STATION,
    ACTIVE_Z,
    CONNECTION_LOADING,
    CONNECTED,
    CONNECTION_TYPE,
    ELEVATION_SOURCE,
    LAST_UPDATE_TIME,
    PROVIDER_STATION_ALT,
    PROVIDER_STATION_ID,
    PROVIDER_STATION_NAME,
    STATION_ELEVATION,
    STATION_LAT,
    STATION_LON,
)
from utils.units import (
    convert_precip,
    convert_pressure,
    convert_radiation,
    convert_temperature,
    convert_temperature_delta,
    convert_wind,
    format_precip,
    format_pressure,
    format_radiation,
    format_radiation_energy,
    format_temperature,
    format_wind,
    normalize_unit_preferences,
    precip_unit_label,
    pressure_unit_label,
    radiation_energy_unit_label,
    radiation_unit_label,
    temperature_unit_label,
    wind_unit_label,
)
_boot_mark("import utils.* (storage/provider/series/units)")
from api.weather_underground import (
    fetch_hourly_7day_session_cached,
    fetch_wu_dashboard_session_cached,
)
from utils.api_errors import BackendApiError
_boot_mark("import api")
from models.radiation import (
    sky_clarity_label, water_balance_label,
)
_boot_mark("import models.radiation")
from domain.wu_calibration import (
    default_wu_calibration,
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, render_sidebar
)
_boot_mark("import components (card/grid/sidebar)")
from components.app_header import render_app_header, render_connection_banner
from components.favorites import render_favorites_bar

from components.browser_context import get_browser_context
from components.browser_geolocation import get_browser_geolocation
_boot_mark("import components.* (header/favs/browser)")

# Las tabs son los módulos más grandes del proyecto (observation, trends,
# historical, map suman ~2.770 líneas). Solo se renderiza una por rerun, así
# que se importan bajo demanda mediante :func:`_get_tab_renderer`.

# Configurar logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# Las constantes y la función _inject_pwa_metadata se definen ahora al inicio
# del archivo (justo después de set_page_config) para que el JS de iconos se
# ejecute lo antes posible. Ver bloque "PWA METADATA" en las primeras líneas.


def _get_climograms_service():
    import services.climograms as climograms
    return climograms


_TAB_MODULE_CACHE: dict[str, object] = {}


def _get_tab_module():
    """
    Importa ``tabs`` bajo demanda y memoiza el módulo.

    Las cuatro tabs (observation, trends, historical, map) suman ~2.770 líneas
    y solo se renderiza una a la vez, así que cargarlas en arranque era un
    coste innecesario para reruns que ni siquiera tocan la UI principal.
    """
    cached = _TAB_MODULE_CACHE.get("tabs")
    if cached is not None:
        return cached
    import tabs as tabs_module
    _TAB_MODULE_CACHE["tabs"] = tabs_module
    return tabs_module


def build_observation_context(*args, **kwargs):
    """Facade lazy hacia :func:`tabs.build_observation_context`."""
    return _get_tab_module().build_observation_context(*args, **kwargs)


def _get_provider_label(provider_id: str) -> str:
    return resolve_provider_label(provider_id)


def _get_provider_station_id(provider_id: str) -> str:
    return resolve_provider_station_id(st.session_state, provider_id)


def _get_provider_api_key(provider_id: str):
    provider_id = coerce_str(provider_id, upper=True)
    api_key_resolvers = {
        "WU": lambda: str(
            st.session_state.get("wu_connected_api_key", "")
            or st.session_state.get("active_key", "")
        ).strip(),
        "WEATHERLINK": lambda: str(st.session_state.get("weatherlink_api_key", "")).strip(),
    }
    feature = get_provider_feature(provider_id)
    resolver = api_key_resolvers.get(str(feature.get("api_key_source", provider_id)).strip().upper())
    if resolver is not None:
        return resolver()
    return None


def _get_provider_api_secret(provider_id: str) -> str:
    provider_id = coerce_str(provider_id, upper=True)
    if provider_id == "WEATHERLINK":
        return str(st.session_state.get("weatherlink_api_secret", "") or "").strip()
    return ""


SERIES_START_LOADERS = {
    "AEMET": {
        "loader": aemet_series_start,
        "formatter": lambda value: datetime.fromisoformat(value).strftime("%d/%m/%Y"),
    },
    "METEOCAT": {
        "loader": meteocat_series_start,
        "formatter": lambda value: datetime.fromisoformat(value).strftime("%d/%m/%Y"),
    },
    "METEOFRANCE": {
        "loader": meteofrance_series_start,
        "formatter": lambda value: str(value),
    },
    "IEM": {
        "loader": iem_series_start,
        "formatter": lambda value: datetime.fromisoformat(value).strftime("%d/%m/%Y"),
    },
}


def _render_historical_provider_series_start(provider_id: str, station_id: str) -> None:
    provider_id = coerce_str(provider_id, upper=True)
    if not station_id:
        return

    provider_feature = get_provider_feature(provider_id)
    provider_label = str(provider_feature.get("series_start_provider_label", "")).strip()
    series_loader_meta = SERIES_START_LOADERS.get(str(provider_feature.get("series_start_source", "")).strip().upper(), {})
    loader = series_loader_meta.get("loader")
    if not callable(loader) or not provider_label:
        return

    series_start_iso = loader(station_id)
    if series_start_iso:
        formatter = series_loader_meta.get("formatter")
        try:
            start_txt = formatter(series_start_iso) if callable(formatter) else str(series_start_iso)
        except Exception:
            start_txt = str(series_start_iso)
        st.caption(
            t(
                "historical.notes.series_start",
                provider=provider_label,
                value=start_txt,
            )
        )
    else:
        st.caption(t("historical.notes.series_start_unavailable", provider=provider_label))


def _get_historical_missing_message(
    provider_id: str,
    station_id: str,
    api_key,
    api_secret: str = "",
) -> str:
    provider_id = coerce_str(provider_id, upper=True)
    provider_feature = get_provider_feature(provider_id)
    key = str(provider_feature.get("historical_missing_key", "")).strip()
    if not key:
        return ""
    if provider_feature.get("requires_api_key") and (not station_id or not api_key):
        return t(key)
    if provider_feature.get("requires_api_secret") and not api_secret:
        return t(key)
    if not provider_feature.get("requires_api_key") and not station_id:
        return t(key)
    return ""


def _standard_provider_runtime_config() -> dict[str, dict]:
    return {
        "AEMET": {
            "fallback_key": "aemet_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de AEMET por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "aemet_last_error",
            "detail_prefix": "Detalle técnico AEMET: ",
            "series_mode": "from_base",
        },
        "EUSKALMET": {
            "fallback_key": "euskalmet_station_alt",
            "warning": (
                "⚠️ No se pudieron obtener datos de Euskalmet ahora mismo. "
                "Si falla en una sola estación, prueba con otra o reinténtalo "
                "en unos minutos; si falla en todas, suele ser un problema de "
                "credenciales/JWT del backend. Revisa el detalle técnico abajo."
            ),
            "detail_key": "euskalmet_last_error",
            "detail_prefix": "Detalle técnico Euskalmet: ",
            "series_mode": "none",
        },
        "METEOCAT": {
            "fallback_key": "meteocat_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Meteocat por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "none",
        },
        "METEOFRANCE": {
            "fallback_key": "meteofrance_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Meteo-France por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "meteofrance_last_error",
            "detail_prefix": "Detalle técnico Meteo-France: ",
            "series_mode": "from_base",
        },
        "FROST": {
            "fallback_key": "frost_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Frost por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "frost_last_error",
            "detail_prefix": "Detalle técnico Frost: ",
            "series_mode": "from_base",
        },
        "METEOGALICIA": {
            "fallback_key": "meteogalicia_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de MeteoGalicia por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "copy_chart",
        },
        "NWS": {
            "fallback_key": "nws_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de NWS por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "from_base",
        },
        "POEM": {
            "fallback_key": "poem_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de POEM por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "poem_last_error",
            "detail_prefix": "Detalle técnico POEM: ",
            "series_mode": "from_base",
        },
        "METOFFICE": {
            "fallback_key": "metoffice_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Met Office por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "metoffice_last_error",
            "detail_prefix": "Detalle técnico Met Office: ",
            "series_mode": "from_base",
        },
        "METEOHUB_IT": {
            "fallback_key": "meteohub_it_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de MeteoHub Italia por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "meteohub_last_error",
            "detail_prefix": "Detalle técnico MeteoHub Italia: ",
            "series_mode": "from_base",
        },
        "IEM": {
            "fallback_key": "iem_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de IEM por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "iem_last_error",
            "detail_prefix": "Detalle técnico IEM: ",
            "series_mode": "from_base",
        },
        "WEATHERLINK": {
            "fallback_key": "weatherlink_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de WeatherLink por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "weatherlink_last_error",
            "detail_prefix": "Detalle técnico WeatherLink: ",
            "series_mode": "none",
        },
    }


def _process_standard_provider_connection(provider_id: str):
    provider_id = coerce_str(provider_id, upper=True)
    config = _standard_provider_runtime_config().get(provider_id)
    if not config:
        raise KeyError(f"Proveedor estándar no soportado: {provider_id}")

    try:
        from frontend.dashboard_payload import build_dashboard_payload
        from utils.api_client import fetch_provider_current_processed_via_api

        station_id = _get_provider_station_id(provider_id)
        if not station_id:
            raise BackendApiError("missing_station")

        credentials = {}
        if provider_id == "WEATHERLINK":
            credentials = {
                "api_key": str(st.session_state.get("weatherlink_api_key", "") or ""),
                "api_secret": str(st.session_state.get("weatherlink_api_secret", "") or ""),
            }

        station_tz = str(
            st.session_state.get("provider_station_tz")
            or st.session_state.get("browser_tz")
            or ""
        ).strip()
        try:
            user_elevation = float(st.session_state.get(ACTIVE_Z) or 0.0)
        except (TypeError, ValueError):
            user_elevation = 0.0

        processed_response = fetch_provider_current_processed_via_api(
            provider_id,
            station_id,
            sun_tz_name=station_tz,
            max_data_age_minutes=MAX_DATA_AGE_MINUTES,
            station_elevation=user_elevation if user_elevation > 0 else None,
            **credentials,
        )

        # La serie sinóptica de 7 días NO se pide aquí: para proveedores lentos
        # (AEMET puede tardar 15-60 s o fallar con 404/429) bloqueaba la
        # conexión —y cada rerun del ciclo de conexión y cada autorefresh la
        # repetía—, dejando la página clavada tras conectar desde el Ranking.
        # La pestaña Tendencias la trae bajo demanda con su propio spinner
        # (_fetch_trends_synoptic_series) y la persiste en session_state; aquí
        # solo la reutilizamos si ya está en el estado para esta estación.
        recent_series = None
        if str(config.get("series_mode", "none")).strip().lower() == "from_base":
            if series_owner_matches(st.session_state, "trend_hourly", provider_id, station_id):
                state_series = series_from_state(st.session_state, "trend_hourly")
                if state_series.get("has_data") or state_series.get("epochs"):
                    recent_series = state_series

        dashboard = build_dashboard_payload(processed_response, recent_series=recent_series)
        derivatives = process_standard_provider(
            dashboard,
            provider_id,
            config["fallback_key"],
        )
    except BackendApiError as exc:
        detail = f"{exc.kind}" + (
            f" (HTTP {exc.status_code})" if exc.status_code else ""
        )
        if getattr(exc, "detail", ""):
            detail = f"{detail}: {exc.detail}"
        warning_message = _friendly_provider_warning(provider_id, config["warning"], detail)
        st.warning(warning_message)
        logger.warning("Backend %s /processed fallo: %s", provider_id, detail)
        if provider_id == "IEM":
            st.session_state[CONNECTED] = True
            st.session_state.pop(CONNECTION_LOADING, None)
            clear_connection_loading_overlay()
            return None, {}
        _cancel_connection_loading()
        st.stop()

    return dashboard, derivatives


def _fetch_provider_synoptic_series_from_state(provider_id: str) -> tuple[dict, str]:
    provider_id = coerce_str(provider_id, upper=True)
    provider_feature = get_provider_feature(provider_id)
    source_key = str(provider_feature.get("synoptic_source_key", "")).strip()
    source_label = t(source_key) if source_key else t("trends.sources.generic_synoptic", provider=provider_id)
    station_id = _get_provider_station_id(provider_id)
    if not series_owner_matches(st.session_state, "trend_hourly", provider_id, station_id):
        store_trend_hourly_series(st.session_state, None)
        _clear_trend_hourly_series_owner()
        return _empty_synoptic_series(), source_label

    hourly7d = store_trend_hourly_series(st.session_state, series_from_state(st.session_state, "trend_hourly"))
    hourly7d["has_data"] = bool(hourly7d.get("has_data")) or len(hourly7d["epochs"]) > 0
    if source_key:
        return hourly7d, source_label
    return hourly7d, source_label


def _friendly_provider_warning(provider_id: str, generic_warning: str, detail: str = "") -> str:
    provider_label = resolve_provider_label(provider_id)
    detail_text = str(detail or "").strip()
    lowered = detail_text.lower()
    if not lowered:
        return generic_warning

    if any(token in lowered for token in ("faltan ", "missing ", "api key", "client_id", "client_secret", "jwt")):
        return f"⚠️ Faltan credenciales o configuración de {provider_label}."

    if any(token in lowered for token in ("401", "403", "unauthorized", "forbidden", "sin permisos", "inválid", "invalid")):
        return f"⚠️ {provider_label} rechazó la autenticación o no hay permisos suficientes."

    if any(token in lowered for token in ("timeout", "timed out", "no responde a tiempo", "read timed out")):
        return f"⚠️ {provider_label} tardó demasiado en responder. Intenta de nuevo en unos minutos."

    if any(token in lowered for token in ("429", "too many requests", "rate limit", "ratelimit", "límite", "limite")):
        return f"⚠️ {provider_label} está limitando temporalmente las peticiones. Espera unos minutos antes de intentarlo de nuevo."

    if any(token in lowered for token in ("connection aborted", "failed to establish", "max retries exceeded", "name or service not known", "temporary failure", "network", "proxyerror", "ssLError".lower(), "no se pudo contactar")):
        return f"⚠️ No se pudo contactar con {provider_label} ahora mismo. Puede ser un problema temporal de red o del servicio."

    if any(token in lowered for token in ("nodata", "no hay datos", "sin datos", "serie vacía", "series vigentes", "no devolvió datos", "does not satisfy", "satisfagan esos criterios", "no data", "empty series")):
        if coerce_str(provider_id, upper=True) == "IEM":
            import re

            match = re.search(r"entre\s+(\d{4}-\d{2}-\d{2})\s+y\s+(\d{4}-\d{2}-\d{2})", detail_text, re.IGNORECASE)
            if match:
                start_txt, end_txt = match.groups()
                return (
                    "⚠️ Esta estación IEM está fuera de servicio para observación actual, "
                    f"pero tiene histórico disponible entre {start_txt} y {end_txt}."
                )
            return "⚠️ IEM tiene esta estación en el archivo histórico, pero no hay observación actual disponible para mostrar ahora."
        return f"⚠️ {provider_label} no está devolviendo datos utilizables para esa estación en este momento."

    if any(token in lowered for token in ("404", "not found", "station_id vacío", "falta station_id", "falta id_station", "endpoint")):
        return f"⚠️ {provider_label} no encuentra esa estación o no tiene un endpoint válido ahora mismo."

    if any(token in lowered for token in ("too old", "demasiado antiguo", "antigu", "stale")):
        return f"⚠️ {provider_label} está devolviendo datos demasiado antiguos para usar esa estación con normalidad."

    return generic_warning


def _cancel_connection_loading() -> None:
    restore_connection_state_from_loading_payload(st.session_state.get(CONNECTION_LOADING))
    st.session_state.pop(CONNECTION_LOADING, None)
    clear_connection_loading_overlay()


def _queue_wu_connection_error(kind: str, status_code: Optional[int] = None) -> str:
    kind = str(kind or "").strip().lower()
    message_key = {
        "unauthorized": "sidebar.messages.wu_credentials_rejected",
        "notfound": "sidebar.messages.wu_station_not_found",
        "ratelimit": "sidebar.messages.wu_rate_limited",
        "timeout": "sidebar.messages.wu_timeout",
        "network": "sidebar.messages.wu_network",
        "badjson": "sidebar.messages.wu_bad_response",
    }.get(kind, "sidebar.messages.wu_connection_error")
    st.session_state["_wu_connection_error_key"] = message_key
    st.session_state["_wu_connection_error_status"] = f" (HTTP {status_code})" if status_code else ""
    return message_key


def _has_live_connection_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        epoch = int(payload.get("epoch", 0) or 0)
    except Exception:
        epoch = 0
    if epoch > 0:
        return True
    for key in ("Tc", "RH", "wind", "p_hpa", "p_station", "Td"):
        value = payload.get(key, float("nan"))
        try:
            if not is_nan(value):
                return True
        except Exception:
            continue
    return False


def _set_chart_series_owner(provider_id: str, station_id: str) -> None:
    set_series_owner(st.session_state, "chart", provider_id, station_id)


def _set_trend_hourly_series_owner(provider_id: str, station_id: str) -> None:
    set_series_owner(st.session_state, "trend_hourly", provider_id, station_id)


def _clear_trend_hourly_series_owner() -> None:
    clear_series_owner(st.session_state, "trend_hourly")


def _chart_series_fresh_for_station(provider_id: str, station_id: str, *, max_age_s: Optional[int] = None) -> bool:
    provider_norm = coerce_str(provider_id, upper=True)
    station_norm = coerce_str(station_id, upper=True)
    if not provider_norm or not station_norm:
        return False
    chart_provider = coerce_str(st.session_state.get("chart_series_provider_id", ""), upper=True)
    chart_station = coerce_str(st.session_state.get("chart_series_station_id", ""), upper=True)
    if chart_provider != provider_norm or chart_station != station_norm:
        return False
    chart_state = series_from_state(st.session_state, "chart", pressure_key="pressures_abs")
    epochs = []
    for ep in chart_state.get("epochs", []):
        try:
            epochs.append(int(ep))
        except (TypeError, ValueError):
            continue
    if not epochs:
        return False
    if not chart_series_has_backend_derivatives(chart_state):
        return False
    freshness_window = int(max_age_s if max_age_s is not None else max(300, REFRESH_SECONDS * 2))
    return max(epochs) >= int(time.time()) - freshness_window


def _normalize_observation_chart_series(provider_id: str, payload: Optional[dict]) -> dict:
    provider_id = coerce_str(provider_id, upper=True)
    series = payload if isinstance(payload, dict) else {}
    series_for_norm = dict(series)
    if "pressures_abs" not in series_for_norm and "pressures" in series_for_norm:
        series_for_norm["pressures_abs"] = series_for_norm.get("pressures", [])
    normalized = normalize_chart_series(series_for_norm)
    normalized["has_data"] = bool(series.get("has_data", False)) or len(normalized.get("epochs", [])) > 0
    return normalized


def _fetch_deferred_observation_chart_series(provider_id: str, station_id: str) -> dict:
    provider_id = coerce_str(provider_id, upper=True)
    station_id = coerce_str(station_id, upper=True)
    if not provider_id or not station_id:
        return {"epochs": [], "has_data": False}

    if provider_id in _standard_provider_runtime_config():
        try:
            from utils.api_client import fetch_provider_today_series_via_api_strict

            credentials = {}
            if provider_id == "WEATHERLINK":
                credentials = {
                    "api_key": str(st.session_state.get("weatherlink_api_key", "") or ""),
                    "api_secret": str(st.session_state.get("weatherlink_api_secret", "") or ""),
                }
            series = fetch_provider_today_series_via_api_strict(
                provider_id,
                station_id,
                **credentials,
            )
        except BackendApiError:
            return {"epochs": [], "has_data": False}
        return _normalize_observation_chart_series(provider_id, series)

    return {"epochs": [], "has_data": False}


def _empty_synoptic_series() -> dict:
    return {"has_data": False, "epochs": [], "temps": [], "humidities": [], "pressures": []}


def _fetch_trends_synoptic_series(provider_id: str):
    provider_id = coerce_str(provider_id, upper=True)
    station_id = _get_provider_station_id(provider_id)
    provider_feature = get_provider_feature(provider_id)
    try:
        station_elevation = float(st.session_state.get(STATION_ELEVATION) or st.session_state.get(ACTIVE_Z) or 0.0)
    except (TypeError, ValueError):
        station_elevation = 0.0
    state_hourly7d, state_source_label = _fetch_provider_synoptic_series_from_state(provider_id)
    if state_hourly7d.get("has_data"):
        return state_hourly7d, state_source_label
    if provider_id == "WU":
        api_key = _get_provider_api_key(provider_id)
        calibration_station = str(st.session_state.get("wu_station_calibration_station", "")).strip().upper()
        if calibration_station == str(station_id).strip().upper():
            station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
        else:
            station_calibration = default_wu_calibration()
        with st.spinner("Obteniendo datos horarios de 7 días..."):
            hourly7d = fetch_hourly_7day_session_cached(
                station_id,
                api_key,
                calibration=station_calibration,
                station_elevation=station_elevation if station_elevation > 0 else None,
            )
        return hourly7d, t(
            str(provider_feature.get("synoptic_source_key", "trends.sources.wu_synoptic"))
        )

    if provider_id in _standard_provider_runtime_config() and station_id:
        from utils.api_client import fetch_provider_recent_series_via_api_strict

        source_label = t(str(provider_feature.get("synoptic_source_key", "")).strip())

        # Negative cache: si el fetch acaba de fallar (proveedores lentos como
        # AEMET pueden tardar 15-60 s en fallar), no lo repitas en cada rerun
        # de la pestaña; muestra el aviso y reintenta pasada la ventana.
        fail_key = f"_trends_synoptic_fail_{provider_id}_{station_id}"
        fail_info = st.session_state.get(fail_key)
        if isinstance(fail_info, dict) and (time.time() - float(fail_info.get("at", 0))) < 120:
            _render_trends_synoptic_warning(str(fail_info.get("kind", "")))
            return _empty_synoptic_series(), source_label

        credentials = {}
        if provider_id == "WEATHERLINK":
            credentials = {
                "api_key": str(st.session_state.get("weatherlink_api_key", "") or ""),
                "api_secret": str(st.session_state.get("weatherlink_api_secret", "") or ""),
            }
        try:
            with st.spinner("Obteniendo serie sinóptica reciente..."):
                hourly7d = fetch_provider_recent_series_via_api_strict(
                    provider_id,
                    station_id,
                    days_back=7,
                    station_elevation=station_elevation if station_elevation > 0 else None,
                    **credentials,
                )
        except BackendApiError as exc:
            # El backend no pudo servir la serie sinóptica (rate limit del
            # proveedor, timeout, backend caído…). No reventamos la pestaña:
            # avisamos y devolvemos serie vacía (los gráficos muestran su
            # estado "sin datos").
            logger.warning(
                "Serie sinóptica %s no disponible (%s)", provider_id, exc.kind,
            )
            st.session_state[fail_key] = {"at": time.time(), "kind": str(exc.kind)}
            _render_trends_synoptic_warning(str(exc.kind))
            return _empty_synoptic_series(), source_label
        st.session_state.pop(fail_key, None)
        # Persistir en session_state (con ownership) para que los siguientes
        # renders de Tendencias —y el pipeline de Observación vía
        # _process_standard_provider_connection— reutilicen la serie sin
        # volver a pedirla al backend.
        stored = store_trend_hourly_series(st.session_state, hourly7d)
        _set_trend_hourly_series_owner(provider_id, station_id)
        return stored, source_label
    return state_hourly7d, state_source_label


def _render_trends_synoptic_warning(kind: str) -> None:
    if kind == "ratelimit":
        st.warning(
            "⚠️ El proveedor ha limitado las peticiones (rate limit). "
            "La serie de tendencias no está disponible ahora mismo; "
            "reinténtalo en unos minutos."
        )
    else:
        st.warning(
            "⚠️ No se pudo obtener la serie de tendencias ahora mismo. "
            "Reinténtalo en unos minutos."
        )


def _build_observation_tab_context() -> dict:
    connection_type = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()

    def _effective_observation_chart_ready() -> bool:
        if has_chart_data:
            return True
        station_id = _get_provider_station_id(connection_type)
        chart_state = series_from_state(st.session_state, "chart", pressure_key="pressures_abs")
        state_ready = (
            station_id
            and series_owner_matches(st.session_state, "chart", connection_type, station_id)
            and (bool(chart_state.get("has_data")) or len(chart_state.get("epochs", [])) > 0)
        )
        if state_ready:
            return True
        return False

    def _ensure_observation_chart_data() -> bool:
        connection_type = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
        if connection_type == "WU":
            station_id = str(st.session_state.get("wu_connected_station", "") or st.session_state.get(ACTIVE_STATION, "")).strip()
            api_key = str(st.session_state.get("wu_connected_api_key", "") or st.session_state.get(ACTIVE_KEY, "")).strip()
            station_id = station_id.upper()
            if not station_id or not api_key:
                return False
            if _chart_series_fresh_for_station(connection_type, station_id):
                return True

            station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
            try:
                station_elevation = float(st.session_state.get(STATION_ELEVATION) or 0.0)
            except (TypeError, ValueError):
                station_elevation = 0.0
            dashboard = fetch_wu_dashboard_session_cached(
                station_id,
                api_key,
                ttl_s=REFRESH_SECONDS,
                calibration=station_calibration,
                station_elevation=station_elevation if station_elevation > 0 else None,
                sun_tz_name=str(
                    st.session_state.get("provider_station_tz")
                    or st.session_state.get("browser_tz")
                    or ""
                ).strip(),
            )
            timeseries = dict(dashboard.get("series") or {})
            chart_pressures = timeseries.get("pressures_abs", []) or []
            chart_precips = timeseries.get("precips", [])

            ts_lat = timeseries.get("lat", float("nan"))
            ts_lon = timeseries.get("lon", float("nan"))
            if is_nan(st.session_state.get(STATION_LAT, float("nan"))) and not is_nan(ts_lat):
                st.session_state[STATION_LAT] = ts_lat
            if is_nan(st.session_state.get(STATION_LON, float("nan"))) and not is_nan(ts_lon):
                st.session_state[STATION_LON] = ts_lon

            wu_sensor_presence = (dashboard.get("station") or {}).get("sensors") or {}
            st.session_state["wu_sensor_presence"] = wu_sensor_presence
            st.session_state["wu_sensor_presence_station"] = station_id

            chart_series = store_chart_series(
                st.session_state,
                {
                    **timeseries,
                    "pressures_abs": chart_pressures,
                    "precips": chart_precips,
                }
            )
            _set_chart_series_owner(connection_type, station_id)
        else:
            station_id = _get_provider_station_id(connection_type)
            if not station_id:
                chart_state = series_from_state(st.session_state, "chart", pressure_key="pressures_abs")
                return bool(chart_state.get("has_data")) or len(chart_state.get("epochs", [])) > 0
            if _chart_series_fresh_for_station(connection_type, station_id):
                return True
            chart_series = store_chart_series(
                st.session_state,
                _fetch_deferred_observation_chart_series(connection_type, station_id),
            )
            _set_chart_series_owner(connection_type, station_id)

        return bool(chart_series.get("has_data")) or len(chart_series.get("epochs", [])) > 0

    return build_observation_context(
        {
            "RD": RD,
            "ensure_chart_data": _ensure_observation_chart_data,
            "_fmt_precip_display": _fmt_precip_display,
            "_fmt_pressure_display": _fmt_pressure_display,
            "_fmt_radiation_display": _fmt_radiation_display,
            "_fmt_radiation_energy_display": _fmt_radiation_energy_display,
            "_fmt_temp_display": _fmt_temp_display,
            "_fmt_wind_display": _fmt_wind_display,
            "_infer_series_step_minutes": _infer_series_step_minutes,
            "_plotly_chart_stretch": _plotly_chart_stretch,
            "_translate_balance_label": _translate_balance_label,
            "_translate_clarity_label": _translate_clarity_label,
            "_translate_pressure_trend_label": _translate_pressure_trend_label,
            "_translate_rain_intensity_label": _translate_rain_intensity_label,
            "_translate_sunrise_sunset_label": _translate_sunrise_sunset_label,
            "observation": base,
            "derivatives": current_derivatives,
            "daily_extremes": daily_extremes,
            "station": station_info,
            "card": card,
            "connected": connected,
            "connection_type": connection_type,
            "convert_precip": convert_precip,
            "convert_pressure": convert_pressure,
            "convert_radiation": convert_radiation,
            "convert_temperature": convert_temperature,
            "convert_wind": convert_wind,
            "dark": dark,
            "has_chart_data": _effective_observation_chart_ready(),
            "html": html,
            "is_nan": is_nan,
            "logger": logger,
            "precip_unit_pref": precip_unit_pref,
            "precip_unit_txt": precip_unit_txt,
            "pressure_unit_pref": pressure_unit_pref,
            "pressure_unit_txt": pressure_unit_txt,
            "radiation_energy_unit_txt": radiation_energy_unit_txt,
            "radiation_unit_pref": radiation_unit_pref,
            "radiation_unit_txt": radiation_unit_txt,
            "render_grid": render_grid,
            "section_title": section_title,
            "sky_clarity_label": sky_clarity_label,
            "st": st,
            "t": t,
            "temp_unit_pref": temp_unit_pref,
            "temp_unit_txt": temp_unit_txt,
            "theme_mode": theme_mode,
            "time": time,
            "water_balance_label": water_balance_label,
            "wind_dir_text": wind_dir_text,
            "wind_unit_pref": wind_unit_pref,
            "wind_unit_txt": wind_unit_txt,
        }
    )


def _build_trends_tab_context() -> dict:
    return {
        "t": t,
        "dark": dark,
        "connected": connected,
        "logger": logger,
        "theme_mode": theme_mode,
        "p_abs": p_abs,
        "p_msl": p_msl,
        "temp_unit_pref": temp_unit_pref,
        "temp_unit_txt": temp_unit_txt,
        "pressure_unit_pref": pressure_unit_pref,
        "pressure_unit_txt": pressure_unit_txt,
        "wind_unit_pref": wind_unit_pref,
        "wind_unit_txt": wind_unit_txt,
        "_render_neutral_info_note": _render_neutral_info_note,
        "_infer_series_step_minutes": _infer_series_step_minutes,
        "_fetch_trends_synoptic_series": _fetch_trends_synoptic_series,
        "_get_provider_station_id": _get_provider_station_id,
        "_plotly_chart_stretch": _plotly_chart_stretch,
        "convert_temperature_delta": convert_temperature_delta,
        "convert_pressure": convert_pressure,
        "convert_wind": convert_wind,
        "is_nan": is_nan,
        "components": components,
    }


def _build_historical_tab_context() -> dict:
    return {
        "section_title": section_title,
        "t": t,
        "connected": connected,
        "dark": dark,
        "theme_mode": theme_mode,
        "unit_preferences": unit_preferences,
        "temp_unit_txt": temp_unit_txt,
        "precip_unit_txt": precip_unit_txt,
        "month_name": month_name,
        "BackendApiError": BackendApiError,
        "_render_neutral_info_note": _render_neutral_info_note,
        "_get_provider_station_id": _get_provider_station_id,
        "_get_provider_api_key": _get_provider_api_key,
        "_get_provider_api_secret": _get_provider_api_secret,
        "_render_historical_provider_series_start": _render_historical_provider_series_start,
        "_get_historical_missing_message": _get_historical_missing_message,
        "_get_climograms_service": _get_climograms_service,
        "_get_provider_label": _get_provider_label,
        "_fetch_historical_dataset": fetch_historical_dataset,
        "_render_theme_table": _render_theme_table,
        "_plotly_chart_stretch": _plotly_chart_stretch,
    }


MAP_CATALOG_FILTER_CACHE_VERSION = 2


def _build_map_tab_context() -> dict:
    def _map_catalog_cache_version(provider_ids: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
        versions = [("COUNTRY_FILTER", MAP_CATALOG_FILTER_CACHE_VERSION)]
        # mtime del catálogo SQLite: si el catálogo cambia (rebuild, reparación
        # de flags IEM…), las cachés del mapa (st.cache_data y las de sesión)
        # deben invalidarse; sin esto una sesión abierta seguía sirviendo
        # estaciones con flags antiguos indefinidamente.
        try:
            versions.append(("STATIONS_DB", int(_Path(STATIONS_DB_PATH).stat().st_mtime_ns)))
        except Exception:
            versions.append(("STATIONS_DB", 0))
        provider_set = {coerce_str(provider_id, upper=True) for provider_id in provider_ids}
        if "METOFFICE" in provider_set:
            try:
                versions.append(("METOFFICE", int(METOFFICE_STATIONS_PATH.stat().st_mtime_ns)))
            except Exception:
                versions.append(("METOFFICE", 0))
        return tuple(versions)

    return {
        "section_title": section_title,
        "t": t,
        "dark": dark,
        "theme_mode": theme_mode,
        "math": math,
        "html": html,
        "html_clean": html_clean,
        "get_browser_geolocation": get_browser_geolocation,
        "get_stored_autoconnect": get_stored_autoconnect,
        "get_stored_autoconnect_target": get_stored_autoconnect_target,
        "resolve_provider_locality": resolve_provider_locality,
        "apply_station_selection": apply_station_selection,
        "disable_provider_autoconnect": disable_provider_autoconnect,
        "persist_provider_autoconnect_target": persist_provider_autoconnect_target,
        "_cached_map_search_nearby_stations": _cached_map_search_nearby_stations,
        "_map_catalog_cache_version": _map_catalog_cache_version,
        "_pydeck_chart_stretch": _pydeck_chart_stretch,
    }


def _build_ranking_tab_context() -> dict:
    return {
        "section_title": section_title,
        "t": t,
        "dark": dark,
        "apply_station_selection": apply_station_selection,
    }


TAB_OPTIONS = ["observation", "trends", "historical", "map", "ranking"]
LEGACY_TAB_ALIASES = {
    "Observación": "observation",
    "Tendencias": "trends",
    "Climogramas": "historical",
    "Histórico": "historical",
    "Mapa": "map",
    "Ranking": "ranking",
}

# Slugs de pestaña usados en la URL compartible (?tab=...). Canónicos en
# español (el ejemplo del usuario: /drassanes/observacion); aceptamos también
# los nombres internos en inglés y variantes históricas como alias de entrada.
TAB_URL_SLUGS = {
    "observation": "observacion",
    "trends": "tendencias",
    "historical": "historico",
    "map": "mapa",
    "ranking": "ranking",
}
TAB_SLUG_TO_INTERNAL = {slug: tab for tab, slug in TAB_URL_SLUGS.items()}
TAB_SLUG_TO_INTERNAL.update({
    "observation": "observation",
    "trends": "trends",
    "historical": "historical",
    "climogramas": "historical",
    "climograms": "historical",
    "map": "map",
})

# Proveedores resolubles por slug de nombre (los que viven en el catálogo
# SQLite del backend). WU/WeatherLink son per-usuario y no se comparten así.
DEEPLINK_PROVIDERS = {
    "AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE",
    "METEOGALICIA", "NWS", "POEM", "METOFFICE", "METEOHUB_IT", "IEM",
}


def _query_param_value(key: str) -> str:
    raw = st.query_params.get(key, "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").strip()


def _resolve_deeplink_tab() -> Optional[str]:
    return TAB_SLUG_TO_INTERNAL.get(_query_param_value("tab").lower())


def _handle_station_deeplink() -> None:
    """Procesa ``?e=<provider>~<slug>&tab=<tab>`` en el arranque.

    Permite abrir un link directo a una estación (p. ej.
    ``?e=AEMET~barcelona-drassanes&tab=observacion``): resuelve el slug contra
    el catálogo del backend, conecta y salta a la pestaña indicada. Los params
    se DEJAN en la URL (son compartibles); un guard en session_state evita
    reprocesar el mismo link y permite navegar libremente después.
    """
    raw_e = _query_param_value("e")
    target_tab_slug = _query_param_value("tab").lower()
    target_tab = _resolve_deeplink_tab()

    if not raw_e:
        # Solo navegación de pestaña por URL, sin tocar la estación/sesión.
        #
        # Importante: la propia app mantiene ?tab=... sincronizado en cada
        # cambio de pestaña. Ese valor no debe volver a imponerse al widget en
        # el siguiente rerun, o Streamlit puede entrar en un ciclo incómodo de
        # "URL antigua vs. radio nuevo" que se percibe como doble click.
        if target_tab_slug and target_tab_slug == st.session_state.get("_last_synced_tab_slug"):
            return
        if (
            target_tab
            and st.session_state.get("active_tab") != target_tab
            and st.session_state.get("_deeplink_tab_consumed_slug") != target_tab_slug
        ):
            st.session_state["_deeplink_tab_consumed_slug"] = target_tab_slug
            st.session_state["_pending_active_tab"] = target_tab
            st.rerun()
        return

    if st.session_state.get("_deeplink_consumed") == raw_e:
        return
    st.session_state["_deeplink_consumed"] = raw_e

    provider, _, slug = raw_e.partition("~")
    provider = provider.strip().upper()
    slug = _station_slug(slug)
    if provider not in DEEPLINK_PROVIDERS or not slug:
        return

    # Ya conectado a esa estación: no reconectes, solo ajusta la pestaña.
    current_provider = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
    current_slug = _station_slug(st.session_state.get(PROVIDER_STATION_NAME, ""))
    if st.session_state.get(CONNECTED) and current_provider == provider and current_slug == slug:
        if (
            target_tab
            and st.session_state.get("active_tab") != target_tab
            and target_tab_slug != st.session_state.get("_last_synced_tab_slug")
        ):
            st.session_state["_deeplink_tab_consumed_slug"] = target_tab_slug
            st.session_state["_pending_active_tab"] = target_tab
            st.rerun()
        return

    try:
        from utils.api_client import fetch_station_by_slug_via_api
        record = fetch_station_by_slug_via_api(provider, slug)
    except BackendApiError as exc:
        logger.info(
            "Deep link %s~%s no resoluble (%s)", provider, slug, getattr(exc, "kind", exc)
        )
        return

    apply_station_selection(
        {
            "provider_id": record.get("provider") or provider,
            "station_id": record.get("station_id"),
            "name": record.get("name") or slug,
            "lat": record.get("lat"),
            "lon": record.get("lon"),
            "elevation_m": record.get("elevation"),
            "station_tz": record.get("tz") or "",
        },
        connected=True,
        pending_active_tab=target_tab or "observation",
        clear_runtime_cache=True,
    )
    st.rerun()


def _sync_shareable_url(active_tab: str) -> None:
    """Refleja la estación activa y la pestaña en la URL (?e=...&tab=...).

    Así la barra de direcciones siempre contiene un link compartible. Escribe
    solo cuando el valor cambia (asignar ``st.query_params`` no provoca rerun)
    y omite ``e`` para proveedores sin catálogo (WU/WeatherLink) para no dejar
    links rotos.
    """
    tab_slug = TAB_URL_SLUGS.get(active_tab, active_tab)
    if _query_param_value("tab") != tab_slug:
        st.query_params["tab"] = tab_slug
    st.session_state["_last_synced_tab_slug"] = tab_slug

    provider = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
    slug = _station_slug(st.session_state.get(PROVIDER_STATION_NAME, ""))
    connected = bool(st.session_state.get(CONNECTED))
    if connected and provider in DEEPLINK_PROVIDERS and slug:
        desired_e = f"{provider}~{slug}"
        if _query_param_value("e") != desired_e:
            st.query_params["e"] = desired_e
            st.session_state["_deeplink_consumed"] = desired_e
    elif "e" in st.query_params:
        del st.query_params["e"]


def _sync_active_tab_state() -> str:
    pending_tab = st.session_state.get("_pending_active_tab")
    if isinstance(pending_tab, str):
        pending_tab = LEGACY_TAB_ALIASES.get(pending_tab, pending_tab)
    if isinstance(pending_tab, str) and pending_tab in TAB_OPTIONS:
        st.session_state["active_tab"] = pending_tab
    if "_pending_active_tab" in st.session_state:
        del st.session_state["_pending_active_tab"]

    active_tab_state = st.session_state.get("active_tab")
    if isinstance(active_tab_state, str):
        st.session_state["active_tab"] = LEGACY_TAB_ALIASES.get(active_tab_state, active_tab_state)
    if st.session_state.get("active_tab") not in TAB_OPTIONS:
        # Pestaña por defecto en el primer arranque: si hay (auto)conexión, la de
        # Observación; si NO hay nada conectado, el Ranking (en vez de Observación
        # vacía con los datos en "—"). La autoconexión ya ha corrido en
        # render_sidebar() antes de esta función, así que CONNECTED es fiable.
        st.session_state["active_tab"] = "observation" if st.session_state.get(CONNECTED) else "ranking"
    return str(st.session_state.get("active_tab", TAB_OPTIONS[0]))


def _store_runtime_snapshot(**payload) -> None:
    st.session_state["_runtime_snapshot"] = dict(payload)


def _load_runtime_snapshot() -> dict:
    snapshot = st.session_state.get("_runtime_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _browser_viewport_width() -> int:
    """Devuelve el ancho CSS del viewport del navegador si está disponible."""
    cached = st.session_state.get("browser_viewport_width")
    try:
        return max(0, int(float(cached)))
    except (TypeError, ValueError):
        pass
    raw = st.query_params.get("_vw", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    try:
        return max(0, int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return 0


def _first_valid_float(*values: object, default: float = float("nan")) -> float:
    """Devuelve el primer float válido no-NaN de una lista heterogénea."""
    for value in values:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if not is_nan(candidate):
            return candidate
    return default


def _last_valid_float(values: object, default: float = float("nan")) -> float:
    if not isinstance(values, list):
        return default
    for value in reversed(values):
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if not is_nan(candidate):
            return candidate
    return default


def _is_small_mobile_client() -> bool:
    """Heurística de cliente móvil pequeño basada en viewport y user-agent."""
    viewport_width = _browser_viewport_width()
    if 0 < viewport_width <= 900:
        return True

    try:
        headers = getattr(st.context, "headers", {}) or {}
    except Exception:
        headers = {}

    user_agent = str(headers.get("user-agent", "")).lower()
    if not user_agent:
        return False

    mobile_tokens = (
        "iphone",
        "android",
        "mobile",
        "ipad",
        "ipod",
    )
    if any(token in user_agent for token in mobile_tokens):
        return True

    return 0 < viewport_width <= 1024 and any(token in user_agent for token in ("safari", "webkit"))


def _apply_compact_plotly_layout(fig) -> None:
    """Compacta un gráfico temporal Plotly con menos ruido visual."""
    has_polar = getattr(getattr(fig, "layout", None), "polar", None) is not None
    if has_polar:
        fig.update_layout(
            margin=dict(l=12, r=12, t=52, b=12),
            height=380,
            title=dict(font=dict(size=15)),
            legend=dict(font=dict(size=9)),
        )
        return

    current_height = getattr(getattr(fig, "layout", None), "height", None)
    target_height = 272 if current_height is None or current_height > 312 else current_height
    fig.update_layout(
        margin=dict(l=16, r=12, t=52, b=30),
        height=target_height,
        title=dict(font=dict(size=15)),
        legend=dict(font=dict(size=9)),
    )

    def _compact_xaxis(axis):
        axis_type = getattr(axis, "type", None)
        updates = {
            "title": None,
            "tickangle": 0,
            "automargin": False,
            "nticks": 4,
            "tickfont": dict(size=11),
            "ticklabeloverflow": "allow",
        }
        if axis_type == "date":
            updates["dtick"] = 3 * 60 * 60 * 1000
            updates["tickformat"] = "%H"
        axis.update(**updates)

    def _compact_yaxis(axis):
        axis.update(
            title=None,
            automargin=False,
            tickfont=dict(size=11),
            ticklabelposition="inside",
            ticklabelstandoff=-2,
        )

    fig.for_each_xaxis(_compact_xaxis)
    fig.for_each_yaxis(_compact_yaxis)


def _compact_plotly_for_mobile(fig) -> None:
    """Compacta gráficos Plotly en pantallas pequeñas."""
    if not _is_small_mobile_client():
        return
    _apply_compact_plotly_layout(fig)
    fig.update_layout(dragmode=False)
    fig.for_each_xaxis(lambda axis: axis.update(fixedrange=True))
    fig.for_each_yaxis(lambda axis: axis.update(fixedrange=True))


def _mobile_plotly_config(config: Optional[dict] = None) -> dict:
    """Devuelve una configuración táctil segura para móviles pequeños."""
    base = dict(config) if isinstance(config, dict) else {}
    if not _is_small_mobile_client():
        return base
    mobile_cfg = {
        "displayModeBar": False,
        "scrollZoom": False,
        "doubleClick": False,
        "showAxisDragHandles": False,
        "staticPlot": True,
        "responsive": True,
    }
    mobile_cfg.update(base)
    mobile_cfg.update({
        "displayModeBar": False,
        "scrollZoom": False,
        "doubleClick": False,
        "showAxisDragHandles": False,
        "staticPlot": True,
    })
    return mobile_cfg


def _register_plotly_templates() -> None:
    """Registra los templates ``meteolabx_light`` y ``meteolabx_dark`` en Plotly
    (idempotente). Se registran AMBOS y de forma eager para que cualquier
    referencia por nombre (p. ej. ``fig.update_layout(template="meteolabx_light")``
    en ``tabs.historical``) sea válida aunque ese sea el primer chart del run y
    aún no haya pasado por :func:`_plotly_chart_stretch`."""
    try:
        import plotly.io as pio
    except Exception:
        return

    if "meteolabx_dark" not in pio.templates:
        pio.templates["meteolabx_dark"] = pio.templates["plotly_dark"]
        pio.templates["meteolabx_dark"].layout.font.color = "rgba(255, 255, 255, 0.92)"
        pio.templates["meteolabx_dark"].layout.title.font.color = "rgba(255, 255, 255, 0.92)"
        pio.templates["meteolabx_dark"].layout.xaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
        pio.templates["meteolabx_dark"].layout.yaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
    if "meteolabx_light" not in pio.templates:
        pio.templates["meteolabx_light"] = pio.templates["plotly_white"]
        pio.templates["meteolabx_light"].layout.font.color = "rgba(15, 18, 25, 0.92)"
        pio.templates["meteolabx_light"].layout.title.font.color = "rgba(15, 18, 25, 0.92)"
        pio.templates["meteolabx_light"].layout.xaxis.title.font.color = "rgba(15, 18, 25, 0.92)"
        pio.templates["meteolabx_light"].layout.yaxis.title.font.color = "rgba(15, 18, 25, 0.92)"


def _ensure_plotly_template(fig) -> None:
    """Configura el template de Plotly bajo demanda, justo antes de renderizar."""
    try:
        import plotly.io as pio
    except Exception:
        return

    _register_plotly_templates()
    template_name = "meteolabx_dark" if dark else "meteolabx_light"
    pio.templates.default = template_name
    try:
        fig.update_layout(template=template_name)
    except Exception:
        pass


def _plotly_chart_stretch(fig, key: str, config: Optional[dict] = None, compact: bool = False):
    """Renderiza Plotly ocupando todo el ancho del contenedor."""
    _ensure_plotly_template(fig)
    if compact:
        _apply_compact_plotly_layout(fig)
    else:
        _compact_plotly_for_mobile(fig)
    cfg = _mobile_plotly_config(config)
    st.plotly_chart(fig, use_container_width=True, key=key, config=cfg)


def _render_neutral_info_note(message: str, title: Optional[str] = None) -> None:
    """Muestra una nota informativa neutra sin apariencia de error."""
    safe_message = html.escape(str(message))
    safe_title = html.escape(str(title or t("common.information")))
    st.markdown(
        html_clean(
            f"""
            <div style="
                margin: 0.3rem 0 0.95rem 0;
                padding: 0.9rem 1rem;
                border-radius: 14px;
                border: 1px solid rgba(127, 127, 127, 0.18);
                background: rgba(127, 127, 127, 0.08);
                color: var(--text);
                box-shadow: none;
            ">
                <div style="font-weight: 700; margin-bottom: 0.2rem;">{safe_title}</div>
                <div style="opacity: 0.88;">{safe_message}</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def _render_observation_warning(warning) -> str:
    """
    Traduce un aviso estructurado ``{"code", "params"}`` del pipeline/backend
    a texto localizado vía i18n (clave ``warnings.<code>``). Acepta también
    strings sueltos (los devuelve tal cual) por robustez.
    """
    if isinstance(warning, str):
        return warning
    if not isinstance(warning, dict):
        return str(warning)
    code = str(warning.get("code", "")).strip()
    if not code:
        return ""
    # Para estaciones MANUALES (COOP del NWS) el aviso de antigüedad sobra: el
    # cartel azul ya explica que solo publican una vez al día.
    if code == "data_age" and is_manual_iem_station(st.session_state):
        return ""
    params = warning.get("params")
    return t(f"warnings.{code}", **(params if isinstance(params, dict) else {}))


from utils.label_i18n import (  # noqa: E402
    translate_pressure_trend_label as _translate_pressure_trend_label,
    translate_rain_intensity_label as _translate_rain_intensity_label,
    translate_clarity_label as _translate_clarity_label,
    translate_balance_label as _translate_balance_label,
    translate_sunrise_sunset_label as _translate_sunrise_sunset_label,
)


def _pydeck_chart_stretch(deck, key: str, height: int = 900):
    """Renderiza pydeck de forma compatible entre versiones de Streamlit."""
    viewport_width = _browser_viewport_width()
    if 0 < viewport_width <= 520:
        height = min(int(height), 420)
    elif 0 < viewport_width <= 900:
        height = min(int(height), 540)
    try:
        return st.pydeck_chart(
            deck, use_container_width=True, height=int(height), key=key,
            on_select="rerun", selection_mode="single-object",
        )
    except TypeError:
        return st.pydeck_chart(deck, use_container_width=True, height=int(height), key=key)


# ============================================================
# PROCESAMIENTO ESTÁNDAR DE PROVEEDORES
# ============================================================

from domain.observation_pipeline import ProcessingContext as _PipelineContext, prepare_observation_effects
from frontend.dashboard_payload import DashboardPayload
from frontend.observation_effects import (
    ObservationEffectHandlers,
    apply_observation_effects,
)


def _apply_observation_side_effects(result) -> None:
    """
    Aplica los EFECTOS SECUNDARIOS del pipeline a ``st.session_state`` y la UI:
    metadata de sesión (lat/lon/elevación/estación), warnings y persistencia
    de series (chart + horaria) con sus owners.

    SEPARADO a propósito del cálculo de derivadas:
    estos efectos NO los produce el backend ``/processed`` —viven en el
    cliente (session_state y ownership de series), así que se
    aplican SIEMPRE y no dependen del modelo procesado que devuelve FastAPI.
    """
    handlers = ObservationEffectHandlers(
        render_warning=_render_observation_warning,
        emit_warning=st.warning,
        log_warning=logger.warning,
        store_series=store_series_state,
        set_chart_owner=_set_chart_series_owner,
        set_trend_hourly_owner=_set_trend_hourly_series_owner,
        clear_trend_hourly_owner=_clear_trend_hourly_series_owner,
    )
    apply_observation_effects(result, st.session_state, handlers)


def _remember_provider_catalog_altitude(provider_name: str, station_id: str, station: dict) -> None:
    """Persist catalog altitude from the backend station block for the active station."""
    prefix = coerce_str(provider_name, upper=True).lower()
    if not prefix or not station_id or not isinstance(station, dict):
        return
    elevation = _first_valid_float(station.get("elevation"), default=float("nan"))
    if is_nan(elevation):
        return
    st.session_state["provider_station_catalog_alt"] = elevation
    st.session_state["provider_station_catalog_station_id"] = station_id
    st.session_state[f"{prefix}_station_catalog_alt"] = elevation
    st.session_state[f"{prefix}_station_catalog_station_id"] = station_id


def process_standard_provider(
    dashboard: DashboardPayload,
    provider_name: str,
    elevation_fallback_key: str,
) -> dict:
    """
    Adapter backend-only para proveedores estándar.

    ``/processed`` es la única fuente de derivadas meteorológicas. El cliente
    solo prepara y aplica efectos propios de Streamlit (sesión, warnings,
    historial y ownership de series); nunca recalcula termodinámica, presión,
    radiación o ET0 como fallback.
    """
    observation = dict(dashboard.observation)
    station = dashboard.station
    for key in ("lat", "lon", "elevation"):
        current_value = observation.get(key)
        station_value = station.get(key)
        if (
            station_value is not None
            and station_value != ""
            and (
                current_value is None
                or current_value == ""
                or (isinstance(current_value, float) and math.isnan(current_value))
            )
        ):
            observation[key] = station_value
    ctx = _PipelineContext(
        provider_name=provider_name,
        elevation_fallback=float(
            _first_valid_float(
                station.get("elevation"),
                st.session_state.get(elevation_fallback_key, 0),
                default=0.0,
            )
        ),
        provider_for_pressure=str(
            st.session_state.get(CONNECTION_TYPE, provider_name) or provider_name
        ),
        sun_tz_name=str(
            station.get("tz")
            or st.session_state.get("provider_station_tz")
            or st.session_state.get("browser_tz")
            or ""
        ).strip(),
        max_data_age_minutes=MAX_DATA_AGE_MINUTES,
        series_override=dashboard.series,
        series_7d=dashboard.recent_series,
        owner_station_id=str(
            _get_provider_station_id(provider_name)
            or station.get("station_id", "")
        ).strip(),
        station_name=str(station.get("name") or "").strip(),
        station_tz=str(station.get("tz") or "").strip(),
    )
    effects = prepare_observation_effects(observation, ctx)
    _apply_observation_side_effects(effects)
    _remember_provider_catalog_altitude(provider_name, ctx.owner_station_id, station)

    for warning in dashboard.warnings:
        logger.info("Backend %s /processed warning: %s", provider_name, warning)

    return dashboard.derivatives


def _unpack_derivatives(r: dict) -> tuple:
    """Read the canonical derivatives block for the module-level render state."""
    def number(key: str) -> float:
        return _first_valid_float(r.get(key))

    return (
        number("z"), number("p_abs"), number("p_msl"),
        str(r.get("p_abs_disp") or "—"), str(r.get("p_msl_disp") or "—"),
        number("dp3"), number("rate_h"), str(r.get("p_label") or "—"),
        str(r.get("p_arrow") or "•"), number("inst_mm_h"), number("r5_mm_h"),
        number("r10_mm_h"), str(r.get("inst_label") or "Sin precipitación"),
        number("e_sat"), number("e"), number("Td_calc"), number("Tw"),
        number("q"), number("q_gkg"), number("theta"), number("Tv"),
        number("Te"), number("rho"), number("rho_v_gm3"), number("lcl"),
        number("solar_rad"), number("uv"), number("et0"), number("clarity"),
        number("balance"), bool(r.get("has_radiation")), bool(r.get("has_chart_data")),
    )


def _infer_series_step_minutes(times_like) -> int:
    try:
        import pandas as pd
        times = pd.to_datetime(times_like, errors="coerce")
        if isinstance(times, pd.Series):
            times_series = times.dropna().sort_values().reset_index(drop=True)
        else:
            times_series = (
                pd.Series(pd.DatetimeIndex(times))
                .dropna()
                .sort_values()
                .reset_index(drop=True)
            )
        if len(times_series) < 2:
            return 0
        diffs = times_series.diff().dropna().dt.total_seconds() / 60.0
        diffs = diffs[diffs > 0]
        if diffs.empty:
            return 0
        return int(round(float(diffs.median())))
    except Exception:
        return 0


# ============================================================
# SIDEBAR Y TEMA
# ============================================================

_boot_mark("about to sync browser context")
sync_browser_context_early()
_boot_mark("sync_browser_context_early")
hydrate_browser_context_live(get_browser_context)
_boot_mark("hydrate_browser_context_live")
theme_mode, dark = render_sidebar()
_boot_mark("render_sidebar")
active_tab = _sync_active_tab_state()
_boot_mark("_sync_active_tab_state")
if st.query_params.get("rank_connect"):
    _get_tab_module().handle_rank_connect_query(_build_ranking_tab_context())
    _boot_mark("handle_rank_connect_query")
_handle_station_deeplink()
_boot_mark("_handle_station_deeplink")
unit_preferences = normalize_unit_preferences(st.session_state.get("unit_preferences"))
temp_unit_pref = unit_preferences["temperature"]
wind_unit_pref = unit_preferences["wind"]
pressure_unit_pref = unit_preferences["pressure"]
precip_unit_pref = unit_preferences["precip"]
radiation_unit_pref = unit_preferences["radiation"]

temp_unit_txt = temperature_unit_label(temp_unit_pref)
wind_unit_txt = wind_unit_label(wind_unit_pref)
pressure_unit_txt = pressure_unit_label(pressure_unit_pref)
precip_unit_txt = precip_unit_label(precip_unit_pref)
radiation_unit_txt = radiation_unit_label(radiation_unit_pref)
radiation_energy_unit_txt = radiation_energy_unit_label(radiation_unit_pref)


def _fmt_temp_display(value, decimals: int = 1) -> str:
    return format_temperature(value, temp_unit_pref, decimals=decimals)


def _fmt_wind_display(value, decimals: int = 1) -> str:
    return format_wind(value, wind_unit_pref, decimals=decimals)


def _fmt_pressure_display(value, decimals: int = 1) -> str:
    return format_pressure(value, pressure_unit_pref, decimals=decimals)


def _fmt_precip_display(value, decimals: int = 1) -> str:
    return format_precip(value, precip_unit_pref, decimals=decimals)


def _fmt_radiation_display(value, decimals: int = 0) -> str:
    return format_radiation(value, radiation_unit_pref, decimals=decimals)


def _fmt_radiation_energy_display(value, decimals: int = 2) -> str:
    return format_radiation_energy(value, radiation_unit_pref, decimals=decimals)


def _render_theme_table(df, table_class: str = "mlbx-data-table") -> None:
    """Renderiza una tabla HTML simple para respetar el tema claro/oscuro."""
    try:
        styled_df = df.copy()
    except Exception:
        styled_df = df
    try:
        html_table = styled_df.to_html(index=False, classes=table_class, border=0)
    except Exception:
        st.dataframe(df, width="stretch", hide_index=True)
        return
    st.markdown(
        html_clean(f"<div class='mlbx-table-wrap'>{html_table}</div>"),
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_map_search_nearby_stations(
    lat: float,
    lon: float,
    max_results: int,
    provider_ids: tuple[str, ...],
    countries: tuple[str, ...] = (),
    catalog_version: tuple[tuple[str, int], ...] = (),
    has_historical: bool = False,
    hide_historical_only: bool = False,
):
    """Cache corto para que cambiar el tema no dispare de nuevo toda la búsqueda del mapa."""
    from providers import search_nearby_stations

    return search_nearby_stations(
        lat,
        lon,
        max_results=max_results,
        provider_ids=list(provider_ids),
        countries=list(countries),
        has_historical=bool(has_historical),
        hide_historical_only=bool(hide_historical_only),
    )

# CSS para sidebar y botones
sidebar_bg = "#f4f6fb" if not dark else "#262730"
sidebar_text = "rgb(15, 18, 25)" if not dark else "rgb(250, 250, 250)"
button_bg = "#ffffff" if not dark else "#0e1117"
button_text = "rgb(15, 18, 25)" if not dark else "rgb(250, 250, 250)"
button_border = "rgba(180, 180, 180, 0.55)" if not dark else "rgba(120, 126, 138, 0.55)"
button_border_width = "1px"
eye_color = "rgba(0, 0, 0, 0.5)" if not dark else "rgba(255, 255, 255, 0.8)"
eye_color_hover = "rgba(0, 0, 0, 0.7)" if not dark else "rgba(255, 255, 255, 1)"
theme_color_scheme = "light" if not dark else "dark"
expander_bg = "rgba(255,255,255,0.45)" if not dark else "rgba(22,25,31,0.45)"
expander_summary_bg = "rgba(255,255,255,0.85)" if not dark else "rgba(17,22,30,0.92)"

sidebar_css_hash = hashlib.md5(f"sidebar-{theme_color_scheme}-{sidebar_bg}-{button_bg}-{button_border}".encode()).hexdigest()[:8]

st.markdown(f"""
<style data-sidebar-theme="{sidebar_css_hash}" data-mlbx-layout-hidden="sidebar-theme">
/* Forzar tema de sidebar */
[data-testid="stSidebar"] {{
    background-color: {sidebar_bg} !important;
    color-scheme: {theme_color_scheme} !important;
    --mlbx-control-bg: {'#ffffff' if not dark else '#0e1117'};
    --mlbx-control-bg-hover: {'#f3f5fa' if not dark else '#141821'};
    --mlbx-control-border: {button_border};
    --mlbx-sidebar-text: {sidebar_text};
    --mlbx-sidebar-muted: {'rgba(45, 52, 66, 0.58)' if not dark else 'rgba(232, 238, 248, 0.62)'};
    --mlbx-sidebar-tooltip-bg: {'rgba(255, 255, 255, 0.92)' if not dark else 'rgba(14, 17, 23, 0.88)'};
}}

[data-testid="stSidebar"],
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] li,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] h5,
[data-testid="stSidebar"] h6 {{
    color: var(--mlbx-sidebar-text) !important;
}}

/* Excepción: banners de estado con color tintado propio */
[data-testid="stSidebar"] .mlbx-status-banner,
[data-testid="stSidebar"] .mlbx-status-banner * {{
    color: var(--mlbx-banner-fg) !important;
    font-weight: 500 !important;
}}

[data-testid="stSidebar"] label {{
    color: {sidebar_text} !important;
}}

[data-testid="stSidebar"] input[type="text"],
[data-testid="stSidebar"] input[type="password"],
[data-testid="stSidebar"] input[type="number"],
[data-testid="stSidebar"] textarea {{
    color: {sidebar_text} !important;
    background-color: var(--mlbx-control-bg) !important;
}}

/* Contenedor de inputs en sidebar (incluye zona del ojo y +/-) */
[data-testid="stSidebar"] [data-baseweb="input"] {{
    background-color: var(--mlbx-control-bg) !important;
    border-color: {button_border} !important;
}}

/* Selectbox de sidebar: forzar fondo y texto del control visible */
[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
    background: var(--mlbx-control-bg) !important;
    border: {button_border_width} solid var(--mlbx-control-border) !important;
    color: var(--mlbx-sidebar-text) !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] div,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] div {{
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [data-testid="stSelectbox"] svg,
[data-testid="stSidebar"] [data-testid="stSelectbox"] svg path,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] svg,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] svg path {{
    fill: var(--mlbx-sidebar-text) !important;
    stroke: var(--mlbx-sidebar-text) !important;
}}

/* Iconos de ayuda (?) que aparecen junto al label de los widgets cuando se
   pasa `help="..."`. Solo usamos data-testid específicos para no afectar
   accidentalmente a otros componentes del sidebar (toggles, inputs...). El
   `*` y los selectores [class*="..."] tan amplios que tenía antes podían
   propagar `color: inherit` a inputs y dejar el texto invisible, lo que
   hacía parecer que las credenciales se habían borrado. */
[data-testid="stSidebar"] [data-testid="stTooltipIcon"],
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] {{
    background: transparent !important;
    color: var(--mlbx-sidebar-muted) !important;
    opacity: 0.78;
    transition: opacity 0.15s ease;
    box-shadow: none !important;
    border: 0 !important;
}}

[data-testid="stSidebar"] [data-testid="stTooltipIcon"]:hover,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"]:hover {{
    opacity: 1;
}}

[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] svg {{
    color: var(--mlbx-sidebar-muted) !important;
    fill: transparent !important;
    stroke: var(--mlbx-sidebar-muted) !important;
    background: transparent !important;
}}

[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg circle,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] svg circle {{
    fill: transparent !important;
    stroke: var(--mlbx-sidebar-muted) !important;
}}

[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg path,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] svg path {{
    fill: var(--mlbx-sidebar-muted) !important;
    stroke: var(--mlbx-sidebar-muted) !important;
}}

[data-testid="stTooltipContent"],
[data-baseweb="tooltip"] {{
    background: var(--mlbx-sidebar-tooltip-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border: 1px solid var(--mlbx-control-border) !important;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.18) !important;
}}

/* Botón del ojo de la API key (evitar cuadro negro) */
[data-testid="stSidebar"] [data-testid="stTextInput"] button {{
    background: var(--mlbx-control-bg) !important;
    border: 0 !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button svg,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg path,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg circle,
[data-testid="stSidebar"] [data-testid="stTextInput"] button svg rect {{
    color: {eye_color} !important;
    fill: {eye_color} !important;
    stroke: {eye_color} !important;
}}

[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg path,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg circle,
[data-testid="stSidebar"] [data-testid="stTextInput"] button:hover svg rect {{
    color: {eye_color_hover} !important;
    fill: {eye_color_hover} !important;
    stroke: {eye_color_hover} !important;
}}

/* Líneas separadoras visibles */
[data-testid="stSidebar"] hr {{
    border-color: {'rgba(0, 0, 0, 0.12)' if not dark else 'rgba(255, 255, 255, 0.12)'} !important;
    border-width: 1px !important;
    margin: 1rem 0 !important;
}}

/* Botones principales de la sidebar (Guardar, Conectar, etc.) - bordes visibles */
[data-testid="stSidebar"] div[data-testid="stButton"] > button {{
    background-color: {button_bg} !important;
    color: {sidebar_text} !important;
    border: {button_border_width} solid {button_border} !important;
}}

[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {{
    background-color: {'#e8ecf3' if not dark else '#1f2229'} !important;
    border-color: {'rgba(100, 100, 100, 0.9)' if not dark else 'rgba(150, 150, 150, 0.9)'} !important;
}}

/* Checkbox */
[data-testid="stSidebar"] [data-testid="stCheckbox"] {{
    color: {sidebar_text} !important;
}}

/* Radios y toggles en sidebar: forzar esquema de color dinámico */
[data-testid="stSidebar"] input[type="radio"],
[data-testid="stSidebar"] input[type="checkbox"] {{
    color-scheme: {theme_color_scheme} !important;
}}

/* Ocultar el control nativo y dejar visible el indicador custom del label */
[data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"],
[data-testid="stSidebar"] [data-testid="stCheckbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    pointer-events: none !important;
}}

/* Radios del tema: forzar colores para que cambien al alternar claro/oscuro */
[data-testid="stSidebar"] input[type="radio"] {{
    -webkit-appearance: none !important;
    appearance: none !important;
    accent-color: #ff4b4b !important;
    width: 0.95rem !important;
    height: 0.95rem !important;
    border-radius: 999px !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    box-shadow: inset 0 0 0 0.24rem transparent !important;
}}

[data-testid="stSidebar"] input[type="radio"]:checked {{
    border-color: #ff4b4b !important;
    box-shadow: inset 0 0 0 0.24rem #ff4b4b !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
}}

[data-testid="stSidebar"] input[type="checkbox"] {{
    -webkit-appearance: none !important;
    appearance: none !important;
    accent-color: #ff4b4b !important;
    width: 1.0rem !important;
    height: 1.0rem !important;
    border-radius: 0.22rem !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] input[type="checkbox"]:checked {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Radios de Streamlit/BaseWeb en sidebar: círculo visible */
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {{
    width: 0.95rem !important;
    height: 0.95rem !important;
    border-radius: 999px !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) > div:first-child {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-color: #ff4b4b !important;
    box-shadow: inset 0 0 0 0.24rem #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child * {{
    color: transparent !important;
    fill: transparent !important;
    stroke: transparent !important;
}}

/* Checkbox de Streamlit/BaseWeb en sidebar: cuadrado visible */
[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child {{
    width: 1.0rem !important;
    height: 1.0rem !important;
    border-radius: 0.22rem !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child svg,
[data-testid="stSidebar"] [data-testid="stCheckbox"] label > div:first-child svg path {{
    fill: {'#ffffff' if not dark else '#ffffff'} !important;
    stroke: {'#ffffff' if not dark else '#ffffff'} !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-child::after {{
    content: "✓";
    color: #ffffff;
    display: block;
    text-align: center;
    line-height: 1rem;
    font-size: 0.8rem;
    font-weight: 700;
}}

/* Radios del selector de tema cuando Streamlit/BaseWeb los renderiza como círculos custom */
[data-testid="stSidebar"] [role="radiogroup"] [role="radio"] {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border: 1px solid {button_border} !important;
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [role="radiogroup"] [role="radio"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [role="radiogroup"] [role="radio"] *,
[data-testid="stSidebar"] [role="radiogroup"] label * {{
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [role="checkbox"] {{
    width: 1.05rem !important;
    height: 1.05rem !important;
    border: 1px solid {button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-radius: 0.25rem !important;
}}

[data-testid="stSidebar"] [role="checkbox"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggle de sidebar (switch) visible en claro/oscuro */
[data-testid="stSidebar"] [data-baseweb="switch"] input + div {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {button_border} !important;
}}

[data-testid="stSidebar"] [data-baseweb="switch"] input + div > div {{
    background-color: {'#ffffff' if not dark else '#dbe4f2'} !important;
}}

[data-testid="stSidebar"] [data-baseweb="switch"] input:checked + div {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [role="switch"] {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {button_border} !important;
    border-radius: 999px !important;
}}

[data-testid="stSidebar"] [role="switch"][aria-checked="true"] {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Segmented control real de Streamlit en sidebar */
[data-testid="stSidebar"] [data-testid="stButtonGroup"] {{
    width: 100% !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] {{
    width: 100% !important;
    background: var(--mlbx-control-bg) !important;
    border: 1px solid var(--mlbx-control-border) !important;
    border-radius: 0.95rem !important;
    overflow: hidden !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
    font-weight: 600 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"] {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
    font-weight: 600 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
    color: var(--mlbx-sidebar-text) !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"],
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"],
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] {{
    background: #ff4b4b !important;
    color: #ffffff !important;
    border-color: #ff4b4b !important;
    font-weight: 700 !important;
    box-shadow: inset 0 0 0 1px #ff4b4b !important;
    z-index: 2 !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"]:hover {{
    background: #ff5f5f !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"]:hover {{
    background: #ff5f5f !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"]:hover,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"]:hover,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"]:hover {{
    background: #ff5f5f !important;
}}

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-pressed="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-selected="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-selected="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] p {{
    color: #ffffff !important;
}}

/* Toggle real de Streamlit/BaseWeb en sidebar */
[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] {{
    color: var(--mlbx-sidebar-text) !important;
    background: transparent !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child {{
    background: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid var(--mlbx-control-border) !important;
    border-radius: 999px !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child > div {{
    background: {'#ffffff' if not dark else '#dbe4f2'} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    appearance: auto !important;
    -webkit-appearance: auto !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;
}}

[data-testid="stSidebar"] [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] {{
    color: var(--mlbx-sidebar-text) !important;
}}

/* Botones +/- de calibración / number_input */
[data-testid="stSidebar"] [data-testid="stNumberInput"] button {{
    background: var(--mlbx-control-bg) !important;
    color: var(--mlbx-sidebar-text) !important;
    border-color: var(--mlbx-control-border) !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stNumberInput"] button:hover {{
    background: var(--mlbx-control-bg-hover) !important;
}}

[data-testid="stSidebar"] [data-testid="stNumberInput"] button svg,
[data-testid="stSidebar"] [data-testid="stNumberInput"] button svg path {{
    fill: var(--mlbx-sidebar-text) !important;
    stroke: var(--mlbx-sidebar-text) !important;
}}
</style>
""", unsafe_allow_html=True)

sidebar_control_bg = '#ffffff' if not dark else '#0e1117'
sidebar_toggle_bg = '#d7dbe4' if not dark else '#1f2734'

components.html(f"""
<script>
(function() {{
  const host = window.parent || window;
  const doc = host.document;
  const SIDEBAR_BG = "{sidebar_control_bg}";
  const SIDEBAR_BORDER = "{button_border}";
  const SIDEBAR_TEXT = "{sidebar_text}";
  const TOGGLE_BG = "{sidebar_toggle_bg}";
  const ACTIVE = "#ff4b4b";
  const MAP_BG = "{button_bg}";
  const MAP_BORDER = "{button_border}";
  const MAP_TEXT = "{'rgba(15, 18, 25, 0.92)' if not dark else 'rgba(255, 255, 255, 0.92)'}";
  const MAP_HOVER = "{'#eef2f8' if not dark else '#1b2230'}";
  const IS_DARK = {str(bool(dark)).lower()};

  function paintSidebarControls() {{
    const sidebar = doc.querySelector('[data-testid="stSidebar"]');
    if (!sidebar) return;

    sidebar.querySelectorAll('[data-testid="stButtonGroup"] [data-baseweb="button-group"]').forEach((group) => {{
      group.style.width = "100%";
      group.style.background = SIDEBAR_BG;
      group.style.border = "1px solid " + SIDEBAR_BORDER;
      group.style.borderRadius = "0.95rem";
      group.style.overflow = "hidden";
      group.style.boxShadow = "none";

      group.querySelectorAll('[role="radio"], button').forEach((btn) => {{
        const checked = btn.getAttribute('aria-checked') === 'true'
          || btn.getAttribute('aria-pressed') === 'true'
          || btn.getAttribute('aria-selected') === 'true'
          || btn.getAttribute('data-selected') === 'true'
          || btn.getAttribute('data-testid') === 'stBaseButton-segmented_controlActive';
        btn.style.background = checked ? ACTIVE : SIDEBAR_BG;
        btn.style.color = checked ? "#ffffff" : SIDEBAR_TEXT;
        btn.style.borderColor = checked ? ACTIVE : SIDEBAR_BORDER;
        btn.style.boxShadow = checked ? "inset 0 0 0 1px " + ACTIVE : "none";
        btn.style.fontWeight = checked ? "700" : "600";
        btn.querySelectorAll('*').forEach((node) => {{
          node.style.color = checked ? "#ffffff" : SIDEBAR_TEXT;
          node.style.fill = checked ? "#ffffff" : "";
          node.style.stroke = checked ? "#ffffff" : "";
        }});
      }});
    }});

    sidebar.querySelectorAll('input[type="radio"]').forEach((input) => {{
      if (input.closest('[data-baseweb="button-group"]')) return;
      const label = input.closest('label');
      const bubble = label && label.children && label.children[0] ? label.children[0] : null;
      input.style.webkitAppearance = "none";
      input.style.appearance = "none";
      input.style.width = "0.95rem";
      input.style.height = "0.95rem";
      input.style.borderRadius = "999px";
      input.style.border = "1px solid " + SIDEBAR_BORDER;
      input.style.background = SIDEBAR_BG;
      input.style.boxShadow = input.checked ? "inset 0 0 0 0.24rem #ff4b4b" : "none";
      if (!bubble) return;
      bubble.style.width = "0.95rem";
      bubble.style.height = "0.95rem";
      bubble.style.borderRadius = "999px";
      bubble.style.border = "1px solid " + SIDEBAR_BORDER;
      bubble.style.background = SIDEBAR_BG;
      bubble.style.boxShadow = input.checked ? "inset 0 0 0 0.24rem #ff4b4b" : "none";
      bubble.style.color = "transparent";
      bubble.querySelectorAll('*').forEach((node) => {{
        node.style.color = "transparent";
        node.style.fill = "transparent";
        node.style.stroke = "transparent";
      }});
    }});

    sidebar.querySelectorAll('input[type="checkbox"]').forEach((input) => {{
      if (input.closest('[data-baseweb="checkbox"]')) return;
      const label = input.closest('label');
      const box = label && label.children && label.children[0] ? label.children[0] : null;
      input.style.webkitAppearance = "none";
      input.style.appearance = "none";
      input.style.width = "1rem";
      input.style.height = "1rem";
      input.style.borderRadius = "0.22rem";
      input.style.border = "1px solid " + (input.checked ? ACTIVE : SIDEBAR_BORDER);
      input.style.background = input.checked ? ACTIVE : SIDEBAR_BG;
      if (!box) return;
      box.style.width = "1rem";
      box.style.height = "1rem";
      box.style.borderRadius = "0.22rem";
      box.style.border = "1px solid " + (input.checked ? ACTIVE : SIDEBAR_BORDER);
      box.style.background = input.checked ? ACTIVE : SIDEBAR_BG;
      box.querySelectorAll('svg, svg *').forEach((node) => {{
        node.style.fill = "#ffffff";
        node.style.stroke = "#ffffff";
      }});
    }});

    sidebar.querySelectorAll('[data-baseweb="switch"]').forEach((sw) => {{
      const knobTrack = sw.querySelector('label > div, div[role="switch"]');
      const roleSwitch = sw.querySelector('[role="switch"]');
      const input = sw.querySelector('input[type="checkbox"]');
      const checked = !!(input && input.checked) || (roleSwitch && roleSwitch.getAttribute('aria-checked') === 'true');
      const track = roleSwitch || knobTrack;
      if (track) {{
        track.style.background = checked ? ACTIVE : TOGGLE_BG;
        track.style.border = "1px solid " + (checked ? ACTIVE : SIDEBAR_BORDER);
        track.style.borderRadius = "999px";
      }}
      const knob = sw.querySelector('label > div > div');
      if (knob) {{
        knob.style.background = "#ffffff";
      }}
    }});
  }}

  function paintMapControls() {{
    doc.querySelectorAll('.mapboxgl-ctrl-group, .maplibregl-ctrl-group').forEach((group) => {{
      group.style.background = MAP_BG;
      group.style.border = "1px solid " + MAP_BORDER;
      group.style.borderRadius = "12px";
      group.style.overflow = "hidden";
      group.style.boxShadow = "0 10px 24px rgba(0,0,0," + (IS_DARK ? "0.28" : "0.12") + ")";
      group.style.filter = "none";

      group.querySelectorAll('button').forEach((btn, index) => {{
        btn.style.background = MAP_BG;
        btn.style.color = MAP_TEXT;
        btn.style.width = "38px";
        btn.style.height = "38px";
        btn.style.borderBottom = index === group.querySelectorAll('button').length - 1 ? "none" : ("1px solid " + MAP_BORDER);
      }});

      group.querySelectorAll('.mapboxgl-ctrl-icon, .maplibregl-ctrl-icon').forEach((icon) => {{
        icon.style.filter = IS_DARK ? "brightness(0) invert(1)" : "none";
      }});
    }});
  }}

  function scheduleThemePaint() {{
    if (host.__mlbxSidebarThemeRaf) return;
    host.__mlbxSidebarThemeRaf = host.requestAnimationFrame(() => {{
      host.__mlbxSidebarThemeRaf = null;
      paintSidebarControls();
      paintMapControls();
    }});
  }}

  function bootstrapThemePaint(attempts) {{
    scheduleThemePaint();
    if (attempts <= 0) return;
    host.setTimeout(() => bootstrapThemePaint(attempts - 1), 300);
  }}

  bootstrapThemePaint(10);

  if (!host.__mlbxSidebarThemeEventsBound) {{
    host.__mlbxSidebarThemeEventsBound = true;
    host.addEventListener("resize", scheduleThemePaint, {{ passive: true }});
    host.addEventListener("pageshow", scheduleThemePaint, {{ passive: true }});
    doc.addEventListener("click", scheduleThemePaint, {{ passive: true }});
  }}
}})();
</script>
""", height=0, width=0)


# ============================================================
# CSS (CLARO / OSCURO)
# ============================================================

if not dark:
    css = html_clean("""
    <style data-mlbx-layout-hidden="theme-vars">
      :root{
        --bg: #f4f6fb;
        --panel: rgba(255,255,255,0.85);
        --border: rgba(18, 18, 18, 0.08);
        --shadow: 0 10px 24px rgba(0,0,0,0.08);
        --text: rgba(15,18,25,0.92);
        --muted: rgba(15,18,25,0.55);
        --accent: rgba(35, 132, 255, 0.20);
      }
      .stApp{
        color-scheme: light;
        background: radial-gradient(circle at 15% 10%, #ffffff 0%, var(--bg) 50%, #eef2fb 100%);
      }
    </style>
    """)
else:
    css = html_clean("""
    <style data-mlbx-layout-hidden="theme-vars">
      :root{
        --bg: #0f1115;
        --panel: rgba(22, 25, 31, 0.78);
        --border: rgba(255,255,255,0.10);
        --shadow: 0 12px 26px rgba(0,0,0,0.50);
        --text: rgba(255,255,255,0.92);
        --muted: rgba(255,255,255,0.62);
        --accent: rgba(120, 180, 255, 0.12);
      }
      .stApp{
        color-scheme: dark;
        background: radial-gradient(circle at 15% 10%, #2a2f39 0%, #14171d 55%, #0f1115 100%);
      }
    </style>
    """)

st.markdown(css, unsafe_allow_html=True)

# CSS adicional para forzar colores en headers de Streamlit
main_button_bg = "#ffffff" if not dark else "rgba(22, 25, 31, 0.88)"
main_button_text = "rgba(15, 18, 25, 0.92)" if not dark else "rgba(255, 255, 255, 0.92)"
main_button_border = "rgba(18, 18, 18, 0.22)" if not dark else "rgba(255, 255, 255, 0.22)"
main_hr_color = "rgba(18, 18, 18, 0.16)" if not dark else "rgba(255, 255, 255, 0.16)"

st.markdown(f"""
<style data-mlbx-layout-hidden="global-theme">
[data-testid="stDecoration"] {{
    display: none !important;
}}

/* Barra superior de Streamlit */
[data-testid="stHeader"] {{
    background: {sidebar_bg} !important;
    border-bottom: 1px solid {main_hr_color} !important;
    color-scheme: {theme_color_scheme} !important;
}}

[data-testid="stHeader"] *,
[data-testid="stToolbar"] *,
[data-testid="stHeader"] svg,
[data-testid="stToolbar"] svg,
[data-testid="stHeader"] svg path,
[data-testid="stToolbar"] svg path {{
    color: {main_button_text} !important;
    fill: {main_button_text} !important;
    stroke: {main_button_text} !important;
}}

[data-testid="stToolbar"] {{
    background: transparent !important;
}}

[data-testid="stToolbar"] button,
[data-testid="stHeader"] button,
[data-testid="collapsedControl"] {{
    background: {main_button_bg} !important;
    color: {main_button_text} !important;
    border: 1px solid {main_button_border} !important;
    box-shadow: none !important;
}}

[data-testid="stToolbar"] button:hover,
[data-testid="stHeader"] button:hover,
[data-testid="collapsedControl"]:hover {{
    background: {'#eef2f8' if not dark else '#1b2230'} !important;
}}

/* Mantener visible el botón para desplegar sidebar cuando está colapsada */
button[data-testid="collapsedControl"] {{
    display: flex !important;
}}

/* Ocultar solo el menú de Streamlit (tres puntos), sin tocar el control de sidebar */
#MainMenu {{
    visibility: hidden !important;
}}

[data-testid="stToolbar"] button[aria-label="Main menu"],
[data-testid="stToolbar"] button[title="Main menu"],
[data-testid="stToolbar"] button[aria-haspopup="menu"]:not([data-testid="collapsedControl"]) {{
    display: none !important;
}}

/* Ocultar indicador de ejecución/stop de Streamlit: evita la "bicicleta" en móvil */
[data-testid="stStatusWidget"],
[data-testid="stStatusWidget"] *,
.stStatusWidget,
.stStatusWidget * {{
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}}

@media (max-width: 900px) {{
    [data-testid="stPlotlyChart"],
    [data-testid="stPlotlyChart"] .js-plotly-plot,
    [data-testid="stPlotlyChart"] .plot-container,
    [data-testid="stPlotlyChart"] .svg-container {{
        max-width: 100% !important;
        overflow: hidden !important;
        touch-action: pan-y !important;
    }}

    [data-testid="stPlotlyChart"] .draglayer,
    [data-testid="stPlotlyChart"] .nsewdrag,
    [data-testid="stPlotlyChart"] .drag {{
        pointer-events: none !important;
    }}
}}

/* Texto del contenido principal dependiente de tema */
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] li,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] span,
[data-testid="stMainBlockContainer"] [data-testid="stText"] {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] {{
    color: var(--muted) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMetricLabel"] > div,
[data-testid="stMainBlockContainer"] [data-testid="stMetricValue"] > div,
[data-testid="stMainBlockContainer"] [data-testid="stMetricDelta"] > div {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMetricLabel"] {{
    opacity: 0.72;
}}

/* Botones secundarios del contenido principal (sin tocar CTA primario rojo) */
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="secondary"],
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="tertiary"] {{
    background: {main_button_bg} !important;
    color: {main_button_text} !important;
    border: 1px solid {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="secondary"]:hover,
[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="tertiary"]:hover {{
    filter: brightness(0.97);
}}

/* Mantener texto correcto dentro de botones (evitar herencia global oscura) */
[data-testid="stMainBlockContainer"] button [data-testid="stMarkdownContainer"] p,
[data-testid="stMainBlockContainer"] button [data-testid="stMarkdownContainer"] span {{
    color: inherit !important;
}}

/* Separadores en contenido principal */
[data-testid="stMainBlockContainer"] hr {{
    border-color: {main_hr_color} !important;
}}

/* Expander de búsqueda manual: borde/contorno visible en ambos temas */
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] {{
    border: 1px solid {main_button_border} !important;
    border-radius: 12px !important;
    background: {expander_bg} !important;
    color-scheme: {theme_color_scheme} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] details,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] > div {{
    background: {expander_bg} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary {{
    background: {expander_summary_bg} !important;
    border-radius: 10px !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary p,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary span {{
    color: var(--text) !important;
}}

/* Inputs dentro del expander: respetar tema claro/oscuro */
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stTextInput"] input,
[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stNumberInput"] input {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(255,255,255,0.92)'} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-baseweb="input"] {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-color: {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stNumberInput"] button {{
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(255,255,255,0.92)'} !important;
    border-color: {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stExpander"] label {{
    color: var(--text) !important;
}}

/* Selectores (multiselect de Mapa/Filtros y otros) siguiendo tema activo */
[data-testid="stMainBlockContainer"] [data-baseweb="select"] > div {{
    background: {main_button_bg} !important;
    border-color: {main_button_border} !important;
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="select"] input {{
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="tag"] {{
    border-color: {main_button_border} !important;
}}

/* Multiselect de filtros (Mapa): forzar fondo/contraste correctos */
[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
    background: {main_button_bg} !important;
    border-color: {main_button_border} !important;
    color: var(--text) !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] [data-baseweb="tag"] {{
    background: {'rgba(255,75,75,0.95)' if not dark else 'rgba(255,75,75,0.95)'} !important;
    color: #ffffff !important;
    border-color: transparent !important;
}}

[data-baseweb="popover"],
body [data-baseweb="popover"] {{
    color-scheme: {theme_color_scheme} !important;
}}

[data-baseweb="popover"] [role="listbox"],
body [role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
    border: 1px solid {main_button_border} !important;
}}

[data-baseweb="popover"] [role="option"],
body [role="option"],
[data-baseweb="popover"] li,
[data-baseweb="menu"] li {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
}}

[data-baseweb="popover"] [role="option"]:hover,
body [role="option"]:hover,
[data-baseweb="popover"] li:hover,
[data-baseweb="menu"] li:hover {{
    background: {'#eef2f8' if not dark else '#1b2230'} !important;
}}

/* Tablas HTML tematizadas */
.mlbx-table-wrap {{
    width: 100%;
    overflow-x: auto;
    margin: 0.25rem 0 1rem 0;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    box-shadow: var(--shadow);
}}

.mlbx-data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.95rem;
    color: var(--text);
}}

.mlbx-data-table thead th {{
    text-align: left;
    padding: 0.72rem 0.78rem;
    background: {'rgba(233,237,243,0.95)' if not dark else 'rgba(42, 46, 56, 0.96)'};
    color: var(--text);
    border-bottom: 1px solid var(--border);
    font-weight: 700;
}}

.mlbx-data-table tbody td {{
    padding: 0.58rem 0.78rem;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    background: transparent;
}}

.mlbx-data-table tbody tr:last-child td {{
    border-bottom: 0;
}}

[data-testid="stMainBlockContainer"] [role="checkbox"] {{
    width: 1.05rem !important;
    height: 1.05rem !important;
    border: 1px solid {main_button_border} !important;
    background: {'#ffffff' if not dark else '#0e1117'} !important;
    border-radius: 0.25rem !important;
}}

[data-testid="stMainBlockContainer"] [role="checkbox"][aria-checked="true"] {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggles del contenido principal (estaciones cercanas / mapa) */
[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input + div {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input + div > div {{
    background-color: {'#ffffff' if not dark else '#dbe4f2'} !important;
}}

[data-testid="stMainBlockContainer"] [data-baseweb="switch"] input:checked + div {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stMainBlockContainer"] [role="switch"] {{
    background-color: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 999px !important;
}}

[data-testid="stMainBlockContainer"] [role="switch"][aria-checked="true"] {{
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

/* Toggle real de Streamlit/BaseWeb en contenido principal (mapa) */
[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] {{
    color: var(--text) !important;
    background: transparent !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child {{
    background: {'#d7dbe4' if not dark else '#1f2734'} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 999px !important;
    box-shadow: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child > div {{
    background: {'#ffffff' if not dark else '#dbe4f2'} !important;
    box-shadow: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child {{
    background: #ff4b4b !important;
    border-color: #ff4b4b !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] input[type="checkbox"] {{
    position: absolute !important;
    opacity: 0 !important;
    width: 1px !important;
    height: 1px !important;
    appearance: auto !important;
    -webkit-appearance: auto !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;
}}

[data-testid="stMainBlockContainer"] [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] {{
    color: var(--text) !important;
}}

/* Mapa: controles de zoom y chips de metadatos */
[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group,
.stApp .mapboxgl-ctrl-group,
.stApp .maplibregl-ctrl-group {{
    background: {main_button_bg} !important;
    border: 1px solid {main_button_border} !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    box-shadow: 0 10px 24px rgba(0,0,0,{'0.12' if not dark else '0.28'}) !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button,
.stApp .mapboxgl-ctrl-group button,
.stApp .maplibregl-ctrl-group button {{
    background: {main_button_bg} !important;
    color: var(--text) !important;
    border-bottom: 1px solid {main_button_border} !important;
    width: 38px !important;
    height: 38px !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button:last-child,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button:last-child,
.stApp .mapboxgl-ctrl-group button:last-child,
.stApp .maplibregl-ctrl-group button:last-child {{
    border-bottom: none !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group button:hover,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group button:hover,
.stApp .mapboxgl-ctrl-group button:hover,
.stApp .maplibregl-ctrl-group button:hover {{
    background: {'#eef2f8' if not dark else '#1b2230'} !important;
}}

[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group .mapboxgl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group .maplibregl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .mapboxgl-ctrl-group .maplibregl-ctrl-icon,
[data-testid="stDeckGlJsonChart"] .maplibregl-ctrl-group .mapboxgl-ctrl-icon,
.stApp .mapboxgl-ctrl-group .mapboxgl-ctrl-icon,
.stApp .maplibregl-ctrl-group .maplibregl-ctrl-icon,
.stApp .mapboxgl-ctrl-group .maplibregl-ctrl-icon,
.stApp .maplibregl-ctrl-group .mapboxgl-ctrl-icon {{
    filter: none !important;
}}

.mlbx-map-meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem 0.65rem;
    align-items: center;
    margin-top: 0.2rem;
    color: var(--text);
    font-size: 0.98rem;
}}

.mlbx-map-meta-item {{
    color: var(--text);
}}

.mlbx-map-chip {{
    display: inline-flex;
    align-items: center;
    padding: 0.16rem 0.52rem;
    border-radius: 0.55rem;
    background: {'rgba(233,237,243,0.95)' if not dark else 'rgba(24, 29, 38, 0.96)'};
    border: 1px solid {'rgba(15,18,25,0.12)' if not dark else 'rgba(255,255,255,0.10)'};
    color: {'rgba(15,18,25,0.92)' if not dark else 'rgba(121, 242, 165, 0.96)'};
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-size: 0.95em;
    font-weight: 700;
    letter-spacing: 0.01em;
}}

/* Forzar que todos los headers usen la variable --text */
h1, h2, h3, h4, h5, h6 {{
    color: var(--text) !important;
}}

/* Headers de markdown también */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {{
    color: var(--text) !important;
}}
</style>
""", unsafe_allow_html=True)

# NOTA: _inject_pwa_metadata() se llama ahora al principio del script (justo
# después de set_page_config) para que iOS Safari encuentre los <link
# rel="apple-touch-icon"> y rel="manifest" en el DOM cuando el usuario hace
# "Añadir a pantalla de inicio". Si se inyecta tarde, iOS usa un screenshot
# como icono porque no le da tiempo a leer los tags inyectados por JS.

# CSS de componentes y responsive mobile
st.markdown(html_clean("""
<style data-mlbx-layout-hidden="component-css">
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"][height="0px"],
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"].st-key-browser_context_sync,
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"].st-key-mlx_local_storage_bootstrap,
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has(style[data-mlbx-layout-hidden]){
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
  }

  [data-testid="stMainBlockContainer"] {
    padding-top: 8.5rem !important;
  }

  .block-container { 
    padding-top: 8.5rem;
    max-width: 1200px;
  }

  .header{
    display:flex; 
    align-items:center; 
    justify-content:space-between;
    margin-top: 0;
    margin-bottom: 0.1rem;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .header h1{ 
    margin:0; 
    font-size:2.0rem; 
    color:var(--text); 
  }
  .header-sub{
    margin: 0;
    font-size: 0.82rem;
    color: var(--muted);
    opacity: 0.9;
    font-weight: 500;
  }
  .meta{ 
    color:var(--muted); 
    font-size:0.95rem; 
  }

  .station-count{
    margin: 0 0 0.45rem 0;
  }
  .station-selector-gap{
    height: 0.42rem;
  }

  /* CTA primario geolocalización */
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="primary"]{
    background: linear-gradient(135deg, #d62828, #b51717) !important;
    border: 1px solid #a41212 !important;
    color: #ffffff !important;
    font-weight: 700 !important;
  }
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button[kind="primary"]:hover{
    background: linear-gradient(135deg, #e63946, #c1121f) !important;
    border: 1px solid #b10f1a !important;
  }
  [data-testid="stMainBlockContainer"] div[data-testid="stButton"]{
    margin-top: 0 !important;
    margin-bottom: 0.12rem !important;
  }

  .section-title{
    margin-top: 1.2rem;
    margin-bottom: 0.8rem;
    font-weight: 800;
    color: var(--text);
    letter-spacing: 0.2px;
    font-size: 1.15rem;
  }

  .grid{
    display: grid;
    gap: 16px;
    overflow: visible;
  }

  .grid-row-spacing{
    margin-top: 16px;
  }

  .grid-6{
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }

  .grid-4{
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .grid-3{
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  /* Tablets grandes */
  @media (max-width: 1300px){
    .grid-6{ grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }

  /* Tablets */
  @media (max-width: 1000px){
    .grid-3{ grid-template-columns: repeat(2, 1fr); }
  }

  /* Tablets pequeñas */
  @media (max-width: 900px){
    .grid-6{ grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .grid-4{ grid-template-columns: repeat(2, minmax(0, 1fr)); }

    .block-container {
      padding-top: 0.35rem;
      padding-left: 0.55rem;
      padding-right: 0.55rem;
    }

    [data-testid="stPlotlyChart"] {
      margin-left: -0.55rem !important;
      margin-right: -0.55rem !important;
      width: calc(100% + 1.1rem) !important;
      max-width: calc(100% + 1.1rem) !important;
      overflow: visible !important;
    }

    [data-testid="stPlotlyChart"] > div,
    [data-testid="stPlotlyChart"] .js-plotly-plot,
    [data-testid="stPlotlyChart"] .plot-container,
    [data-testid="stPlotlyChart"] .svg-container {
      width: 100% !important;
      max-width: 100% !important;
      overflow: visible !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .plot-container {
      margin-left: 0 !important;
      margin-right: 0 !important;
      width: 100% !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .g-ytitle,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-xtitle,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-x2title,
    [data-testid="stPlotlyChart"] .js-plotly-plot .g-y2title {
      display: none !important;
    }

    [data-testid="stPlotlyChart"] .main-svg .infolayer:has(.legend) .g-gtitle,
    [data-testid="stPlotlyChart"] .main-svg .infolayer:has(.legend) .gtitle {
      display: none !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .xaxislayer-above > .xtick text,
    [data-testid="stPlotlyChart"] .js-plotly-plot .xaxislayer-below > .xtick text {
      font-size: 12px !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-above > .ytick text,
    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-above > .y2tick text,
    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-below > .ytick text,
    [data-testid="stPlotlyChart"] .js-plotly-plot .yaxislayer-below > .y2tick text {
      font-size: 12px !important;
    }
  }

  /* Móviles grandes */
  @media (max-width: 600px){
    .grid-3, .grid-4, .grid-6 { 
      grid-template-columns: 1fr; 
      gap: 12px;
    }

    .grid-thermo.grid-4 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    
    .header h1 { 
      font-size: 1.6rem; 
    }
    
    .section-title {
      font-size: 1rem;
      margin-top: 1rem;
      margin-bottom: 0.6rem;
    }
    
    .meta {
      font-size: 0.85rem;
    }
  }

  /* Móviles pequeños */
  @media (max-width: 400px){
    .block-container {
      padding-left: 0.4rem;
      padding-right: 0.4rem;
    }
    
    .header h1 { 
      font-size: 1.4rem; 
    }
    
    .grid {
      gap: 10px;
    }
  }

  .card{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 22px;
    box-shadow: var(--shadow);
    padding: 14px;
    min-height: 0;
    backdrop-filter: blur(12px);
    transition: transform .12s ease;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  
  /* Deshabilitar hover en móviles táctiles */
  @media (hover: hover) {
    .card:hover{ transform: translateY(-2px); }
  }

  .card.card-h{
    flex-direction: row;
    align-items: flex-start;
    gap: 14px;
    position: relative;
    overflow: visible;
  }

  .card-help-wrap{
    position: absolute;
    top: auto;
    bottom: 10px;
    right: 10px;
    z-index: 8;
    display: inline-flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
  }

  .card-help-btn{
    width: 16px;
    height: 16px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.62rem;
    font-weight: 800;
    line-height: 1;
    background: rgba(0, 0, 0, 0.26);
    color: rgba(255, 255, 255, 0.78);
    user-select: none;
    cursor: help;
  }

  .card-help-tooltip{
    min-width: 260px;
    max-width: min(420px, calc(100vw - 44px));
    padding: 9px 10px;
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    background: rgba(14, 18, 26, 0.96);
    color: rgba(248, 251, 255, 0.96);
    font-size: 0.74rem;
    line-height: 1.34;
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
    opacity: 0;
    transform: translateY(4px);
    transition: opacity .15s ease, transform .15s ease;
    pointer-events: none;
    text-align: left;
    position: absolute;
    right: 0;
    bottom: calc(100% + 8px);
    z-index: 9999;
  }

  .card-help-wrap:hover .card-help-tooltip,
  .card-help-wrap:focus-within .card-help-tooltip,
  .card-help-wrap:focus .card-help-tooltip{
    opacity: 1;
    transform: translateY(0);
  }
  
  /* Tarjetas en layout compacto en móviles */
  @media (max-width: 420px){
    .card {
      padding: 12px;
      border-radius: 18px;
    }
    
    /* Mantener layout horizontal pero más compacto */
    .card.card-h {
      gap: 10px;
    }
  }
  
  /* Layout vertical solo en móviles muy pequeños */
  @media (max-width: 360px){
    .card.card-h {
      flex-direction: column;
      gap: 10px;
    }
  }

  .icon-col{
    flex: 0 0 auto;
    display: flex;
    align-items: flex-start;
    padding-top: 2px;
  }

  .content-col{
    flex: 1 1 auto;
    min-width: 0;
  }

.side-col{
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: center;
  gap: 4px;
  margin-left: 10px;
  min-width: 52px;
}
.side-col .max,
.side-col .min{
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1.15;
  white-space: nowrap;
}

/* Optimizar side-col en móviles */
@media (max-width: 420px){
  .side-col {
    min-width: 44px;
    margin-left: 8px;
    gap: 3px;
  }
  
  .side-col .max,
  .side-col .min {
    font-size: 0.90rem;
  }
}

@media (max-width: 360px){
  .side-col {
    min-width: 42px;
    margin-left: 6px;
    gap: 2px;
  }
  
  .side-col .max,
  .side-col .min {
    font-size: 0.86rem;
  }
}

  .card-title{
    color: var(--muted);
    font-size: 0.78rem;
    font-weight: 800;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    margin-top: 2px;
    white-space: normal;
    overflow: visible;
    line-height: 1.15;
  }

  .card-value{
    margin-top: 6px;
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--text);
    line-height: 1.1;
    white-space: nowrap;
  }

  .grid-basic .card-value{
    font-size: 2.4rem;
    font-weight: 700;
    line-height: 1.05;
  }
  
  /* Tamaños de fuente optimizados para móviles */
  @media (max-width: 600px){
    .card-title {
      font-size: 0.72rem;
      letter-spacing: 0.4px;
    }
    
    .card-value {
      font-size: 1.6rem;
      margin-top: 4px;
    }
    
    .grid-basic .card-value {
      font-size: 2.0rem;
    }
  }
  
  /* iPhone estándar (390-420px) - reducir aún más para dar espacio a max/min */
  @media (max-width: 420px){
    .card-value {
      font-size: 1.5rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.85rem;
    }
    
    .card-title {
      font-size: 0.70rem;
    }
  }
  
  @media (max-width: 360px){
    .card-value {
      font-size: 1.4rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.7rem;
    }
  }
  
  @media (max-width: 400px){
    .card-value {
      font-size: 1.5rem;
    }
    
    .grid-basic .card-value {
      font-size: 1.8rem;
    }
  }

  .unit{
    margin-left: 6px;
    font-size: 1.0rem;
    color: var(--muted);
    font-weight: 600;
  }
  
  @media (max-width: 600px){
    .unit {
      font-size: 0.85rem;
      margin-left: 4px;
    }
  }
  
  @media (max-width: 420px){
    .unit {
      font-size: 0.80rem;
      margin-left: 3px;
    }
  }

  .icon.big{
    width: 54px; height: 54px;
    border-radius: 18px;
    display:flex; align-items:center; justify-content:center;
    flex: 0 0 auto;
    background: transparent;
    box-shadow: none;
  }

  .rose-stats-grid{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    column-gap: 14px;
    row-gap: 6px;
    margin-top: 0.15rem;
  }

  .rose-stat-item{
    margin: 0;
    font-size: 0.98rem;
    line-height: 1.45;
    color: var(--text);
  }

  .rose-stat-item.is-dominant{
    font-weight: 800;
  }

  .icon-img{
    width: 54px;
    height: 54px;
    display:block;
    image-rendering: auto;
    filter: none;
  }
  
  /* Iconos más pequeños en móviles */
  @media (max-width: 600px){
    .icon.big {
      width: 48px;
      height: 48px;
    }
    
    .icon-img {
      width: 48px;
      height: 48px;
    }
  }
  
  @media (max-width: 400px){
    .icon.big {
      width: 42px;
      height: 42px;
    }
    
    .icon-img {
      width: 42px;
      height: 42px;
    }
  }

  .subtitle{
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.35;
  }

  .subtitle div{
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .subtitle b{ color: var(--text); font-weight: 600; }
  
  @media (max-width: 600px){
    .subtitle {
      font-size: 0.82rem;
      margin-top: 8px;
    }
  }
  
  /* Sidebar colapsada por defecto en móviles pero accesible */
  @media (max-width: 768px){
    /* Ocultar contenido del sidebar cuando está colapsada */
    [data-testid="stSidebar"][aria-expanded="false"] > div {
      display: none;
    }
    
    /* Reducir ancho del sidebar colapsado para evitar texto flotante */
    [data-testid="stSidebar"][aria-expanded="false"] {
      width: 0 !important;
      min-width: 0 !important;
      overflow: hidden;
    }
    
    /* Mostrar normalmente cuando está expandida */
    [data-testid="stSidebar"][aria-expanded="true"] {
      width: 21rem !important;
    }
    
    /* Asegurar que el botón de colapsar está visible */
    button[data-testid="collapsedControl"] {
      display: flex !important;
    }
  }
  
  /* Optimizar tabs en móviles */
  @media (max-width: 600px){
    [data-baseweb="tab-list"] {
      gap: 8px;
    }
    
    [data-baseweb="tab"] {
      font-size: 0.85rem !important;
      padding: 8px 12px !important;
    }
  }
</style>
"""), unsafe_allow_html=True)

if st.session_state.get(CONNECTED, False):
    if active_tab in {"observation", "trends", "historical"}:
        _inject_mobile_plotly_compactor()
    _inject_live_age_updater()

# ============================================================
# HEADER
# ============================================================

def _provider_refresh_seconds() -> int:
    """Intervalo de refresh sugerido según proveedor conectado."""
    provider_id = st.session_state.get(CONNECTION_TYPE, "")

    # Permite override por proveedor futuro sin tocar core.
    custom_value = st.session_state.get("provider_refresh_seconds")
    if custom_value not in (None, ""):
        try:
            return max(MIN_REFRESH_SECONDS, int(custom_value))
        except Exception:
            pass

    defaults = {
        "AEMET": 600,  # AEMET reporta típicamente en ventanas de ~10 min
        "METEOCAT": 600,  # Meteocat XEMA actualiza en base semihoraria/horaria según estación
        "EUSKALMET": 600,  # Euskalmet suele reportar en slots de 10 min
        "FROST": 300,  # Frost ofrece dato subhorario y series densas para muchas estaciones
        "METEOFRANCE": 300,  # Meteo-France ofrece dato actual a 6 min y serie horaria del día
        "METEOGALICIA": 600,  # MeteoGalicia ofrece estado y serie horaria reciente
        "NWS": 600,  # NWS suele actualizar en intervalo subhorario según estación
        "POEM": 300,  # POEM dispone de endpoints TR y series con mayor frecuencia
        "METOFFICE": 3600,  # Land Observations devuelve una serie horaria de 48 h
        "WEATHERLINK": 60,  # WeatherLink current conditions; límite por defecto holgado
        "WU": REFRESH_SECONDS,
    }
    return int(defaults.get(provider_id, REFRESH_SECONDS))


def _pressure_decimals_for_provider(provider_id: str) -> int:
    return 0 if str(provider_id).strip().upper() == "WU" else 1


def _total_catalog_stations() -> int:
    return int(STATION_CATALOG_TOTAL)


header_refresh_seconds = _provider_refresh_seconds() if st.session_state.get(CONNECTED, False) else REFRESH_SECONDS
header_refresh_label = (
    f"{header_refresh_seconds // 60} min"
    if header_refresh_seconds % 60 == 0 and header_refresh_seconds >= 60
    else f"{header_refresh_seconds}s"
)

_boot_mark("before render_app_header")
render_app_header(
    t=t,
    dark=dark,
    header_refresh_label=header_refresh_label,
    total_station_count=_total_catalog_stations(),
)
_boot_mark("render_app_header")
render_favorites_bar(t=t, dark=dark)
_boot_mark("render_favorites_bar")


# ============================================================
# COMPROBACIÓN DE CONEXIÓN
# ============================================================

connected = st.session_state.get(CONNECTED, False)
connection_snapshot = build_connection_snapshot(st.session_state)
has_connection_banner = render_connection_banner(
    t=t,
    dark=dark,
    snapshot=connection_snapshot,
    is_nan=is_nan,
    disconnect_callback=lambda: (
        disconnect_active_station(),
        st.rerun() if hasattr(st, "rerun") else st.experimental_rerun(),
    ),
)

# Aviso para estaciones MANUALES (observadores cooperativos del NWS vía IEM,
# redes ``*_COOP``): solo publican máx/mín una vez al día, sin datos en directo.
if str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper() == "IEM":
    _conn_network = str(st.session_state.get(PROVIDER_STATION_ID, "") or "").split("|", 1)[0]
    if _conn_network.upper().endswith("_COOP"):
        st.info(t("header.manual_station"))

if not has_connection_banner:
    st.markdown(
        html_clean(
            f"""
            <div style="
                margin: 0.35rem 0 0.0rem 0;
                padding: 0.9rem 1rem;
                border-radius: 10px;
                background: rgba(66, 133, 244, 0.20);
                color: rgb(47, 156, 255);
                font-weight: 500;
            ">
                👈 {t("header.connect_prompt")}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    # Mostrar selector de estaciones en pantalla principal
    from components.station_selector import render_station_selector

    render_station_selector()

connection_loading_payload = st.session_state.get(CONNECTION_LOADING)
loading_in_progress = isinstance(connection_loading_payload, dict)
_overlay_state_key = "_overlay_was_visible"
_overlay_was_visible = bool(st.session_state.get(_overlay_state_key, False))
if loading_in_progress:
    render_connection_loading_overlay(connection_loading_payload, title_text=t("connection.loading_title"), dark=dark)
    st.session_state[_overlay_state_key] = True
elif _overlay_was_visible:
    # Solo inyectamos el <iframe> de limpieza si el overlay estuvo activo en
    # un rerun anterior. Antes se enviaba en cada rerun y añadía un iframe
    # vacío al DOM cada vez, lo cual es ruido para el navegador.
    clear_connection_loading_overlay()
    st.session_state[_overlay_state_key] = False


# ============================================================
# OBTENCIÓN Y PROCESAMIENTO DE DATOS
# ============================================================

# Valores por defecto (se usan cuando no hay conexión)
base = {
    "Tc": float("nan"),
    "RH": float("nan"),
    "p_hpa": float("nan"),
    "Td": float("nan"),
    "wind": float("nan"),
    "gust": float("nan"),
    "feels_like": float("nan"),
    "heat_index": float("nan"),
    "wind_dir_deg": float("nan"),
    "precip_total": float("nan"),
    "solar_radiation": float("nan"),
    "uv": float("nan"),
    "epoch": 0,
}
daily_extremes = {}
station_info = {}
current_derivatives = {}

z = 0
inst_mm_h = float("nan")
r5_mm_h = float("nan")
r10_mm_h = float("nan")
inst_label = "—"
p_abs = float("nan")
p_msl = float("nan")
p_abs_disp = "—"
p_msl_disp = "—"
dp3 = float("nan")
p_label = "—"
p_arrow = "•"
e = float("nan")
q_gkg = float("nan")
theta = float("nan")
Tv = float("nan")
Te = float("nan")
Tw = float("nan")
lcl = float("nan")
rho = float("nan")
rho_v_gm3 = float("nan")

# Radiación
solar_rad = float("nan")
uv = float("nan")
et0 = float("nan")
clarity = float("nan")
balance = float("nan")
has_radiation = False  # Flag para saber si hay datos de radiación

# Gráficos
chart_epochs = []
chart_temps = []
has_chart_data = False

skip_live_refresh = bool(connected and active_tab in {"trends", "historical", "map", "ranking"})
runtime_snapshot = _load_runtime_snapshot() if skip_live_refresh else {}
if runtime_snapshot:
    base = dict(runtime_snapshot.get("base", base))
    daily_extremes = dict(runtime_snapshot.get("daily_extremes") or {})
    station_info = dict(runtime_snapshot.get("station") or {})
    current_derivatives = dict(runtime_snapshot.get("derivatives") or {})
    (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
     dp3, rate_h, p_label, p_arrow,
     inst_mm_h, r5_mm_h, r10_mm_h, inst_label,
     e_sat, e, Td_calc, Tw, q, q_gkg,
     theta, Tv, Te, rho, rho_v_gm3, lcl,
     solar_rad, uv, et0, clarity, balance,
     has_radiation, has_chart_data) = _unpack_derivatives(current_derivatives)

# Solo calcular datos si está conectado o intentando conectar
if (connected or loading_in_progress) and not runtime_snapshot:
    provider_id = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
    # Determinar origen de datos
    if provider_id in _standard_provider_runtime_config():
        dashboard, current_derivatives = _process_standard_provider_connection(provider_id)
        if dashboard is not None:
            base = dashboard.observation
            daily_extremes = dashboard.daily_extremes
            station_info = dashboard.station
            (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
             dp3, rate_h, p_label, p_arrow,
             inst_mm_h, r5_mm_h, r10_mm_h, inst_label,
             e_sat, e, Td_calc, Tw, q, q_gkg,
             theta, Tv, Te, rho, rho_v_gm3, lcl,
             solar_rad, uv, et0, clarity, balance,
             has_radiation, has_chart_data) = _unpack_derivatives(current_derivatives)

    else:
        # ========== DATOS DE WEATHER UNDERGROUND ==========
        station_id = str(st.session_state.get("wu_connected_station", "") or st.session_state.get(ACTIVE_STATION, "")).strip()
        api_key = str(st.session_state.get("wu_connected_api_key", "") or st.session_state.get(ACTIVE_KEY, "")).strip()

        # Verificar que tenemos los datos mínimos necesarios
        if not station_id or not api_key:
            _cancel_connection_loading()
            st.error("❌ Faltan datos de conexión. Introduce Station ID y API Key en el sidebar.")
            st.session_state[CONNECTED] = False
            st.stop()

        try:
            calibration_station = str(st.session_state.get("wu_station_calibration_station", "")).strip().upper()
            if calibration_station == station_id.upper():
                station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
            else:
                station_calibration = default_wu_calibration()

            st.session_state.pop("_wu_calibration_changed", False)

            runtime_z_str = str(st.session_state.get("wu_connected_z", "")).strip()
            visible_station_id = str(st.session_state.get(ACTIVE_STATION, "")).strip()
            visible_z_str = str(st.session_state.get(ACTIVE_Z, "")).strip()
            active_z_str = (
                runtime_z_str
                if runtime_z_str
                else visible_z_str if visible_station_id.upper() == station_id.upper() else ""
            )
            try:
                elevation_user = float(active_z_str) if active_z_str else 0.0
            except ValueError:
                elevation_user = 0.0

            sun_tz_name = str(
                st.session_state.get("provider_station_tz")
                or st.session_state.get("browser_tz")
                or ""
            ).strip()
            _proc_resp = fetch_wu_dashboard_session_cached(
                station_id,
                api_key,
                ttl_s=REFRESH_SECONDS,
                calibration=station_calibration,
                station_elevation=elevation_user if elevation_user > 0 else None,
                sun_tz_name=sun_tz_name,
            )
            base_raw = dict(_proc_resp.get("observation") or {})
            base = dict(base_raw)
            daily_extremes = dict(_proc_resp.get("daily_extremes") or {})
            station_info = dict(_proc_resp.get("station") or {})
            current_derivatives = dict(_proc_resp.get("derivatives") or {})

            # Guardar timestamp de última actualización exitosa
            st.session_state[LAST_UPDATE_TIME] = time.time()

            # Guardar latitud y longitud para cálculos de radiación
            st.session_state[STATION_LAT] = base.get("lat", float("nan"))
            st.session_state[STATION_LON] = base.get("lon", float("nan"))
            st.session_state["wu_station_lat"] = base.get("lat", float("nan"))
            st.session_state["wu_station_lon"] = base.get("lon", float("nan"))
            st.session_state[PROVIDER_STATION_ID] = station_id
            st.session_state[PROVIDER_STATION_NAME] = station_id

            # ========== ALTITUD ==========
            # Prioridad: 1) active_z del usuario, 2) elevation de API
            elevation_api = base.get("elevation", float("nan"))

            # PRIORIDAD: Usuario primero, luego API
            if elevation_user > 0:
                z = elevation_user
                st.session_state[ELEVATION_SOURCE] = "usuario"
            elif not is_nan(elevation_api):
                z = elevation_api
                st.session_state[ELEVATION_SOURCE] = "API"
            else:
                z = 0
                st.session_state[ELEVATION_SOURCE] = "ninguna"
                st.warning("⚠️ **Falta dato de altitud**. Los cálculos de presión absoluta y temperatura potencial pueden ser incorrectos. Introduce la altitud manualmente en el sidebar.")
                logger.error("Sin dato de altitud (API ni usuario)")

            st.session_state[STATION_ELEVATION] = z
            st.session_state["wu_station_alt"] = z
            st.session_state[PROVIDER_STATION_ALT] = z

            wu_derivatives = current_derivatives
            (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
             dp3, rate_h, p_label, p_arrow,
             inst_mm_h, r5_mm_h, r10_mm_h, inst_label,
             e_sat, e, Td_calc, Tw, q, q_gkg,
             theta, Tv, Te, rho, rho_v_gm3, lcl,
             solar_rad, uv, et0, clarity, balance,
             has_radiation, has_chart_data) = _unpack_derivatives(wu_derivatives)
            st.session_state[STATION_ELEVATION] = z
            st.session_state["wu_station_alt"] = z
            st.session_state[PROVIDER_STATION_ALT] = z

            for _w in (_proc_resp.get("warnings") or []):
                _msg = _render_observation_warning(_w)
                if _msg:
                    st.warning(_msg)

            # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
            wu_chart_series_is_fresh = _chart_series_fresh_for_station("WU", station_id)
            use_cached_wu_chart_series = wu_chart_series_is_fresh
            if use_cached_wu_chart_series:
                chart_state = series_from_state(st.session_state, "chart", pressure_key="pressures_abs")
                chart_station_id = str(st.session_state.get("chart_series_station_id", "")).strip().upper()
                chart_epochs = chart_state.get("epochs", [])
                chart_temps = chart_state.get("temps", [])
                chart_humidities = chart_state.get("humidities", [])
                chart_dewpts = chart_state.get("dewpts", [])
                chart_pressures = chart_state.get("pressures_abs", [])
                chart_uv_indexes = chart_state.get("uv_indexes", [])
                chart_solar_radiations = chart_state.get("solar_radiations", [])
                chart_winds = chart_state.get("winds", [])
                chart_gusts = chart_state.get("gusts", [])
                chart_wind_dirs = chart_state.get("wind_dirs", [])
                has_chart_data = chart_station_id == station_id.upper() and (
                    bool(chart_state.get("has_data")) or len(chart_epochs) > 0
                )
                if has_chart_data:
                    has_chart_data = bool(wu_derivatives.get("has_chart_data"))
            else:
                timeseries = dict(_proc_resp.get("series") or {})
                timeseries_raw = timeseries
                chart_epochs = timeseries.get("epochs", [])
                chart_temps = timeseries.get("temps", [])
                chart_humidities = timeseries.get("humidities", [])
                chart_dewpts = timeseries.get("dewpts", [])
                chart_pressures = timeseries.get("pressures_abs", []) or []
                chart_uv_indexes = timeseries.get("uv_indexes", [])
                chart_solar_radiations = timeseries.get("solar_radiations", [])
                chart_winds = timeseries.get("winds", [])
                chart_gusts = timeseries.get("gusts", [])
                chart_wind_dirs = timeseries.get("wind_dirs", [])

                ts_lat = timeseries.get("lat", float("nan"))
                ts_lon = timeseries.get("lon", float("nan"))
                if is_nan(st.session_state.get(STATION_LAT, float("nan"))) and not is_nan(ts_lat):
                    st.session_state[STATION_LAT] = ts_lat
                if is_nan(st.session_state.get(STATION_LON, float("nan"))) and not is_nan(ts_lon):
                    st.session_state[STATION_LON] = ts_lon
                has_chart_data = bool(timeseries.get("has_data", False)) or len(chart_epochs) > 0
                wu_sensor_presence = station_info.get("sensors") or {}
                prev_wu_sensor_presence = st.session_state.get("wu_sensor_presence", {})
                prev_wu_sensor_station = str(st.session_state.get("wu_sensor_presence_station", "")).strip().upper()
                st.session_state["wu_sensor_presence"] = wu_sensor_presence
                st.session_state["wu_sensor_presence_station"] = station_id.upper()
                # Solo forzamos rerun cuando hay un cambio genuino (cambio de
                # estación o cambio de sensores en la misma estación). En la
                # primera detección (prev_wu_sensor_station vacío) no rerunemos:
                # el sidebar se quedará sin la UI de calibración este ciclo,
                # pero la mostrará en el siguiente autorefresh natural,
                # ahorrando ~1 rerun (300-500ms en red) en la carga inicial.
                if active_tab == "observation" and prev_wu_sensor_station and (
                    prev_wu_sensor_station != station_id.upper()
                    or prev_wu_sensor_presence != wu_sensor_presence
                ):
                    st.rerun()
            
                # Guardar en session_state para acceso desde otras tabs
                store_chart_series(
                    st.session_state,
                    {
                        **timeseries,
                        "epochs": chart_epochs,
                        "temps": chart_temps,
                        "humidities": chart_humidities,
                        "dewpts": chart_dewpts,
                        "pressures_abs": chart_pressures,
                        "uv_indexes": chart_uv_indexes,
                        "solar_radiations": chart_solar_radiations,
                        "winds": chart_winds,
                        "gusts": chart_gusts,
                        "wind_dirs": chart_wind_dirs,
                        "precips": timeseries.get("precips", []),
                        "has_data": has_chart_data,
                    }
                )
                _set_chart_series_owner("WU", station_id.upper())

        except BackendApiError as api_error:
            # OJO: no usar `as e` aquí. Python borra el nombre vinculado en la
            # cláusula except al salir del bloque, lo que eliminaría la variable
            # de módulo `e` (presión de vapor) y dispararía NameError al
            # construir el contexto del tab de observación.
            had_connection_loading = isinstance(st.session_state.get(CONNECTION_LOADING), dict)
            _cancel_connection_loading()
            error_key = _queue_wu_connection_error(api_error.kind, api_error.status_code)
            if had_connection_loading:
                st.rerun()
            st.sidebar.error(t(error_key, status=str(api_error.status_code or "")))
            st.error(t(error_key, status=str(api_error.status_code or "")))
            st.stop()
        except Exception as err:
            _cancel_connection_loading()
            # Usar concatenación simple para evitar cualquier problema con format specifiers
            st.error("❌ Error inesperado: " + str(err))
            logger.error(f"Error inesperado: {repr(err)}")

if st.session_state.get(CONNECTION_LOADING) and _has_live_connection_payload(base):
    _loading_payload_for_clear = st.session_state.get(CONNECTION_LOADING)
    _loading_started_at = None
    if isinstance(_loading_payload_for_clear, dict):
        _loading_started_at = _loading_payload_for_clear.get("started_at")
    st.session_state[CONNECTED] = True
    st.session_state.pop(CONNECTION_LOADING, None)
    # Pasamos started_at para garantizar que el overlay se vea un mínimo de
    # tiempo aunque la respuesta venga de caché (evita el parpadeo que ocurría
    # en conexiones posteriores a la primera).
    clear_connection_loading_overlay(started_at=_loading_started_at)
    st.rerun()


if connected and int(base.get("epoch", 0) or 0) > 0:
    _store_runtime_snapshot(
        base=base,
        derivatives=current_derivatives,
        daily_extremes=daily_extremes,
        station=station_info,
    )

# Mostrar metadata si está conectado (común para AEMET y WU). Sin epoch
# válido (p. ej. estación de archivo sin observación actual) no hay "último
# dato" que mostrar: pintar epoch=0 enseñaba "01-01-1970" y una edad absurda.
if connected and int(base.get("epoch", 0) or 0) > 0:
    browser_tz_name = str(
        st.session_state.get("browser_tz") or st.query_params.get("_tz", "")
    ).strip()
    station_tz_name = str(
        station_info.get("tz") or st.session_state.get("provider_station_tz", "")
    ).strip()

    user_time_txt = es_datetime_from_epoch(base["epoch"], browser_tz_name)
    user_time_label = t("meta.user_time")
    user_time_label_safe = html.escape(user_time_label)
    user_time_fallback_label = html.escape(t("meta.user_time"))
    user_time_txt_safe = html.escape(user_time_txt)

    station_time_txt = ""
    if station_tz_name:
        try:
            station_epoch_txt = es_datetime_from_epoch(base["epoch"], station_tz_name)
            if station_epoch_txt != user_time_txt:
                station_time_txt = (
                    f" · {t('meta.station_time')} ({station_tz_name}): {station_epoch_txt}"
                )
        except Exception:
            station_time_txt = ""

    st.markdown(
        html_clean(
            (
                f"<div class='meta'>{t('meta.last_data')} · "
                f"<span class='mlbx-live-user-time-label' data-fallback-label='{user_time_fallback_label}'>{user_time_label_safe}</span>: "
                f"<span class='mlbx-live-user-time' data-epoch='{int(base['epoch'])}'>{user_time_txt_safe}</span>"
                f"{station_time_txt} · {t('meta.age')}: "
                f"<span class='mlbx-live-age' data-epoch='{int(base['epoch'])}'>{html.escape(age_string(base['epoch']))}</span></div>"
            )
        ),
        unsafe_allow_html=True
    )

# ============================================================
# NAVEGACIÓN CON TABS
# ============================================================

# ============================================================
# SELECTOR DE TABS CON st.radio (estilizado como tabs)
# ============================================================

# CSS para ocultar círculos y estilizar como tabs (dinámico según tema)
# DEBE IR ANTES del radio button para que se aplique correctamente
tabs_color = "rgba(15, 18, 25, 0.92)" if not dark else "rgba(255, 255, 255, 0.92)"

# Añadir hash único al CSS para forzar regeneración
import hashlib
css_hash = hashlib.md5(f"{tabs_color}{dark}".encode()).hexdigest()[:8]

st.markdown(f"""
<style data-theme-hash="{css_hash}" data-mlbx-layout-hidden="tabs-theme">
/* Ocultar el círculo del radio */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label > div:first-child {{
    display: none;
}}
/* Estilo base de cada opción */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label {{
    padding: 0.5rem 1rem;
    margin-right: 0.5rem;
    border-bottom: 3px solid transparent;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.2s ease;
}}
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label div[data-testid="stMarkdownContainer"] p {{
    color: {tabs_color} !important;
}}
/* Hover */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:hover {{
    border-bottom: 3px solid rgba(255, 75, 75, 0.3);
}}
/* Opción seleccionada */
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:has(input:checked) {{
    border-bottom: 3px solid #ff4b4b;
    font-weight: 600;
}}
[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
    color: #ff4b4b !important;
}}
</style>

<script>
// Aplicar colores a las pestañas con JavaScript como fallback
(function() {{
    const tabColor = '{tabs_color}';
    const labels = document.querySelectorAll('[data-testid="stMainBlockContainer"] div[role="radiogroup"] > label');
    labels.forEach(label => {{
        const p = label.querySelector('p');
        if (p && !label.querySelector('input:checked')) {{
            p.style.setProperty('color', tabColor, 'important');
        }}
    }});
}})();
</script>
""", unsafe_allow_html=True)

tab_options = TAB_OPTIONS

# Radio buttons estilizados como tabs con underline
active_tab = st.radio(
    "Navegación",
    tab_options,
    horizontal=True,
    format_func=lambda tab_id: t(f"tabs.{tab_id}"),
    key="active_tab",
    label_visibility="collapsed"
)


def _weatherlink_station_label(station: dict) -> str:
    name = str(station.get("station_name") or station.get("name") or "").strip()
    station_id = str(station.get("station_id") or station.get("station_id_uuid") or "").strip()
    city = str(station.get("city") or "").strip()
    region = str(station.get("region") or "").strip()
    locality = ", ".join(part for part in (city, region) if part)
    label = name or station_id
    if locality and locality not in label:
        label = f"{label} · {locality}"
    return label


def _render_weatherlink_station_selector() -> None:
    if str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper() != "WEATHERLINK":
        return
    stations = st.session_state.get("weatherlink_stations", [])
    if not isinstance(stations, list) or len(stations) <= 1:
        return
    station_options = [
        str(station.get("station_id") or station.get("station_id_uuid") or "").strip()
        for station in stations
        if isinstance(station, dict) and str(station.get("station_id") or station.get("station_id_uuid") or "").strip()
    ]
    if len(station_options) <= 1:
        return
    current_station_id = str(st.session_state.get("weatherlink_station_id", "") or "").strip()
    if current_station_id not in station_options:
        current_station_id = station_options[0]
    if st.session_state.get("weatherlink_station_selector") not in station_options:
        st.session_state["weatherlink_station_selector"] = current_station_id

    station_by_id = {
        str(station.get("station_id") or station.get("station_id_uuid") or "").strip(): station
        for station in stations
        if isinstance(station, dict)
    }
    selected_station_id = st.selectbox(
        t("weatherlink.station_selector.label"),
        station_options,
        format_func=lambda station_id: _weatherlink_station_label(station_by_id.get(station_id, {})),
        key="weatherlink_station_selector",
        width="stretch",
    )
    selected_station_id = str(selected_station_id or "").strip()
    if selected_station_id and selected_station_id != current_station_id:
        selected_station = station_by_id.get(selected_station_id, {})
        if apply_weatherlink_station_state(
            selected_station,
            str(st.session_state.get("weatherlink_api_key", "") or ""),
            str(st.session_state.get("weatherlink_api_secret", "") or ""),
            str(st.session_state.get("weatherlink_station_alt", "") or ""),
            stations,
            connected=True,
        ):
            st.rerun()


_render_weatherlink_station_selector()

# Mantén la URL como link compartible (?e=<provider>~<slug>&tab=<tab>).
_sync_shareable_url(active_tab)

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# Registra los templates de Plotly antes de pintar cualquier tab: algunas
# (historical) referencian "meteolabx_light/dark" por nombre al construir la
# figura, antes de pasar por _plotly_chart_stretch.
_register_plotly_templates()

# Panel INTERNO de estadísticas (administración): sustituye a las pestañas
# mientras está abierto. Se activa desde el formulario WU con el id especial
# Statics_admin + contraseña (ver components/internal_stats.py).
if st.session_state.get("internal_stats_open"):
    from components.internal_stats import render_internal_stats

    render_internal_stats()
    st.stop()

# TAB 1: OBSERVACIÓN
_boot_mark(f"before tab render (active_tab={active_tab})")
if active_tab == "observation":
    _get_tab_module().render_observation_tab(_build_observation_tab_context())
    _boot_mark("after render_observation_tab")

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "trends":
    _get_tab_module().render_trends_tab(_build_trends_tab_context())
    _boot_mark("after render_trends_tab")

# ============================================================
# TAB 3: HISTORICO
# ============================================================

elif active_tab == "historical":
    _get_tab_module().render_historical_tab(_build_historical_tab_context())
    _boot_mark("after render_historical_tab")

# ============================================================
# TAB 4: MAPA
# ============================================================

elif active_tab == "map":
    _get_tab_module().render_map_tab(_build_map_tab_context())
    _boot_mark("after render_map_tab")

# ============================================================
# TAB 5: RANKING
# ============================================================

elif active_tab == "ranking":
    _get_tab_module().render_ranking_tab(_build_ranking_tab_context())
    _boot_mark("after render_ranking_tab")

# ============================================================
# AUTOREFRESH SOLO EN OBSERVACIÓN
# ============================================================
# Autorefresh solo se activa cuando el tab activo es Observación

if st.session_state.get(CONNECTED, False):
    if active_tab == "observation":
        refresh_interval = _provider_refresh_seconds()
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_interval * 1000, key="refresh_data")

# ============================================================
# FOOTER
# ============================================================

APP_VERSION = "1.2.6"


def _whats_new_footer_html() -> str:
    """HTML desplegable de novedades en el propio footer."""
    def _section(title: str, key: str) -> str:
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in t_list(key))
        return (
            f"<div class='mlx-wn-title'>{html.escape(str(title))}</div>"
            f"<ul class='mlx-wn-list'>{items}</ul>"
        )

    def _release(version: str, improvements_key: str, fixes_key: str) -> str:
        return (
            f"<div class='mlx-wn-version'>{html.escape(str(version))}</div>"
            + _section(t("footer.improvements_title"), improvements_key)
            + _section(t("footer.fixes_title"), fixes_key)
        )

    return (
        _release("1.2.0", "footer.improvements", "footer.fixes")
        + _release("1.1.0", "footer.previous_improvements", "footer.previous_fixes")
    )


# Pie de página: línea superior con la versión (a la izquierda) y, justo al
# lado, un desplegable "Novedades" que se abre en la propia página.
st.markdown(
    html_clean(
        f"<style>"
        ".mlb-footsep{margin-top:1.25rem;padding-top:0.8rem;"
        "border-top:1px solid var(--line);}"
        ".mlb-footer-head{display:flex;align-items:baseline;gap:0.45rem;"
        "flex-wrap:wrap;margin:0;}"
        ".mlb-version{color:var(--muted);font-size:0.92rem;font-weight:700;"
        "white-space:nowrap;}"
        ".mlb-whats-new{display:inline;position:relative;margin:0;padding:0;}"
        ".mlb-whats-new summary{display:inline;cursor:pointer;list-style:none;"
        "color:#2384ff !important;text-decoration:underline !important;"
        "text-decoration-thickness:1.5px !important;text-underline-offset:2px !important;"
        "font-weight:700 !important;font-size:0.92rem !important;line-height:1.25;"
        "border:0 !important;outline:0 !important;box-shadow:none !important;}"
        ".mlb-whats-new summary::-webkit-details-marker{display:none;}"
        ".mlb-whats-new summary:hover{color:#1366d6 !important;}"
        ".mlb-whats-new-panel{margin-top:0.85rem;width:min(820px, calc(100vw - 3rem));"
        "padding:1rem 1.15rem 1.05rem;border-radius:10px;"
        "background:rgba(219, 235, 255, 0.96);border:1px solid rgba(51, 126, 215, 0.22);"
        "color:rgba(24, 35, 56, 0.96);box-shadow:0 12px 28px rgba(41, 83, 145, 0.12);}"
        ".mlx-wn-version{font-weight:900;font-size:1.04rem;margin:0.1rem 0 0.45rem;}"
        ".mlx-wn-version:not(:first-child){margin-top:1rem;padding-top:0.9rem;"
        "border-top:1px solid rgba(51, 126, 215, 0.22);}"
        ".mlx-wn-title{font-weight:800;font-size:1rem;margin:0.15rem 0 0.28rem;}"
        ".mlx-wn-title:not(:first-child){margin-top:0.75rem;}"
        ".mlx-wn-list{margin:0;padding-left:1.25rem;}"
        ".mlx-wn-list li{margin:0.18rem 0;line-height:1.45;font-size:0.9rem;}"
        ".mlb-footer-bottom{margin-top:0.4rem;font-size:0.86rem;"
        "color:var(--muted);opacity:0.92;}"
        "</style>"
        "<div class='mlb-footsep'></div>"
        "<div class='mlb-footer-head'>"
        f"<span class='mlb-version'>MeteoLabX · {t('footer.version', version=APP_VERSION)}</span>"
        "<details class='mlb-whats-new'>"
        f"<summary>{html.escape(str(t('footer.whats_new')))}</summary>"
        f"<div class='mlb-whats-new-panel'>{_whats_new_footer_html()}</div>"
        "</details>"
        "</div>"
    ),
    unsafe_allow_html=True,
)

st.markdown(
    html_clean(
        "<div class='mlb-footer-bottom'>%s: WU · WeatherLink · AEMET · Meteocat · "
        "Euskalmet · Frost · Meteo-France · MeteoGalicia · NWS · POEM · Met Office · "
        "MeteoHub Italia · IEM · %s · © 2026</div>"
        % (t("footer.sources"), t("footer.unaffiliated"))
    ),
    unsafe_allow_html=True,
)

flush_local_storage_writes("mlx_local_storage_app_flush")
_boot_mark("END OF SCRIPT")

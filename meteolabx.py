"""
MeteoLabx - Panel meteorológico avanzado
Aplicación principal
"""
import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(
    page_title="MeteoLabX",
    page_icon="favicon.png",
    layout="wide",
    initial_sidebar_state="collapsed"  # Sidebar colapsada por defecto en móvil
)
import time
import math
import logging
import html
import os
import hashlib
from typing import Optional
from datetime import datetime, timedelta

# Imports locales
from config import REFRESH_SECONDS, MIN_REFRESH_SECONDS, MAX_DATA_AGE_MINUTES, LS_AUTOCONNECT, RD
from data_files import STATION_CATALOG_TOTAL
from utils import html_clean, is_nan, es_datetime_from_epoch, age_string, fmt_hpa, month_name, t
from utils.storage import (
    set_local_storage,
    set_stored_autoconnect_target,
    get_stored_autoconnect,
    get_stored_autoconnect_target,
)
from utils.provider_state import (
    apply_station_selection,
    build_connection_snapshot,
    disable_provider_autoconnect,
    disconnect_active_station,
    persist_provider_autoconnect_target,
    get_provider_label as resolve_provider_label,
    get_provider_station_id as resolve_provider_station_id,
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
from utils.series_state import (
    series_from_state,
    store_chart_series,
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
    PENDING_ACTIVE_TAB,
    PROVIDER_STATION_ALT,
    PROVIDER_STATION_ID,
    PROVIDER_STATION_LAT,
    PROVIDER_STATION_LON,
    PROVIDER_STATION_NAME,
    PROVIDER_STATION_TZ,
    STATION_ELEVATION,
    STATION_LAT,
    STATION_LON,
)
from utils.units import (
    convert_precip,
    convert_pressure,
    convert_radiation,
    convert_radiation_energy,
    convert_temperature,
    convert_temperature_delta,
    convert_wind,
    format_precip,
    format_pressure,
    format_radiation,
    format_radiation_energy,
    format_temperature,
    format_temperature_delta,
    format_wind,
    normalize_unit_preferences,
    precip_unit_label,
    pressure_unit_label,
    radiation_energy_unit_label,
    radiation_unit_label,
    temperature_unit_label,
    wind_unit_label,
)
from api import WuError, fetch_wu_current_session_cached, fetch_daily_timeseries_session_cached, fetch_hourly_7day_session_cached
from models.thermodynamics import (
    e_s, vapor_pressure, dewpoint_from_vapor_pressure,
    mixing_ratio, specific_humidity, absolute_humidity,
    potential_temperature, virtual_temperature, equivalent_temperature, equivalent_potential_temperature,
    wet_bulb_celsius, msl_to_absolute, air_density, lcl_height,
    apparent_temperature, heat_index_rothfusz,
)
from models.radiation import (
    sky_clarity_label, uv_index_label, water_balance, water_balance_label,
)
from services import (
    rain_rates_from_total, rain_intensity_label, reset_rain_history,
    init_pressure_history, push_pressure, pressure_trend_3h
)
from services.wu_calibration import (
    apply_wu_current_calibration,
    apply_wu_series_calibration,
    default_wu_calibration,
    detect_wu_sensor_presence,
)
from components import (
    card, section_title, render_grid,
    wind_dir_text, render_sidebar
)
from components.app_header import render_app_header, render_connection_banner

from components.browser_context import get_browser_context
from components.browser_geolocation import get_browser_geolocation
from tabs import (
    build_observation_context,
    render_observation_tab,
    render_trends_tab,
    render_historical_tab,
    render_map_tab,
)

# Configurar logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


PWA_ASSET_VERSION = "8"
PWA_STATIC_BASE = "/app/static"


def _inject_pwa_metadata() -> None:
    """Sincroniza manifest e iconos en el head real para web apps instalables."""
    components.html(
        f"""
        <script>
        (function () {{
          try {{
            const doc = window.parent && window.parent.document ? window.parent.document : document;
            const head = doc.head;
            if (!head) return;
            const base = "{PWA_STATIC_BASE}";
            const version = "{PWA_ASSET_VERSION}";
            const asset = (name) => `${{base}}/${{name}}?v=${{version}}`;

            function upsertMeta(name, content) {{
              let el = head.querySelector(`meta[name="${{name}}"]`);
              if (!el) {{
                el = doc.createElement("meta");
                el.setAttribute("name", name);
                head.appendChild(el);
              }}
              el.setAttribute("content", content);
            }}

            function upsertLink(rel, href, attrs) {{
              attrs = attrs || {{}};
              const sizes = attrs.sizes || "";
              const selector = sizes
                ? `link[rel="${{rel}}"][sizes="${{sizes}}"]`
                : `link[rel="${{rel}}"]:not([sizes])`;
              let el = head.querySelector(selector);
              if (!el) {{
                el = doc.createElement("link");
                el.setAttribute("rel", rel);
                if (sizes) el.setAttribute("sizes", sizes);
                head.appendChild(el);
              }}
              Object.keys(attrs).forEach((key) => el.setAttribute(key, attrs[key]));
              el.setAttribute("href", href);
            }}

            upsertMeta("viewport", "width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes");
            upsertMeta("mobile-web-app-capable", "yes");
            upsertMeta("apple-mobile-web-app-capable", "yes");
            upsertMeta("apple-mobile-web-app-status-bar-style", "black-translucent");
            upsertMeta("apple-mobile-web-app-title", "MeteoLabX");
            upsertMeta("theme-color", "#2384ff");

            upsertLink("manifest", asset("manifest.json"));
            upsertLink("apple-touch-icon", asset("apple-touch-icon-pwa.png"));
            upsertLink("apple-touch-icon", asset("apple-touch-icon-pwa.png"), {{ sizes: "180x180" }});
            upsertLink("apple-touch-icon-precomposed", asset("apple-touch-icon-pwa.png"));
            upsertLink("icon", asset("icon-192-pwa.png"), {{ type: "image/png", sizes: "192x192" }});
            upsertLink("icon", asset("icon-512-pwa.png"), {{ type: "image/png", sizes: "512x512" }});
            upsertLink("shortcut icon", asset("icon-192-pwa.png"), {{ type: "image/png" }});
          }} catch (_e) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _get_frost_service():
    """Importa Frost bajo demanda para aligerar el arranque inicial."""
    from services import frost as frost_service
    return frost_service


def _get_meteofrance_service():
    """Importa Meteo-France bajo demanda para aligerar el arranque inicial."""
    from services import meteofrance as meteofrance_service
    return meteofrance_service


def _get_climograms_service():
    """Importa cálculos de climogramas bajo demanda para aligerar el arranque inicial."""
    from services import climograms as climograms_service
    return climograms_service


def _get_aemet_service():
    """Importa AEMET bajo demanda para aligerar el arranque inicial."""
    from services import aemet as aemet_service
    return aemet_service


def _get_meteocat_service():
    """Importa Meteocat bajo demanda para aligerar el arranque inicial."""
    from services import meteocat as meteocat_service
    return meteocat_service


def _get_euskalmet_service():
    """Importa Euskalmet bajo demanda para aligerar el arranque inicial."""
    from services import euskalmet as euskalmet_service
    return euskalmet_service


def _get_meteogalicia_service():
    """Importa MeteoGalicia bajo demanda para aligerar el arranque inicial."""
    from services import meteogalicia as meteogalicia_service
    return meteogalicia_service


def _get_poem_service():
    """Importa POEM bajo demanda para aligerar el arranque inicial."""
    from services import poem as poem_service
    return poem_service


def _get_nws_service():
    """Importa NWS bajo demanda para aligerar el arranque inicial."""
    from services import nws as nws_service
    return nws_service


def _get_provider_label(provider_id: str) -> str:
    return resolve_provider_label(provider_id)


def _get_provider_station_id(provider_id: str) -> str:
    return resolve_provider_station_id(st.session_state, provider_id)


def _get_provider_api_key(provider_id: str):
    provider_id = str(provider_id or "").strip().upper()
    api_key_resolvers = {
        "WU": lambda: str(
            st.session_state.get("active_key", "")
            or st.session_state.get("wu_connected_api_key", "")
        ).strip(),
        "AEMET": lambda: _get_aemet_service().AEMET_API_KEY,
        "METEOFRANCE": lambda: _get_meteofrance_service().METEOFRANCE_API_KEY,
    }
    feature = get_provider_feature(provider_id)
    resolver = api_key_resolvers.get(str(feature.get("api_key_source", provider_id)).strip().upper())
    if resolver is not None:
        return resolver()
    return None


SERIES_START_LOADERS = {
    "METEOCAT": {
        "loader": lambda station_id: _get_meteocat_service().get_meteocat_station_series_start_date(station_id),
        "formatter": lambda value: datetime.fromisoformat(value).strftime("%d/%m/%Y"),
    },
    "METEOFRANCE": {
        "loader": lambda station_id: _get_meteofrance_service().get_meteofrance_station_series_start_date(station_id),
        "formatter": lambda value: str(value),
    },
}


def _render_historical_provider_series_start(provider_id: str, station_id: str) -> None:
    provider_id = str(provider_id or "").strip().upper()
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


def _get_historical_missing_message(provider_id: str, station_id: str, api_key) -> str:
    provider_id = str(provider_id or "").strip().upper()
    provider_feature = get_provider_feature(provider_id)
    key = str(provider_feature.get("historical_missing_key", "")).strip()
    if not key:
        return ""
    if provider_feature.get("requires_api_key") and (not station_id or not api_key):
        return t(key)
    if not provider_feature.get("requires_api_key") and not station_id:
        return t(key)
    return ""


def _standard_provider_runtime_config() -> dict[str, dict]:
    return {
        "EUSKALMET": {
            "loader": lambda: _get_euskalmet_service().get_euskalmet_data(),
            "fallback_key": "euskalmet_station_alt",
            "warning": (
                "⚠️ No se pudieron obtener datos de Euskalmet. "
                "Se intenta generar JWT automáticamente desde "
                "`EUSKALMET_PRIVATE_KEY_PATH` / `EUSKALMET_PUBLIC_KEY_PATH`."
            ),
            "detail_key": "euskalmet_last_error",
            "detail_prefix": "Detalle técnico Euskalmet: ",
            "series_mode": "none",
        },
        "METEOCAT": {
            "loader": lambda: _get_meteocat_service().get_meteocat_data(),
            "fallback_key": "meteocat_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Meteocat por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "none",
        },
        "METEOFRANCE": {
            "loader": lambda: _get_meteofrance_service().get_meteofrance_data(),
            "fallback_key": "meteofrance_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Meteo-France por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "meteofrance_last_error",
            "detail_prefix": "Detalle técnico Meteo-France: ",
            "series_mode": "from_base",
        },
        "FROST": {
            "loader": lambda: _get_frost_service().get_frost_data(),
            "fallback_key": "frost_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de Frost por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "frost_last_error",
            "detail_prefix": "Detalle técnico Frost: ",
            "series_mode": "from_base",
        },
        "METEOGALICIA": {
            "loader": lambda: _get_meteogalicia_service().get_meteogalicia_data(),
            "fallback_key": "meteogalicia_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de MeteoGalicia por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "copy_chart",
        },
        "NWS": {
            "loader": lambda: _get_nws_service().get_nws_data(),
            "fallback_key": "nws_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de NWS por ahora. Intenta de nuevo en unos minutos.",
            "series_mode": "from_base",
        },
        "POEM": {
            "loader": lambda: _get_poem_service().get_poem_data(),
            "fallback_key": "poem_station_alt",
            "warning": "⚠️ No se pudieron obtener datos de POEM por ahora. Intenta de nuevo en unos minutos.",
            "detail_key": "poem_last_error",
            "detail_prefix": "Detalle técnico POEM: ",
            "series_mode": "from_base",
        },
    }


def _extract_provider_series_7d(base: dict, mode: str) -> Optional[dict]:
    mode = str(mode or "none").strip().lower()
    if mode == "from_base":
        raw_7d = base.get("_series_7d")
        return raw_7d if isinstance(raw_7d, dict) else {}
    if mode == "copy_chart":
        return {}
    return None


def _process_standard_provider_connection(provider_id: str) -> tuple[dict, "ProcessedData"]:
    provider_id = str(provider_id or "").strip().upper()
    config = _standard_provider_runtime_config().get(provider_id)
    if not config:
        raise KeyError(f"Proveedor estándar no soportado: {provider_id}")

    base = config["loader"]()
    if base is None:
        _cancel_connection_loading()
        detail_key = str(config.get("detail_key", "")).strip()
        detail = str(st.session_state.get(detail_key, "")).strip() if detail_key else ""
        st.warning(_friendly_provider_warning(provider_id, config["warning"], detail))
        if detail:
            st.caption(f"{config.get('detail_prefix', '')}{detail}")
        st.stop()

    return base, process_standard_provider(
        base,
        provider_id,
        config["fallback_key"],
        series_7d=_extract_provider_series_7d(base, str(config.get("series_mode", "none"))),
    )


def _fetch_provider_synoptic_series_from_state(provider_id: str) -> tuple[dict, str]:
    provider_id = str(provider_id or "").strip().upper()
    hourly7d = store_trend_hourly_series(st.session_state, series_from_state(st.session_state, "trend_hourly"))
    hourly7d["has_data"] = bool(hourly7d.get("has_data")) or len(hourly7d["epochs"]) > 0
    return hourly7d, t("trends.sources.generic_synoptic", provider=provider_id)


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

    if any(token in lowered for token in ("connection aborted", "failed to establish", "max retries exceeded", "name or service not known", "temporary failure", "network", "proxyerror", "ssLError".lower(), "no se pudo contactar")):
        return f"⚠️ No se pudo contactar con {provider_label} ahora mismo. Puede ser un problema temporal de red o del servicio."

    if any(token in lowered for token in ("no hay datos", "sin datos", "serie vacía", "series vigentes", "no devolvió datos", "does not satisfy", "satisfagan esos criterios", "no data", "empty series")):
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
    st.session_state["chart_series_provider_id"] = str(provider_id or "").strip().upper()
    st.session_state["chart_series_station_id"] = str(station_id or "").strip().upper()


def _chart_series_fresh_for_station(provider_id: str, station_id: str, *, max_age_s: Optional[int] = None) -> bool:
    provider_norm = str(provider_id or "").strip().upper()
    station_norm = str(station_id or "").strip().upper()
    if not provider_norm or not station_norm:
        return False
    chart_provider = str(st.session_state.get("chart_series_provider_id", "")).strip().upper()
    chart_station = str(st.session_state.get("chart_series_station_id", "")).strip().upper()
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
    freshness_window = int(max_age_s if max_age_s is not None else max(300, REFRESH_SECONDS * 2))
    return max(epochs) >= int(time.time()) - freshness_window


def _normalize_observation_chart_series(provider_id: str, payload: Optional[dict]) -> dict:
    provider_id = str(provider_id or "").strip().upper()
    series = payload if isinstance(payload, dict) else {}
    epochs = list(series.get("epochs", []))
    normalized = {
        "epochs": epochs,
        "temps": list(series.get("temps", [])),
        "humidities": list(series.get("humidities", [])),
        "dewpts": list(series.get("dewpts", [])),
        "pressures_abs": list(series.get("pressures_abs", series.get("pressures", []))),
        "uv_indexes": list(series.get("uv_indexes", [])),
        "solar_radiations": list(series.get("solar_radiations", [])),
        "winds": list(series.get("winds", [])),
        "gusts": list(series.get("gusts", [])),
        "wind_dirs": list(series.get("wind_dirs", [])),
        "has_data": bool(series.get("has_data", False)) or len(epochs) > 0,
    }
    if provider_id == "AEMET" and (not normalized["humidities"]) and normalized["dewpts"]:
        normalized["humidities"] = _humidity_series_from_temp_and_dewpoint(
            normalized["temps"],
            normalized["dewpts"],
        )
    return normalized


def _fetch_deferred_observation_chart_series(provider_id: str, station_id: str) -> dict:
    provider_id = str(provider_id or "").strip().upper()
    station_id = str(station_id or "").strip().upper()
    if not provider_id or not station_id:
        return {"epochs": [], "has_data": False}

    if provider_id == "AEMET":
        return _normalize_observation_chart_series(
            provider_id,
            _get_aemet_service().fetch_aemet_today_series_with_lookback(station_id, hours_before_start=0),
        )
    if provider_id == "METEOCAT":
        return _normalize_observation_chart_series(
            provider_id,
            _get_meteocat_service().fetch_meteocat_today_series_with_lookback(
                station_id,
                hours_before_start=0,
                api_key=_get_meteocat_service().METEOCAT_API_KEY,
            ),
        )
    if provider_id == "EUSKALMET":
        return _normalize_observation_chart_series(
            provider_id,
            _get_euskalmet_service().fetch_euskalmet_day_series(
                station_id,
                jwt=getattr(_get_euskalmet_service(), "EUSKALMET_JWT", None),
                api_key=getattr(_get_euskalmet_service(), "EUSKALMET_API_KEY", None),
            ),
        )

    if provider_id in _standard_provider_runtime_config():
        try:
            base = _standard_provider_runtime_config()[provider_id]["loader"]()
        except Exception:
            return {"epochs": [], "has_data": False}
        if not isinstance(base, dict):
            return {"epochs": [], "has_data": False}
        return _normalize_observation_chart_series(provider_id, base.get("_series"))

    return {"epochs": [], "has_data": False}


def _empty_synoptic_series() -> dict:
    return {"has_data": False, "epochs": [], "temps": [], "humidities": [], "pressures": []}


def _synoptic_provider_registry() -> dict[str, dict]:
    return {
        "AEMET": {
            "spinner": "Obteniendo serie sinóptica reciente de AEMET...",
            "loader": lambda station_id: _get_aemet_service().fetch_aemet_recent_synoptic_series(
                station_id,
                days_back=7,
                step_hours=3,
            ),
            "requires_station_id": True,
        },
        "METEOFRANCE": {
            "spinner": "Obteniendo serie sinóptica reciente de Meteo-France...",
            "loader": lambda station_id: _get_meteofrance_service().fetch_meteofrance_recent_synoptic_series(
                station_id,
                _get_meteofrance_service().METEOFRANCE_API_KEY,
                days_back=7,
                step_hours=3,
            ),
        },
        "METEOCAT": {
            "spinner": "Obteniendo serie reciente de Meteocat...",
            "loader": lambda station_id: _get_meteocat_service().fetch_meteocat_recent_synoptic_series(
                station_id,
                days_back=7,
            ),
        },
        "METEOGALICIA": {
            "spinner": "Obteniendo serie reciente de MeteoGalicia...",
            "loader": lambda station_id: _get_meteogalicia_service().fetch_meteogalicia_recent_synoptic_series(
                station_id,
                days_back=7,
                step_hours=3,
            ),
        },
        "EUSKALMET": {"empty": True},
    }


def _fetch_trends_synoptic_series(provider_id: str):
    provider_id = str(provider_id or "").strip().upper()
    station_id = _get_provider_station_id(provider_id)
    provider_feature = get_provider_feature(provider_id)
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
            hourly7d_raw = fetch_hourly_7day_session_cached(station_id, api_key)
        return apply_wu_series_calibration(hourly7d_raw, station_calibration), t(
            str(provider_feature.get("synoptic_source_key", "trends.sources.wu_synoptic"))
        )

    config = _synoptic_provider_registry().get(provider_id)
    if config is not None:
        source_label = t(str(provider_feature.get("synoptic_source_key", "")).strip())
        if config.get("empty"):
            return _empty_synoptic_series(), source_label
        if config.get("requires_station_id") and not station_id:
            return _empty_synoptic_series(), source_label
        spinner_txt = config["spinner"]
        loader = config["loader"]
        with st.spinner(spinner_txt):
            return loader(station_id), source_label
    return state_hourly7d, state_source_label


def _build_observation_tab_context() -> dict:
    connection_type = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()

    def _ensure_observation_chart_data() -> bool:
        connection_type = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
        if connection_type == "WU":
            station_id = str(st.session_state.get(ACTIVE_STATION, "")).strip()
            api_key = str(st.session_state.get(ACTIVE_KEY, "")).strip()
            if not station_id:
                station_id = str(st.session_state.get("wu_connected_station", "")).strip()
            if not api_key:
                api_key = str(st.session_state.get("wu_connected_api_key", "")).strip()
            station_id = station_id.upper()
            if not station_id or not api_key:
                return False
            if _chart_series_fresh_for_station(connection_type, station_id):
                return True

            station_calibration = st.session_state.get("wu_station_calibration", default_wu_calibration())
            timeseries_raw = fetch_daily_timeseries_session_cached(
                station_id,
                api_key,
                ttl_s=REFRESH_SECONDS,
            )
            timeseries = apply_wu_series_calibration(timeseries_raw, station_calibration)
            chart_epochs = timeseries.get("epochs", [])
            chart_temps = timeseries.get("temps", [])
            chart_humidities = timeseries.get("humidities", [])
            chart_dewpts = timeseries.get("dewpts", [])
            _cp_msl = timeseries.get("pressures", [])
            _msl_factor = math.exp(-z / 8000.0)
            chart_pressures = [
                p * _msl_factor if not is_nan(p) else float("nan")
                for p in _cp_msl
            ]
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

            wu_sensor_presence = detect_wu_sensor_presence(base, timeseries_raw)
            st.session_state["wu_sensor_presence"] = wu_sensor_presence
            st.session_state["wu_sensor_presence_station"] = station_id

            if len(chart_humidities) == 0 or all(is_nan(h) for h in chart_humidities):
                chart_humidities = _humidity_series_from_temp_and_dewpoint(chart_temps, chart_dewpts)

            chart_series = store_chart_series(
                st.session_state,
                {
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
                    "has_data": timeseries.get("has_data", False),
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

        chart_has_data = bool(chart_series.get("has_data")) or len(chart_series.get("epochs", [])) > 0
        if has_radiation and chart_has_data:
            chart_epochs = chart_series.get("epochs", [])
            chart_temps = chart_series.get("temps", [])
            chart_humidities = chart_series.get("humidities", [])
            chart_solar_radiations = chart_series.get("solar_radiations", [])
            chart_winds = chart_series.get("winds", [])
            et0_chart, balance_chart = _accumulate_et0_from_series(
                chart_epochs=chart_epochs,
                chart_temps=chart_temps,
                chart_humidities=chart_humidities,
                chart_solar_radiations=chart_solar_radiations,
                chart_winds=chart_winds,
                fallback_wind=base.get("wind", 2.0),
                lat=base.get("lat", float("nan")),
                elevation_m=z,
                precip_total=base.get("precip_total", float("nan")),
            )
            st.session_state["chart_et0"] = et0_chart
            st.session_state["chart_balance"] = balance_chart
        else:
            st.session_state["chart_et0"] = float("nan")
            st.session_state["chart_balance"] = float("nan")

        return chart_has_data

    return build_observation_context(
        {
            "RD": RD,
            "Te": Te,
            "Tv": Tv,
            "Tw": Tw,
            "ensure_chart_data": _ensure_observation_chart_data,
            "_fmt_precip_display": _fmt_precip_display,
            "_fmt_pressure_display": _fmt_pressure_display,
            "_fmt_radiation_display": _fmt_radiation_display,
            "_fmt_radiation_energy_display": _fmt_radiation_energy_display,
            "_fmt_temp_display": _fmt_temp_display,
            "_fmt_wind_display": _fmt_wind_display,
            "_get_aemet_service": _get_aemet_service,
            "_infer_series_step_minutes": _infer_series_step_minutes,
            "_plotly_chart_stretch": _plotly_chart_stretch,
            "_translate_balance_label": _translate_balance_label,
            "_translate_clarity_label": _translate_clarity_label,
            "_translate_pressure_trend_label": _translate_pressure_trend_label,
            "_translate_rain_intensity_label": _translate_rain_intensity_label,
            "_translate_sunrise_sunset_label": _translate_sunrise_sunset_label,
            "balance": balance,
            "base": base,
            "card": card,
            "clarity": clarity,
            "connected": connected,
            "connection_type": connection_type,
            "convert_pressure": convert_pressure,
            "convert_radiation": convert_radiation,
            "convert_temperature": convert_temperature,
            "convert_wind": convert_wind,
            "dark": dark,
            "dp3": dp3,
            "e": e,
            "et0": et0,
            "has_chart_data": has_chart_data,
            "has_radiation": has_radiation,
            "html": html,
            "inst_label": inst_label,
            "inst_mm_h": inst_mm_h,
            "is_nan": is_nan,
            "lcl": lcl,
            "logger": logger,
            "p_abs": p_abs,
            "p_arrow": p_arrow,
            "p_label": p_label,
            "p_msl": p_msl,
            "precip_unit_txt": precip_unit_txt,
            "pressure_unit_pref": pressure_unit_pref,
            "pressure_unit_txt": pressure_unit_txt,
            "q_gkg": q_gkg,
            "r1_mm_h": r1_mm_h,
            "r5_mm_h": r5_mm_h,
            "radiation_energy_unit_txt": radiation_energy_unit_txt,
            "radiation_unit_pref": radiation_unit_pref,
            "radiation_unit_txt": radiation_unit_txt,
            "render_grid": render_grid,
            "rho": rho,
            "rho_v_gm3": rho_v_gm3,
            "section_title": section_title,
            "sky_clarity_label": sky_clarity_label,
            "solar_rad": solar_rad,
            "st": st,
            "t": t,
            "temp_unit_pref": temp_unit_pref,
            "temp_unit_txt": temp_unit_txt,
            "theme_mode": theme_mode,
            "theta": theta,
            "time": time,
            "uv": uv,
            "water_balance_label": water_balance_label,
            "wind_dir_text": wind_dir_text,
            "wind_unit_pref": wind_unit_pref,
            "wind_unit_txt": wind_unit_txt,
            "z": z,
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
        "_get_aemet_service": _get_aemet_service,
        "_get_meteocat_service": _get_meteocat_service,
        "_get_meteofrance_service": _get_meteofrance_service,
        "_render_neutral_info_note": _render_neutral_info_note,
        "_infer_series_step_minutes": _infer_series_step_minutes,
        "_fetch_trends_synoptic_series": _fetch_trends_synoptic_series,
        "_get_provider_station_id": _get_provider_station_id,
        "_plotly_chart_stretch": _plotly_chart_stretch,
        "convert_temperature_delta": convert_temperature_delta,
        "convert_pressure": convert_pressure,
        "convert_wind": convert_wind,
        "is_nan": is_nan,
        "e_s": e_s,
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
        "WuError": WuError,
        "_render_neutral_info_note": _render_neutral_info_note,
        "_get_provider_station_id": _get_provider_station_id,
        "_get_provider_api_key": _get_provider_api_key,
        "_render_historical_provider_series_start": _render_historical_provider_series_start,
        "_get_historical_missing_message": _get_historical_missing_message,
        "_get_climograms_service": _get_climograms_service,
        "_get_frost_service": _get_frost_service,
        "_get_provider_label": _get_provider_label,
        "_fetch_historical_dataset": lambda **kwargs: fetch_historical_dataset(
            **kwargs,
            get_frost_service=_get_frost_service,
            get_meteocat_service=_get_meteocat_service,
            get_aemet_service=_get_aemet_service,
            get_meteofrance_service=_get_meteofrance_service,
            get_meteogalicia_service=_get_meteogalicia_service,
        ),
        "_render_theme_table": _render_theme_table,
        "_plotly_chart_stretch": _plotly_chart_stretch,
    }


def _build_map_tab_context() -> dict:
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
        "_pydeck_chart_stretch": _pydeck_chart_stretch,
    }


TAB_OPTIONS = ["observation", "trends", "historical", "map"]
LEGACY_TAB_ALIASES = {
    "Observación": "observation",
    "Tendencias": "trends",
    "Climogramas": "historical",
    "Histórico": "historical",
    "Mapa": "map",
}


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
        st.session_state["active_tab"] = TAB_OPTIONS[0]
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


def _build_demo_radiation_series(
    current_solar: float,
    current_uv: float,
    *,
    now_dt: Optional[datetime] = None,
) -> dict:
    """Serie diaria sintética para visualizar radiación/UV en modo DEMO."""
    now_local = now_dt or datetime.now().astimezone()
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    step_seconds = 5 * 60
    total_steps = max(2, int((now_local - day_start).total_seconds() // step_seconds) + 1)

    # El slider del DEMO representa una intensidad "típica" del tramo fuerte del día,
    # no un valor a extrapolar desde la hora real actual. Así evitamos picos absurdos
    # cuando se prueba de noche con UVI manual.
    solar_noon_hour = 14.0
    sigma_hours = 2.8
    solar_peak = min(1200.0, max(0.0, float(current_solar) if current_solar > 0 else 850.0))
    uv_peak = min(15.0, max(0.0, float(current_uv) if current_uv > 0 else 8.5))

    epochs = []
    solar_radiations = []
    uv_indexes = []
    temps = []
    humidities = []
    winds = []
    gusts = []
    wind_dirs = []

    for step_idx in range(total_steps):
        point_dt = day_start + timedelta(seconds=step_idx * step_seconds)
        point_hour = point_dt.hour + (point_dt.minute / 60.0)
        diurnal_shape = math.exp(-((point_hour - solar_noon_hour) ** 2) / (2.0 * sigma_hours ** 2))

        solar_val = solar_peak * (diurnal_shape ** 1.02)
        uv_val = uv_peak * (diurnal_shape ** 1.18)
        if diurnal_shape < 0.08:
            solar_val = 0.0
        if diurnal_shape < 0.10:
            uv_val = 0.0

        # Variables auxiliares plausibles para que ET0 y balance también se vean.
        temp_shape = math.exp(-((point_hour - 16.0) ** 2) / (2.0 * 4.6 ** 2))
        rh_shape = math.exp(-((point_hour - 14.5) ** 2) / (2.0 * 4.0 ** 2))
        wind_shape = math.exp(-((point_hour - 15.5) ** 2) / (2.0 * 4.8 ** 2))

        epochs.append(int(point_dt.timestamp()))
        solar_radiations.append(float(max(0.0, solar_val)))
        uv_indexes.append(float(max(0.0, uv_val)))
        temps.append(float(14.5 + (11.0 * temp_shape)))
        humidities.append(float(max(28.0, min(92.0, 82.0 - (30.0 * rh_shape)))))
        winds.append(float(6.0 + (8.0 * wind_shape)))
        gusts.append(float(8.5 + (11.0 * wind_shape)))
        wind_dirs.append(210.0)

    return {
        "epochs": epochs,
        "solar_radiations": solar_radiations,
        "uv_indexes": uv_indexes,
        "temps": temps,
        "humidities": humidities,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": wind_dirs,
        "has_data": bool(epochs),
    }


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


def _ensure_plotly_template(fig) -> None:
    """Configura el template de Plotly bajo demanda, justo antes de renderizar."""
    try:
        import plotly.io as pio
    except Exception:
        return

    if dark:
        template_name = "meteolabx_dark"
        if template_name not in pio.templates:
            pio.templates[template_name] = pio.templates["plotly_dark"]
            pio.templates[template_name].layout.font.color = "rgba(255, 255, 255, 0.92)"
            pio.templates[template_name].layout.title.font.color = "rgba(255, 255, 255, 0.92)"
            pio.templates[template_name].layout.xaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
            pio.templates[template_name].layout.yaxis.title.font.color = "rgba(255, 255, 255, 0.92)"
    else:
        template_name = "meteolabx_light"
        if template_name not in pio.templates:
            pio.templates[template_name] = pio.templates["plotly_white"]
            pio.templates[template_name].layout.font.color = "rgba(15, 18, 25, 0.92)"
            pio.templates[template_name].layout.title.font.color = "rgba(15, 18, 25, 0.92)"
            pio.templates[template_name].layout.xaxis.title.font.color = "rgba(15, 18, 25, 0.92)"
            pio.templates[template_name].layout.yaxis.title.font.color = "rgba(15, 18, 25, 0.92)"

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


def _translate_pressure_trend_label(label: str) -> str:
    mapping = {
        "Estable": "stable",
        "Subiendo rápido": "rising_fast",
        "Subiendo": "rising",
        "Bajando rápido": "falling_fast",
        "Bajando": "falling",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.pressure.{key}") if key else str(label or "—")


def _translate_rain_intensity_label(label: str) -> str:
    mapping = {
        "Sin precipitación": "no_precip",
        "Traza de precipitación": "trace",
        "Lluvia muy débil": "very_light",
        "Lluvia débil": "light",
        "Lluvia ligera": "light_moderate",
        "Lluvia moderada": "moderate",
        "Lluvia fuerte": "heavy",
        "Lluvia muy fuerte": "very_heavy",
        "Lluvia torrencial": "torrential",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.rain.{key}") if key else str(label or "—")


def _translate_clarity_label(label: str) -> str:
    mapping = {
        "Despejado": "clear",
        "Poco nuboso": "mostly_clear",
        "Parcialmente nuboso": "partly_cloudy",
        "Nuboso": "cloudy",
        "Muy nuboso": "very_cloudy",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.clarity.{key}") if key else str(label or "—")


def _translate_balance_label(label: str) -> str:
    mapping = {
        "Superávit": "surplus",
        "Positivo": "positive",
        "Equilibrio": "balance",
        "Déficit": "deficit",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.balance.{key}") if key else str(label or "—")


def _translate_sunrise_sunset_label(label: str) -> str:
    text = str(label or "").strip()
    if not text or "·" not in text:
        return text
    left, right = [part.strip() for part in text.split("·", 1)]
    sunrise = left.replace("Orto", "").replace("Sunrise", "").strip()
    sunset = right.replace("Ocaso", "").replace("Sunset", "").strip()
    if not sunrise and not sunset:
        return text
    return t("observation.cards.radiation.sky_clarity.sunrise_sunset", sunrise=sunrise, sunset=sunset)


def _inject_mobile_plotly_compactor() -> None:
    """Compacta gráficos Plotly solo en viewports pequeños desde el DOM padre."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          if (!doc) return;

          function isSmallViewport() {
            const vw = Math.round(host.innerWidth || doc.documentElement.clientWidth || 0);
            return vw > 0 && vw <= 900;
          }

          function isNarrowPlot(plot) {
            if (!plot || typeof plot.getBoundingClientRect !== "function") return false;
            const rect = plot.getBoundingClientRect();
            const width = Math.round(rect && rect.width ? rect.width : 0);
            return width > 0 && width <= 460;
          }

          function titleText(axis) {
            if (!axis || axis.title == null) return "";
            if (typeof axis.title === "string") return axis.title;
            return axis.title.text || "";
          }

          function getPlotlyApi(plot) {
            const candidates = [
              host.Plotly,
              window.Plotly,
              plot && plot.ownerDocument && plot.ownerDocument.defaultView && plot.ownerDocument.defaultView.Plotly
            ];
            for (const candidate of candidates) {
              if (candidate && typeof candidate.relayout === "function") return candidate;
            }
            return null;
          }

          function captureOriginal(plot) {
            if (plot.__mlbxOriginalLayout) return plot.__mlbxOriginalLayout;
            const layout = plot.layout || {};
            plot.__mlbxOriginalLayout = {
              margin: {
                l: layout.margin && layout.margin.l != null ? layout.margin.l : 60,
                r: layout.margin && layout.margin.r != null ? layout.margin.r : 40,
                t: layout.margin && layout.margin.t != null ? layout.margin.t : 60,
                b: layout.margin && layout.margin.b != null ? layout.margin.b : 60
              },
              height: layout.height != null ? layout.height : null,
              titleFontSize: layout.title && layout.title.font && layout.title.font.size != null ? layout.title.font.size : null,
              legendFontSize: layout.legend && layout.legend.font && layout.legend.font.size != null ? layout.legend.font.size : null,
              xaxis: {
                title: titleText(layout.xaxis),
                dtick: layout.xaxis && layout.xaxis.dtick != null ? layout.xaxis.dtick : null,
                tickformat: layout.xaxis && layout.xaxis.tickformat != null ? layout.xaxis.tickformat : null,
                tickangle: layout.xaxis && layout.xaxis.tickangle != null ? layout.xaxis.tickangle : 0,
                automargin: !!(layout.xaxis && layout.xaxis.automargin),
                nticks: layout.xaxis && layout.xaxis.nticks != null ? layout.xaxis.nticks : null,
                tickfontSize: layout.xaxis && layout.xaxis.tickfont && layout.xaxis.tickfont.size != null ? layout.xaxis.tickfont.size : null,
                fixedrange: !!(layout.xaxis && layout.xaxis.fixedrange)
              },
              yaxis: {
                title: titleText(layout.yaxis),
                automargin: !!(layout.yaxis && layout.yaxis.automargin),
                tickfontSize: layout.yaxis && layout.yaxis.tickfont && layout.yaxis.tickfont.size != null ? layout.yaxis.tickfont.size : null,
                ticklabelposition: layout.yaxis && layout.yaxis.ticklabelposition != null ? layout.yaxis.ticklabelposition : null,
                ticklabelstandoff: layout.yaxis && layout.yaxis.ticklabelstandoff != null ? layout.yaxis.ticklabelstandoff : null,
                fixedrange: !!(layout.yaxis && layout.yaxis.fixedrange)
              },
              yaxis2: {
                title: titleText(layout.yaxis2),
                automargin: !!(layout.yaxis2 && layout.yaxis2.automargin),
                tickfontSize: layout.yaxis2 && layout.yaxis2.tickfont && layout.yaxis2.tickfont.size != null ? layout.yaxis2.tickfont.size : null,
                ticklabelposition: layout.yaxis2 && layout.yaxis2.ticklabelposition != null ? layout.yaxis2.ticklabelposition : null,
                ticklabelstandoff: layout.yaxis2 && layout.yaxis2.ticklabelstandoff != null ? layout.yaxis2.ticklabelstandoff : null,
                fixedrange: !!(layout.yaxis2 && layout.yaxis2.fixedrange)
              }
            };
            return plot.__mlbxOriginalLayout;
          }

          function compactPlot(plot) {
            if (!plot || !plot.layout || !plot.layout.xaxis) return;
            const plotlyApi = getPlotlyApi(plot);
            if (!plotlyApi) return;
            const original = captureOriginal(plot);
            if (plot.dataset.mlbxCompactMode === "mobile") return;
            const currentHeight = plot.layout && plot.layout.height != null ? plot.layout.height : null;
            const targetHeight = currentHeight == null || currentHeight > 312 ? 272 : currentHeight;
            plotlyApi.relayout(plot, {
              "margin.l": 16,
              "margin.r": 12,
              "margin.t": 52,
              "margin.b": 30,
              "height": targetHeight,
              "title.font.size": 15,
              "legend.font.size": 9,
              "xaxis.title.text": "",
              "xaxis.dtick": 3 * 60 * 60 * 1000,
              "xaxis.tickformat": "%H",
              "xaxis.tickangle": 0,
              "xaxis.automargin": false,
              "xaxis.nticks": 4,
              "xaxis.tickfont.size": 11,
              "xaxis.ticklabeloverflow": "allow",
              "xaxis.fixedrange": true,
              "yaxis.title.text": "",
              "yaxis.automargin": false,
              "yaxis.tickfont.size": 11,
              "yaxis.ticklabelposition": "inside",
              "yaxis.ticklabelstandoff": -2,
              "yaxis.fixedrange": true,
              "yaxis2.title.text": "",
              "yaxis2.automargin": false,
              "yaxis2.tickfont.size": 11,
              "yaxis2.ticklabelposition": "inside",
              "yaxis2.ticklabelstandoff": -2,
              "yaxis2.fixedrange": true,
              "dragmode": false
            }).then(function () {
              plot.dataset.mlbxCompactMode = "mobile";
            }).catch(function () {});
          }

          function restorePlot(plot) {
            const original = plot && plot.__mlbxOriginalLayout;
            if (!plot || !original || plot.dataset.mlbxCompactMode !== "mobile") return;
            const plotlyApi = getPlotlyApi(plot);
            if (!plotlyApi) return;
            plotlyApi.relayout(plot, {
              "margin.l": original.margin.l,
              "margin.r": original.margin.r,
              "margin.t": original.margin.t,
              "margin.b": original.margin.b,
              "height": original.height,
              "title.font.size": original.titleFontSize,
              "legend.font.size": original.legendFontSize,
              "xaxis.title.text": original.xaxis.title,
              "xaxis.dtick": original.xaxis.dtick,
              "xaxis.tickformat": original.xaxis.tickformat,
              "xaxis.tickangle": original.xaxis.tickangle,
              "xaxis.automargin": original.xaxis.automargin,
              "xaxis.nticks": original.xaxis.nticks,
              "xaxis.tickfont.size": original.xaxis.tickfontSize,
              "xaxis.fixedrange": original.xaxis.fixedrange,
              "yaxis.title.text": original.yaxis.title,
              "yaxis.automargin": original.yaxis.automargin,
              "yaxis.tickfont.size": original.yaxis.tickfontSize,
              "yaxis.ticklabelposition": original.yaxis.ticklabelposition,
              "yaxis.ticklabelstandoff": original.yaxis.ticklabelstandoff,
              "yaxis.fixedrange": original.yaxis.fixedrange,
              "yaxis2.title.text": original.yaxis2.title,
              "yaxis2.automargin": original.yaxis2.automargin,
              "yaxis2.tickfont.size": original.yaxis2.tickfontSize,
              "yaxis2.ticklabelposition": original.yaxis2.ticklabelposition,
              "yaxis2.ticklabelstandoff": original.yaxis2.ticklabelstandoff,
              "yaxis2.fixedrange": original.yaxis2.fixedrange
            }).then(function () {
              plot.dataset.mlbxCompactMode = "desktop";
            }).catch(function () {});
          }

          function syncPlots() {
            const plots = Array.from(doc.querySelectorAll('[data-testid="stPlotlyChart"] .js-plotly-plot'));
            plots.forEach(function (plot) {
              if (isSmallViewport() || isNarrowPlot(plot)) compactPlot(plot);
              else restorePlot(plot);
            });
          }

          function schedulePlotSync() {
            if (host.__mlbxViewportPlotRaf) return;
            host.__mlbxViewportPlotRaf = host.requestAnimationFrame(function () {
              host.__mlbxViewportPlotRaf = null;
              syncPlots();
            });
          }

          function bootstrapPlotSync(attempts) {
            schedulePlotSync();
            if (attempts <= 0) return;
            host.setTimeout(function () {
              bootstrapPlotSync(attempts - 1);
            }, 350);
          }

          bootstrapPlotSync(10);

          if (!host.__mlbxViewportPlotResizeBound) {
            host.__mlbxViewportPlotResizeBound = true;
            host.addEventListener("resize", schedulePlotSync, { passive: true });
            host.addEventListener("pageshow", schedulePlotSync, { passive: true });
          }

          if (!host.__mlbxViewportPlotObserverBound && host.MutationObserver) {
            host.__mlbxViewportPlotObserverBound = true;
            const observer = new host.MutationObserver(function () {
              schedulePlotSync();
            });
            observer.observe(doc.body, { childList: true, subtree: true });
            host.__mlbxViewportPlotObserver = observer;
          }
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _inject_live_age_updater() -> None:
    """Mantiene actualizados edad y hora local del usuario sin esperar a un rerun completo."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          if (!doc || !doc.body) return;

          function formatAge(epoch) {
            const now = Math.floor(Date.now() / 1000);
            const diff = Math.max(0, now - epoch);
            if (diff < 60) return `${diff}s`;
            if (diff < 3600) return `${Math.floor(diff / 60)}m`;
            return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
          }

          function formatLocalDateTime(epoch) {
            const date = new Date(epoch * 1000);
            if (!Number.isFinite(date.getTime())) return "";
            const pad = function (value) {
              return String(value).padStart(2, "0");
            };
            return [
              pad(date.getDate()),
              pad(date.getMonth() + 1),
              date.getFullYear()
            ].join("-") + " " + [
              pad(date.getHours()),
              pad(date.getMinutes()),
              pad(date.getSeconds())
            ].join(":");
          }

          function refreshUserTimes() {
            doc.querySelectorAll(".mlbx-live-user-time[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              const text = formatLocalDateTime(epoch);
              if (text && el.textContent !== text) el.textContent = text;
            });
            doc.querySelectorAll(".mlbx-live-user-time-label").forEach(function (el) {
              const fallback = el.getAttribute("data-fallback-label") || "Hora usuario";
              if (el.textContent !== fallback) el.textContent = fallback;
            });
          }

          function refreshAges() {
            refreshUserTimes();
            doc.querySelectorAll(".mlbx-live-age[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              const text = formatAge(epoch);
              if (el.textContent !== text) el.textContent = text;
            });
          }

          function runNow() {
            try {
              refreshAges();
            } catch (err) {
              console.debug("MeteoLabX age refresh error", err);
            }
          }

          function ensureInterval() {
            if (host.__mlbxAgeInterval) {
              host.clearInterval(host.__mlbxAgeInterval);
            }
            host.__mlbxAgeInterval = host.setInterval(runNow, 1000);
          }

          if (!host.__mlbxAgeRefreshBound) {
            host.__mlbxAgeRefreshBound = true;
            host.addEventListener("pageshow", runNow, { passive: true });
            host.addEventListener("focus", runNow, { passive: true });
            doc.addEventListener("visibilitychange", runNow, { passive: true });
            if (host.MutationObserver && doc.body) {
              host.__mlbxAgeObserver = new host.MutationObserver(runNow);
              host.__mlbxAgeObserver.observe(doc.body, { childList: true, subtree: true });
            }
          }

          runNow();
          ensureInterval();
        })();
        </script>
        """,
        height=0,
        width=0,
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

from dataclasses import dataclass


def _humidity_series_from_temp_and_dewpoint(temps_like, dewpoints_like) -> list[float]:
    humidities: list[float] = []
    for temp, td in zip(temps_like, dewpoints_like):
        if is_nan(temp) or is_nan(td):
            humidities.append(float("nan"))
            continue
        e_td = e_s(td)
        e_s_t = e_s(temp)
        humidities.append(100.0 * e_td / e_s_t if e_s_t > 0 else float("nan"))
    return humidities


def _accumulate_et0_from_series(
    *,
    chart_epochs,
    chart_temps,
    chart_humidities,
    chart_solar_radiations,
    chart_winds,
    fallback_wind,
    lat,
    elevation_m,
    precip_total,
):
    from models.radiation import penman_monteith_et0

    et0_accum = 0.0
    valid_steps = 0
    fallback_wind = 2.0 if is_nan(fallback_wind) else fallback_wind

    for i, epoch_i in enumerate(chart_epochs):
        solar_i = chart_solar_radiations[i] if i < len(chart_solar_radiations) else float("nan")
        temp_i = chart_temps[i] if i < len(chart_temps) else float("nan")
        rh_i = chart_humidities[i] if i < len(chart_humidities) else float("nan")
        if is_nan(solar_i) or solar_i < 0 or is_nan(temp_i) or is_nan(rh_i):
            continue

        wind_i = chart_winds[i] if i < len(chart_winds) else float("nan")
        if is_nan(wind_i):
            wind_i = fallback_wind
        if wind_i < 0.1:
            wind_i = 0.1

        et0_i = penman_monteith_et0(
            solar_i,
            temp_i,
            rh_i,
            wind_i,
            lat,
            elevation_m,
            float(epoch_i),
        )
        if is_nan(et0_i):
            continue

        step_hours = 5.0 / 60.0
        if i > 0:
            try:
                dt_seconds = float(epoch_i) - float(chart_epochs[i - 1])
                if 120 <= dt_seconds <= 1800:
                    step_hours = dt_seconds / 3600.0
            except Exception:
                pass

        et0_accum += et0_i / 24.0 * step_hours
        valid_steps += 1

    et0 = et0_accum if valid_steps > 0 else float("nan")
    balance = water_balance(precip_total, et0) if not is_nan(et0) else float("nan")
    return et0, balance


def _precip_rate_between_last_measurements(epochs, precip_values) -> float:
    """Calcula intensidad de lluvia desde las dos últimas muestras de serie.

    Las APIs no-WU suelen exponer lluvia acumulada por día o lluvia del intervalo.
    Detectamos series mayormente monótonas como acumuladas; si no, tratamos el
    último valor como lluvia caída durante el último intervalo.
    """
    points = []
    for ep, precip in zip(epochs or [], precip_values or []):
        try:
            ep_i = int(float(ep))
            precip_f = float(precip)
        except (TypeError, ValueError):
            continue
        if ep_i <= 0 or is_nan(precip_f):
            continue
        points.append((ep_i, max(0.0, precip_f)))

    if len(points) < 2:
        return float("nan")

    points.sort(key=lambda item: item[0])
    values = [value for _, value in points]
    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    non_negative_ratio = (
        sum(1 for diff in diffs if diff >= -0.05) / len(diffs)
        if diffs else 1.0
    )
    looks_cumulative = non_negative_ratio >= 0.8

    latest_ep, latest_precip = points[-1]
    for previous_ep, previous_precip in reversed(points[:-1]):
        dt_seconds = latest_ep - previous_ep
        if dt_seconds <= 0:
            continue
        if looks_cumulative:
            delta_mm = latest_precip - previous_precip
            if delta_mm < -0.05:
                delta_mm = latest_precip
            delta_mm = max(0.0, delta_mm)
        else:
            delta_mm = latest_precip
        return (delta_mm / dt_seconds) * 3600.0

    return float("nan")


def _precip_rate_from_series(series: Optional[dict]) -> float:
    """Obtiene la intensidad no-WU a partir de la serie canónica disponible."""
    if not isinstance(series, dict):
        return float("nan")
    epochs = series.get("epochs", [])
    precip_values = []
    for key in ("precips", "precip_accum_mm", "precip_step_mm"):
        values = series.get(key)
        if values is not None and len(values) > 0:
            precip_values = values
            break
    return _precip_rate_between_last_measurements(epochs, precip_values)


@dataclass
class ProcessedData:
    """Variables derivadas del procesamiento post-fetch de un proveedor estándar."""
    z: float
    p_abs: float
    p_msl: float
    p_abs_disp: str
    p_msl_disp: str
    dp3: float
    rate_h: float
    p_label: str
    p_arrow: str
    inst_mm_h: float
    r1_mm_h: float
    r5_mm_h: float
    inst_label: str
    e_sat: float
    e: float
    Td_calc: float
    Tw: float
    q: float
    q_gkg: float
    theta: float
    Tv: float
    Te: float
    rho: float
    rho_v_gm3: float
    lcl: float
    solar_rad: float
    uv: float
    et0: float
    clarity: float
    balance: float
    has_radiation: bool
    has_chart_data: bool


def process_standard_provider(
    base: dict,
    provider_name: str,
    elevation_fallback_key: str,
    series_override: Optional[dict] = None,
    series_7d: Optional[dict] = None,
) -> ProcessedData:
    """Procesamiento post-fetch común a todos los proveedores estándar.

    Parámetros:
        base: dict devuelto por get_xxx_data() con keys canónicas (Tc, RH, p_hpa…)
        provider_name: nombre del proveedor ("EUSKALMET", "METEOCAT"…)
        elevation_fallback_key: key de session_state para altitud de respaldo
        series_override: si no es None, se usa como serie de charts en vez de base["_series"]
        series_7d: si no es None, se escribe en trend_hourly_* de session_state
    """
    NaN = float("nan")

    # 1. Session state: lat, lon, elevation, timestamp
    st.session_state[LAST_UPDATE_TIME] = time.time()
    st.session_state[STATION_LAT] = base.get("lat", NaN)
    st.session_state[STATION_LON] = base.get("lon", NaN)

    z = base.get("elevation", st.session_state.get(elevation_fallback_key, 0))
    st.session_state[STATION_ELEVATION] = z
    st.session_state[ELEVATION_SOURCE] = provider_name

    # 2. Warning de datos antiguos
    data_age_minutes = (time.time() - base["epoch"]) / 60
    if data_age_minutes > MAX_DATA_AGE_MINUTES:
        st.warning(
            f"⚠️ Datos de {provider_name} con {data_age_minutes:.0f} minutos "
            "de antigüedad. La estación puede no estar reportando."
        )
        logger.warning(f"Datos {provider_name} antiguos: {data_age_minutes:.1f} minutos")

    # 3. Lluvia: fuera de WU se calcula desde la serie, no desde historial de tips.
    inst_mm_h = r1_mm_h = r5_mm_h = NaN
    inst_label = rain_intensity_label(inst_mm_h)

    # 4. Presión
    p_abs = float(base.get("p_abs_hpa", NaN))
    p_msl = float(base.get("p_hpa", NaN))
    provider_for_pressure = st.session_state.get(CONNECTION_TYPE, provider_name)
    p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
    p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

    if not is_nan(p_abs):
        init_pressure_history()
        push_pressure(p_abs, base["epoch"])

    # 5. Tendencia presión 3h (desde base primero)
    if not is_nan(p_msl):
        dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
            p_now=p_msl,
            epoch_now=base["epoch"],
            p_3h_ago=base.get("pressure_3h_ago"),
            epoch_3h_ago=base.get("epoch_3h_ago"),
        )
    else:
        dp3, rate_h, p_label, p_arrow = NaN, NaN, "—", "•"

    # 6. Termodinámica
    e_sat = e_v = Td_calc = Tw = q_val = q_gkg = theta = Tv_val = Te_val = NaN
    rho_val = rho_v_gm3 = lcl_val = NaN

    if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
        e_sat = e_s(base["Tc"])
        e_v = vapor_pressure(base["Tc"], base["RH"])
        Td_calc = dewpoint_from_vapor_pressure(e_v)
        Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)
        base["Td"] = Td_calc

        if not is_nan(p_abs):
            q_val = specific_humidity(e_v, p_abs)
            q_gkg = q_val * 1000
            theta = potential_temperature(base["Tc"], p_abs)
            Tv_val = virtual_temperature(base["Tc"], q_val)
            Te_val = equivalent_temperature(base["Tc"], q_val)
            rho_val = air_density(p_abs, Tv_val)
            rho_v_gm3 = absolute_humidity(e_v, base["Tc"])
            lcl_val = lcl_height(base["Tc"], Td_calc)
    else:
        base["Td"] = NaN

    # 6.5 Sensación térmica y Heat Index (calculados, nunca del API)
    wind_fl = base.get("wind", 0.0)
    if is_nan(wind_fl):
        wind_fl = 0.0
    wind_fl_ms = float(wind_fl) / 3.6
    base["feels_like"] = apparent_temperature(base["Tc"], e_v, wind_fl_ms)
    base["heat_index"] = heat_index_rothfusz(base["Tc"], base.get("RH", NaN))

    # 7. Radiación / UV / claridad  (ET0 se acumula desde la serie en paso 8.5)
    solar_rad = base.get("solar_radiation", NaN)
    uv = base.get("uv", NaN)
    has_radiation = not is_nan(solar_rad) or not is_nan(uv)
    et0 = clarity = balance = NaN

    if has_radiation:
        from models.radiation import sky_clarity_index
        lat = base.get("lat", NaN)
        lon = base.get("lon", NaN)
        clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)

    # 8. Series para gráficos
    if series_override is not None:
        series = series_override if isinstance(series_override, dict) else {}
    else:
        raw = base.get("_series")
        series = raw if isinstance(raw, dict) else {}
    inst_mm_h = _precip_rate_from_series(series)
    r1_mm_h = r5_mm_h = NaN
    inst_label = rain_intensity_label(inst_mm_h)
    chart_series = store_chart_series(st.session_state, series)
    chart_epochs = chart_series["epochs"]
    chart_temps = chart_series["temps"]
    chart_humidities = chart_series["humidities"]
    chart_pressures = chart_series["pressures_abs"]
    chart_winds = chart_series["winds"]
    chart_gusts = chart_series["gusts"]
    chart_wind_dirs = chart_series["wind_dirs"]
    chart_uv_indexes = chart_series["uv_indexes"]
    chart_solar_radiations = chart_series["solar_radiations"]
    has_chart_data = chart_series["has_data"]
    owner_station_id = (
        _get_provider_station_id(provider_name)
        or str(base.get("station_code", "")).strip()
        or str(base.get("idema", "")).strip()
    )
    if owner_station_id and (has_chart_data or len(chart_epochs) > 0):
        _set_chart_series_owner(provider_name, owner_station_id)

    # 8.5. ET0 acumulada desde serie — integra cada paso temporal igual que WU
    if has_radiation and chart_solar_radiations:
        et0, balance = _accumulate_et0_from_series(
            chart_epochs=chart_epochs,
            chart_temps=chart_temps,
            chart_humidities=chart_humidities,
            chart_solar_radiations=chart_solar_radiations,
            chart_winds=chart_winds,
            fallback_wind=base.get("wind", 2.0),
            lat=base.get("lat", NaN),
            elevation_m=z,
            precip_total=base["precip_total"],
        )

    # 9. Tendencia presión 3h desde serie (abs → MSL)
    if has_chart_data and len(chart_epochs) == len(chart_pressures):
        press_valid = [
            (int(ep), float(p))
            for ep, p in zip(chart_epochs, chart_pressures)
            if not is_nan(float(p))
        ]
        if len(press_valid) >= 2:
            press_valid.sort(key=lambda x: x[0])
            ep_now, p_abs_now = press_valid[-1]
            target_ep = ep_now - (3 * 3600)
            ep_3h, p_abs_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))
            msl_factor = math.exp(z / 8000.0)
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_abs_now * msl_factor,
                epoch_now=ep_now,
                p_3h_ago=p_abs_3h * msl_factor,
                epoch_3h_ago=ep_3h,
            )

    # 10. Trend hourly opcional (MeteoGalicia, NWS)
    if series_7d is not None:
        if isinstance(series_7d, dict) and series_7d.get("has_data"):
            store_trend_hourly_series(st.session_state, series_7d)
        else:
            store_trend_hourly_series(st.session_state, chart_series)
    else:
        store_trend_hourly_series(st.session_state, None)

    return ProcessedData(
        z=z, p_abs=p_abs, p_msl=p_msl, p_abs_disp=p_abs_disp, p_msl_disp=p_msl_disp,
        dp3=dp3, rate_h=rate_h, p_label=p_label, p_arrow=p_arrow,
        inst_mm_h=inst_mm_h, r1_mm_h=r1_mm_h, r5_mm_h=r5_mm_h, inst_label=inst_label,
        e_sat=e_sat, e=e_v, Td_calc=Td_calc, Tw=Tw, q=q_val, q_gkg=q_gkg,
        theta=theta, Tv=Tv_val, Te=Te_val, rho=rho_val, rho_v_gm3=rho_v_gm3, lcl=lcl_val,
        solar_rad=solar_rad, uv=uv, et0=et0, clarity=clarity, balance=balance,
        has_radiation=has_radiation, has_chart_data=has_chart_data,
    )


def _unpack_processed(r: ProcessedData) -> tuple:
    """Desempaqueta ProcessedData en la tupla de variables locales que espera el display."""
    return (
        r.z, r.p_abs, r.p_msl, r.p_abs_disp, r.p_msl_disp,
        r.dp3, r.rate_h, r.p_label, r.p_arrow,
        r.inst_mm_h, r.r1_mm_h, r.r5_mm_h, r.inst_label,
        r.e_sat, r.e, r.Td_calc, r.Tw, r.q, r.q_gkg,
        r.theta, r.Tv, r.Te, r.rho, r.rho_v_gm3, r.lcl,
        r.solar_rad, r.uv, r.et0, r.clarity, r.balance,
        r.has_radiation, r.has_chart_data,
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

sync_browser_context_early()
hydrate_browser_context_live(get_browser_context)
theme_mode, dark = render_sidebar()
active_tab = _sync_active_tab_state()
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


def _fmt_temp_delta_display(value, decimals: int = 1) -> str:
    return format_temperature_delta(value, temp_unit_pref, decimals=decimals)


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
):
    """Cache corto para que cambiar el tema no dispare de nuevo toda la búsqueda del mapa."""
    from providers import search_nearby_stations

    return search_nearby_stations(
        lat,
        lon,
        max_results=max_results,
        provider_ids=list(provider_ids),
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
<style data-sidebar-theme="{sidebar_css_hash}">
/* Forzar tema de sidebar */
[data-testid="stSidebar"] {{
    background-color: {sidebar_bg} !important;
    color-scheme: {theme_color_scheme} !important;
    --mlbx-control-bg: {'#ffffff' if not dark else '#0e1117'};
    --mlbx-control-bg-hover: {'#f3f5fa' if not dark else '#141821'};
    --mlbx-control-border: {button_border};
    --mlbx-sidebar-text: {sidebar_text};
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

[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[aria-checked="true"] p,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] *,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] div,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] span,
[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-baseweb="button-group"] > button[data-testid="stBaseButton-segmented_controlActive"] p,
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
    <style>
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
    <style>
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
<style>
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

_inject_pwa_metadata()

# CSS de componentes y responsive mobile
st.markdown(html_clean("""
<style>
  .block-container { 
    padding-top: 1.2rem; 
    max-width: 1200px;
  }

  .header{
    display:flex; 
    align-items:center; 
    justify-content:space-between;
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
      padding-top: 0.8rem;
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
        "WU": REFRESH_SECONDS,
    }
    return int(defaults.get(provider_id, REFRESH_SECONDS))


def _pressure_decimals_for_provider(provider_id: str) -> int:
    return 0 if str(provider_id).strip().upper() == "WU" else 1


def _fmt_pressure_for_provider(value, provider_id: str) -> str:
    try:
        v = float(value)
    except Exception:
        return "—"
    if is_nan(v):
        return "—"
    decimals = _pressure_decimals_for_provider(provider_id)
    return f"{v:.{decimals}f}"


def _total_catalog_stations() -> int:
    return int(STATION_CATALOG_TOTAL)


header_refresh_seconds = _provider_refresh_seconds() if st.session_state.get(CONNECTED, False) else REFRESH_SECONDS
header_refresh_label = (
    f"{header_refresh_seconds // 60} min"
    if header_refresh_seconds % 60 == 0 and header_refresh_seconds >= 60
    else f"{header_refresh_seconds}s"
)

render_app_header(
    t=t,
    dark=dark,
    header_refresh_label=header_refresh_label,
    total_station_count=_total_catalog_stations(),
)


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
if loading_in_progress:
    render_connection_loading_overlay(connection_loading_payload, title_text=t("connection.loading_title"), dark=dark)
else:
    clear_connection_loading_overlay()



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
    "temp_max": None,
    "temp_min": None,
    "rh_max": None,
    "rh_min": None,
    "gust_max": None,
}

z = 0
inst_mm_h = float("nan")
r1_mm_h = float("nan")
r5_mm_h = float("nan")
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

skip_live_refresh = bool(connected and active_tab in {"trends", "historical", "map"})
runtime_snapshot = _load_runtime_snapshot() if skip_live_refresh else {}
if runtime_snapshot:
    base = dict(runtime_snapshot.get("base", base))
    z = runtime_snapshot.get("z", z)
    inst_mm_h = runtime_snapshot.get("inst_mm_h", inst_mm_h)
    r1_mm_h = runtime_snapshot.get("r1_mm_h", r1_mm_h)
    r5_mm_h = runtime_snapshot.get("r5_mm_h", r5_mm_h)
    inst_label = runtime_snapshot.get("inst_label", inst_label)
    p_abs = runtime_snapshot.get("p_abs", p_abs)
    p_msl = runtime_snapshot.get("p_msl", p_msl)
    p_abs_disp = runtime_snapshot.get("p_abs_disp", p_abs_disp)
    p_msl_disp = runtime_snapshot.get("p_msl_disp", p_msl_disp)
    dp3 = runtime_snapshot.get("dp3", dp3)
    p_label = runtime_snapshot.get("p_label", p_label)
    p_arrow = runtime_snapshot.get("p_arrow", p_arrow)
    e = runtime_snapshot.get("e", e)
    q_gkg = runtime_snapshot.get("q_gkg", q_gkg)
    theta = runtime_snapshot.get("theta", theta)
    Tv = runtime_snapshot.get("Tv", Tv)
    Te = runtime_snapshot.get("Te", Te)
    Tw = runtime_snapshot.get("Tw", Tw)
    lcl = runtime_snapshot.get("lcl", lcl)
    rho = runtime_snapshot.get("rho", rho)
    rho_v_gm3 = runtime_snapshot.get("rho_v_gm3", rho_v_gm3)
    solar_rad = runtime_snapshot.get("solar_rad", solar_rad)
    uv = runtime_snapshot.get("uv", uv)
    et0 = runtime_snapshot.get("et0", et0)
    clarity = runtime_snapshot.get("clarity", clarity)
    balance = runtime_snapshot.get("balance", balance)
    has_radiation = bool(runtime_snapshot.get("has_radiation", has_radiation))
    has_chart_data = bool(runtime_snapshot.get("has_chart_data", has_chart_data))

# Solo calcular datos si está conectado o intentando conectar
if (connected or loading_in_progress) and not runtime_snapshot:
    provider_id = str(st.session_state.get(CONNECTION_TYPE, "")).strip().upper()
    # Determinar origen de datos
    if _get_aemet_service().is_aemet_connection():
        # ========== DATOS DE AEMET ==========
        aemet_service = _get_aemet_service()
        
        # Primero obtener datos históricos (más frescos, cada 10 min)
        (
            chart_epochs,
            chart_temps,
            chart_humidities,
            chart_pressures,
            chart_winds,
            chart_gusts,
            chart_wind_dirs,
            chart_precips,
        ) = aemet_service.get_aemet_daily_charts()
        has_chart_data = len(chart_epochs) > 0
        
        # Obtener dato actual del endpoint normal (puede ser antiguo)
        base = aemet_service.get_aemet_data()
        
        if base is None:
            _cancel_connection_loading()
            err_detail = str(st.session_state.get("aemet_last_error", "")).strip()
            st.warning(
                _friendly_provider_warning(
                    "AEMET",
                    "⚠️ No se pudieron obtener datos de AEMET por ahora. Intenta de nuevo en unos minutos.",
                    err_detail,
                )
            )
            if err_detail:
                st.caption(f"Detalle técnico AEMET: {err_detail}")
            st.stop()
        
        # Si tenemos datos históricos, usar el último punto como dato actual (más fresco)
        if has_chart_data:
            # No heredar máximos de viento del endpoint "actual" (puede venir desfasado)
            base["gust_max"] = None
            
            # Último punto del gráfico
            last_idx = -1
            from datetime import datetime
            chart_last_epoch = chart_epochs[last_idx]
            base_epoch = base.get("epoch", 0)
            use_chart_for_current = (
                is_nan(base_epoch)
                or base_epoch <= 0
                or chart_last_epoch > base_epoch
            )
            
            # Panel principal: usar SIEMPRE la fuente más fresca (actual vs serie)
            if use_chart_for_current:
                base["epoch"] = chart_last_epoch
                if not is_nan(chart_temps[last_idx]):
                    base["Tc"] = chart_temps[last_idx]
                if not is_nan(chart_humidities[last_idx]):
                    base["RH"] = chart_humidities[last_idx]
                if not is_nan(chart_pressures[last_idx]):
                    # PRES diezminutal es presión de estación; forzar recálculo de MSLP
                    base["p_station"] = chart_pressures[last_idx]
                    base["p_hpa"] = None
                if not is_nan(chart_winds[last_idx]):
                    base["wind"] = chart_winds[last_idx]
                if not is_nan(chart_gusts[last_idx]):
                    base["gust"] = chart_gusts[last_idx]
                if not is_nan(chart_wind_dirs[last_idx]):
                    base["wind_dir_deg"] = chart_wind_dirs[last_idx]
                if not is_nan(chart_precips[last_idx]):
                    base["precip_total"] = chart_precips[last_idx]
            
            # Calcular max/min solo del día ACTUAL (desde medianoche de hoy)
            from datetime import datetime
            now_local = datetime.now()
            today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_epoch = int(today_start.timestamp())
            
            # Filtrar solo puntos del día actual
            temps_hoy = []
            gusts_hoy = []
            winds_hoy = []
            precs_hoy = []
            for epoch, temp, gust, wind in zip(chart_epochs, chart_temps, chart_gusts, chart_winds):
                if epoch >= today_start_epoch and not is_nan(temp):
                    temps_hoy.append(temp)
                if epoch >= today_start_epoch:
                    if not is_nan(gust):
                        gusts_hoy.append(gust)
                    if not is_nan(wind):
                        winds_hoy.append(wind)

            for epoch, prec in zip(chart_epochs, chart_precips):
                if epoch >= today_start_epoch and not is_nan(prec):
                    # Normalizar valores negativos espurios
                    precs_hoy.append(max(0.0, float(prec)))
            
            if len(temps_hoy) > 0:
                # La card de temperatura usa temp_max/temp_min
                base["temp_max"] = max(temps_hoy)
                base["temp_min"] = min(temps_hoy)

            wind_candidates = gusts_hoy + winds_hoy
            if len(wind_candidates) > 0:
                # La card de viento usa gust_max en la esquina derecha
                base["gust_max"] = max(wind_candidates)
            else:
                base["gust_max"] = None

            dir_validas = [
                float(d)
                for d, w, g in zip(chart_wind_dirs, chart_winds, chart_gusts)
                if (not is_nan(d)) and (
                    (not is_nan(w) and float(w) > 0.3) or
                    (not is_nan(g) and float(g) > 0.3)
                )
            ]
            if len(dir_validas) == 0:
                base["wind_dir_deg"] = float("nan")

            # Precipitación de hoy desde diezminutal (evitar endpoint actual desfasado)
            if len(precs_hoy) > 0:
                # Detectar si la serie parece acumulada (monótona) o incremental.
                diffs = [precs_hoy[i] - precs_hoy[i - 1] for i in range(1, len(precs_hoy))]
                non_negative_ratio = (
                    sum(1 for d in diffs if d >= -0.05) / len(diffs)
                    if len(diffs) > 0 else 1.0
                )

                if non_negative_ratio >= 0.8:
                    # Serie acumulada: sumar incrementos positivos, tolerando reseteos.
                    total_today = 0.0
                    for i in range(1, len(precs_hoy)):
                        d = precs_hoy[i] - precs_hoy[i - 1]
                        if d >= 0:
                            total_today += d
                        else:
                            # Reset del contador: arrancar desde el nuevo valor.
                            total_today += max(0.0, precs_hoy[i])
                else:
                    # Serie incremental por paso (10 min): sumar directamente.
                    total_today = sum(precs_hoy)

                base["precip_total"] = max(0.0, total_today)
            else:
                base["precip_total"] = float("nan")
            
            store_chart_series(
                st.session_state,
                {
                    "epochs": chart_epochs,
                    "temps": chart_temps,
                    "humidities": chart_humidities,
                    "pressures_abs": chart_pressures,
                    "winds": chart_winds,
                    "gusts": chart_gusts,
                    "wind_dirs": chart_wind_dirs,
                    "precips": chart_precips,
                    "has_data": has_chart_data,
                }
            )
        else:
            # Evitar extremos desfasados cuando no hay serie diezminutal válida
            base["temp_max"] = None
            base["temp_min"] = None
            base["gust_max"] = None
            store_chart_series(st.session_state, None)
        
        # AEMET devuelve datos ya parseados en formato compatible
        # Guardar timestamp
        st.session_state["last_update_time"] = time.time()
        
        # Guardar coordenadas
        st.session_state["station_lat"] = base.get("lat", float("nan"))
        st.session_state["station_lon"] = base.get("lon", float("nan"))
        
        # Altitud de AEMET
        z = base.get("elevation", st.session_state.get("aemet_station_alt", 0))
        st.session_state["station_elevation"] = z
        st.session_state["elevation_source"] = "AEMET"
        
        # Advertir si los datos son muy antiguos
        now_ts = time.time()
        data_age_minutes = (now_ts - base["epoch"]) / 60
        if data_age_minutes > MAX_DATA_AGE_MINUTES:
            st.warning(f"⚠️ Datos de AEMET con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
            logger.warning(f"Datos AEMET antiguos: {data_age_minutes:.1f} minutos")
        
        # ========== PROCESAMIENTO DE DATOS AEMET ==========
        
        # Lluvia: AEMET y demás proveedores no-WU usan la intensidad entre
        # las dos últimas muestras de serie; las ventanas 1/5 min son solo WU.
        inst_mm_h = _precip_rate_between_last_measurements(chart_epochs, chart_precips)
        r1_mm_h = r5_mm_h = float("nan")
        inst_label = rain_intensity_label(inst_mm_h)
        
        # Presión - AEMET puede devolver None si no tiene dato
        p_hpa_raw = base.get("p_hpa")
        if p_hpa_raw is None or p_hpa_raw == "":
            # Si no hay presión nivel del mar, intentar con presión de estación
            p_station_raw = base.get("p_station")
            if p_station_raw is not None and p_station_raw != "":
                # Tenemos presión de estación, calcular MSLP
                p_abs = float(p_station_raw)
                # Calcular MSLP desde presión de estación (inverso de msl_to_absolute)
                # Aproximación simple: p_msl ≈ p_station * exp(z / 8000)
                import math
                p_msl = p_abs * math.exp(z / 8000.0)
            else:
                # No hay ningún dato de presión
                p_msl = float("nan")
                p_abs = float("nan")
        else:
            # Tenemos MSLP, calcular presión absoluta
            p_msl = float(p_hpa_raw)
            p_abs = msl_to_absolute(p_msl, z, base["Tc"])
        
        provider_for_pressure = st.session_state.get("connection_type", "AEMET")
        p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
        p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)
        has_pressure_now = not is_nan(p_msl) and not is_nan(p_abs)
        
        if has_pressure_now:
            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

        if has_pressure_now:
            # Tendencia de presión 3h usando diezminutal (si hay datos de barómetro).
            # Si no hay serie válida, fallback automático al comportamiento existente.
            trend_p_now = p_msl
            trend_epoch_now = base["epoch"]
            trend_p_3h = None
            trend_epoch_3h = None

            if has_chart_data:
                press_valid = []
                for ep, p_st in zip(chart_epochs, chart_pressures):
                    if not is_nan(p_st):
                        press_valid.append((ep, p_st))

                if len(press_valid) >= 2:
                    press_valid.sort(key=lambda x: x[0])
                    ep_now, p_station_now = press_valid[-1]
                    target_ep = ep_now - (3 * 3600)
                    ep_3h, p_station_3h = min(press_valid, key=lambda x: abs(x[0] - target_ep))

                    # Convertir presión de estación a MSL con el mismo factor para ambos puntos
                    import math
                    msl_factor = math.exp(z / 8000.0)
                    trend_p_now = p_station_now * msl_factor
                    trend_epoch_now = ep_now
                    trend_p_3h = p_station_3h * msl_factor
                    trend_epoch_3h = ep_3h

            # Tendencia de presión
            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=trend_p_now,
                epoch_now=trend_epoch_now,
                p_3h_ago=trend_p_3h,
                epoch_3h_ago=trend_epoch_3h
            )
        else:
            dp3, rate_h, p_label, p_arrow = float("nan"), float("nan"), "—", "•"
        
        # Inicializar variables termodinámicas
        e_sat = float("nan")
        e = float("nan")
        Td_calc = float("nan")
        Tw = float("nan")
        q = float("nan")
        q_gkg = float("nan")
        theta = float("nan")
        Tv = float("nan")
        Te = float("nan")
        rho = float("nan")
        rho_v_gm3 = float("nan")
        lcl = float("nan")
        
        # Termodinámica básica - NO necesita presión (solo T y RH)
        if not is_nan(base.get("Tc")) and not is_nan(base.get("RH")):
            e_sat = e_s(base["Tc"])
            e = vapor_pressure(base["Tc"], base["RH"])
            Td_calc = dewpoint_from_vapor_pressure(e)
            Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)

            # Actualizar base con Td calculado
            base["Td"] = Td_calc
            
            # Termodinámica avanzada - SÍ necesita presión
            if not is_nan(p_abs):
                q = specific_humidity(e, p_abs)
                q_gkg = q * 1000
                theta = potential_temperature(base["Tc"], p_abs)
                Tv = virtual_temperature(base["Tc"], q)
                Te = equivalent_temperature(base["Tc"], q)
                rho = air_density(p_abs, Tv)
                rho_v_gm3 = absolute_humidity(e, base["Tc"])
                lcl = lcl_height(base["Tc"], Td_calc)
        else:
            base["Td"] = float("nan")

        # Sensación térmica y Heat Index (calculados, nunca del API)
        wind_fl = base.get("wind", 0.0)
        if is_nan(wind_fl):
            wind_fl = 0.0
        wind_fl_ms = float(wind_fl) / 3.6
        base["feels_like"] = apparent_temperature(base["Tc"], e, wind_fl_ms)
        base["heat_index"] = heat_index_rothfusz(base["Tc"], base.get("RH", float("nan")))

        # Radiación (no disponible en AEMET)
        solar_rad = float("nan")
        uv = float("nan")
        et0 = float("nan")
        clarity = float("nan")
        balance = float("nan")
        has_radiation = False

    elif provider_id in _standard_provider_runtime_config():
        base, _r = _process_standard_provider_connection(provider_id)
        (z, p_abs, p_msl, p_abs_disp, p_msl_disp,
         dp3, rate_h, p_label, p_arrow,
         inst_mm_h, r1_mm_h, r5_mm_h, inst_label,
         e_sat, e, Td_calc, Tw, q, q_gkg,
         theta, Tv, Te, rho, rho_v_gm3, lcl,
         solar_rad, uv, et0, clarity, balance,
         has_radiation, has_chart_data) = _unpack_processed(_r)

    else:
        # ========== DATOS DE WEATHER UNDERGROUND ==========
        station_id = str(st.session_state.get(ACTIVE_STATION, "")).strip()
        api_key = str(st.session_state.get(ACTIVE_KEY, "")).strip()

        if not station_id:
            station_id = str(st.session_state.get("wu_connected_station", "")).strip()
            if station_id:
                st.session_state[ACTIVE_STATION] = station_id
        if not api_key:
            api_key = str(st.session_state.get("wu_connected_api_key", "")).strip()
            if api_key:
                st.session_state[ACTIVE_KEY] = api_key

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

            if st.session_state.pop("_wu_calibration_changed", False):
                reset_rain_history()
                st.session_state.pop("p_hist", None)

            # Obtener datos de WU (con cache)
            base_raw = fetch_wu_current_session_cached(station_id, api_key, ttl_s=REFRESH_SECONDS)
            base = apply_wu_current_calibration(base_raw, station_calibration)

            # Guardar timestamp de última actualización exitosa
            st.session_state[LAST_UPDATE_TIME] = time.time()

            # Guardar latitud y longitud para cálculos de radiación
            st.session_state[STATION_LAT] = base.get("lat", float("nan"))
            st.session_state[STATION_LON] = base.get("lon", float("nan"))

            # ========== ALTITUD ==========
            # Prioridad: 1) active_z del usuario, 2) elevation de API
            elevation_api = base.get("elevation", float("nan"))

            # Obtener elevation_user manejando string vacío
            active_z_str = str(st.session_state.get(ACTIVE_Z, "0")).strip()
            try:
                elevation_user = float(active_z_str) if active_z_str else 0.0
            except ValueError:
                elevation_user = 0.0

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

            now_ts = time.time()

            # Advertir si los datos son muy antiguos
            data_age_minutes = (now_ts - base["epoch"]) / 60
            if data_age_minutes > MAX_DATA_AGE_MINUTES:
                st.warning(f"⚠️ Datos con {data_age_minutes:.0f} minutos de antigüedad. La estación puede no estar reportando.")
                logger.warning(f"Datos antiguos: {data_age_minutes:.1f} minutos")

            # ========== LLUVIA ==========
            inst_mm_h, r1_mm_h, r5_mm_h = rain_rates_from_total(base["precip_total"], base["epoch"])
            inst_label = rain_intensity_label(inst_mm_h)

            # ========== PRESIÓN ==========
            p_msl = float(base["p_hpa"])
            p_abs = msl_to_absolute(p_msl, z, base["Tc"])
            provider_for_pressure = st.session_state.get(CONNECTION_TYPE, "WU")
            p_abs_disp = _fmt_pressure_for_provider(p_abs, provider_for_pressure)
            p_msl_disp = _fmt_pressure_for_provider(p_msl, provider_for_pressure)

            init_pressure_history()
            push_pressure(p_abs, base["epoch"])

            dp3, rate_h, p_label, p_arrow = pressure_trend_3h(
                p_now=p_msl,
                epoch_now=base["epoch"],
                p_3h_ago=base.get("pressure_3h_ago"),
                epoch_3h_ago=base.get("epoch_3h_ago")
            )

            # ========== TERMODINÁMICA ==========
            # Todas las variables calculadas a partir de T, RH y p_abs
            e_sat = e_s(base["Tc"])  # Presión de saturación
            e = vapor_pressure(base["Tc"], base["RH"])  # Presión de vapor
            Td_calc = dewpoint_from_vapor_pressure(e)  # Td calculado (para LCL)
            q = specific_humidity(e, p_abs)  # Humedad específica
            q_gkg = q * 1000  # g/kg
            theta = potential_temperature(base["Tc"], p_abs)  # Temperatura potencial
            Tv = virtual_temperature(base["Tc"], q)  # Temperatura virtual
            Te = equivalent_temperature(base["Tc"], q)  # Temperatura equivalente
            Tw = wet_bulb_celsius(base["Tc"], base["RH"], p_abs)  # Psicrométrica si hay p_abs
            rho = air_density(p_abs, Tv)  # Densidad del aire
            rho_v_gm3 = absolute_humidity(e, base["Tc"])  # Humedad absoluta
            lcl = lcl_height(base["Tc"], Td_calc)  # Altura LCL

            # Sensación térmica y Heat Index (calculados, nunca del API)
            wind_fl = base.get("wind", 0.0)
            if is_nan(wind_fl):
                wind_fl = 0.0
            wind_fl_ms = float(wind_fl) / 3.6
            base["feels_like"] = apparent_temperature(base["Tc"], e, wind_fl_ms)
            base["heat_index"] = heat_index_rothfusz(base["Tc"], base["RH"])

            # ========== RADIACIÓN ==========
            solar_rad = base.get("solar_radiation", float("nan"))
            uv = base.get("uv", float("nan"))
        
            # MODO DEMO: Reemplazar con valores demo si está activado
            if st.session_state.get("demo_radiation", False):
                demo_solar = st.session_state.get("demo_solar")
                demo_uv = st.session_state.get("demo_uv")
                if demo_solar is not None:
                    solar_rad = float(demo_solar)
                if demo_uv is not None:
                    uv = float(demo_uv)

            # Determinar si la estación tiene sensores de radiación
            has_radiation = not is_nan(solar_rad) or not is_nan(uv)

            if has_radiation:
                # Obtener latitud, elevación y timestamp para FAO-56
                lat = base.get("lat", float("nan"))
                now_ts = time.time()
            
                # ET0 por FAO-56 Penman-Monteith
                wind_speed = base.get("wind", 2.0)  # Velocidad viento (default 2 m/s si no hay)
                if not is_nan(wind_speed) and wind_speed < 0.1:
                    wind_speed = 0.1  # Mínimo para evitar división por cero
            
                from models.radiation import penman_monteith_et0
                et0 = penman_monteith_et0(
                    solar_rad, 
                    base["Tc"], 
                    base["RH"], 
                    wind_speed, 
                    lat, 
                    z,  # elevación
                    now_ts
                )

                # Claridad del cielo con latitud y elevación (FAO-56)
                # Usar epoch del dato (no time.time()) para que la referencia
                # teórica coincida con el momento de la medición.
                from models.radiation import sky_clarity_index
                lon = base.get("lon", float("nan"))
                clarity = sky_clarity_index(solar_rad, lat, z, base["epoch"], lon)

                # ET0 y balance mostrados en UI se recalculan como acumulado "hoy"
                # usando la serie /all/1day tras cargar los puntos temporales.
                et0 = float("nan")
                balance = float("nan")


            # ========== SERIES TEMPORALES PARA GRÁFICOS ==========
            wu_chart_series_is_fresh = _chart_series_fresh_for_station("WU", station_id)
            use_cached_wu_chart_series = wu_chart_series_is_fresh or active_tab != "observation"
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
                    et0 = float(st.session_state.get("chart_et0", et0))
                    balance = float(st.session_state.get("chart_balance", balance))
            else:
                timeseries_raw = fetch_daily_timeseries_session_cached(
                    station_id,
                    api_key,
                    ttl_s=REFRESH_SECONDS,
                )
                timeseries = apply_wu_series_calibration(timeseries_raw, station_calibration)
                chart_epochs = timeseries.get("epochs", [])
                chart_temps = timeseries.get("temps", [])
                chart_humidities = timeseries.get("humidities", [])
                chart_dewpts = timeseries.get("dewpts", [])
                # WU devuelve presiones MSL → convertir a absoluta para coherencia
                _cp_msl = timeseries.get("pressures", [])
                _msl_factor = math.exp(-z / 8000.0)
                chart_pressures = [
                    p * _msl_factor if not is_nan(p) else float("nan")
                    for p in _cp_msl
                ]
                chart_uv_indexes = timeseries.get("uv_indexes", [])
                chart_solar_radiations = timeseries.get("solar_radiations", [])
                chart_winds = timeseries.get("winds", [])
                chart_gusts = timeseries.get("gusts", [])
                chart_wind_dirs = timeseries.get("wind_dirs", [])

                # Fallback de coordenadas desde /all/1day si current no las trajo.
                ts_lat = timeseries.get("lat", float("nan"))
                ts_lon = timeseries.get("lon", float("nan"))
                if is_nan(st.session_state.get(STATION_LAT, float("nan"))) and not is_nan(ts_lat):
                    st.session_state[STATION_LAT] = ts_lat
                    base["lat"] = ts_lat
                if is_nan(st.session_state.get(STATION_LON, float("nan"))) and not is_nan(ts_lon):
                    st.session_state[STATION_LON] = ts_lon
                    base["lon"] = ts_lon

                if is_nan(base.get("lat", float("nan"))) and not is_nan(st.session_state.get(STATION_LAT, float("nan"))):
                    base["lat"] = st.session_state.get(STATION_LAT)
                if is_nan(base.get("lon", float("nan"))) and not is_nan(st.session_state.get(STATION_LON, float("nan"))):
                    base["lon"] = st.session_state.get(STATION_LON)
                has_chart_data = bool(timeseries.get("has_data", False)) or len(chart_epochs) > 0

                wu_sensor_presence = detect_wu_sensor_presence(base_raw, timeseries_raw)
                prev_wu_sensor_presence = st.session_state.get("wu_sensor_presence", {})
                prev_wu_sensor_station = str(st.session_state.get("wu_sensor_presence_station", "")).strip().upper()
                st.session_state["wu_sensor_presence"] = wu_sensor_presence
                st.session_state["wu_sensor_presence_station"] = station_id.upper()
                if (
                    active_tab == "observation"
                    and (prev_wu_sensor_station != station_id.upper() or prev_wu_sensor_presence != wu_sensor_presence)
                ):
                    st.rerun()
            
                # FALLBACK: Si no hay humidities, calcularlas desde T y Td
                # (esto no debería ser necesario normalmente)
                if len(chart_humidities) == 0 or all(is_nan(h) for h in chart_humidities):
                    logger.warning("⚠️  API no devolvió humedad - usando fallback desde T y Td")
                    chart_humidities = _humidity_series_from_temp_and_dewpoint(chart_temps, chart_dewpts)

                # ET0 "hoy" acumulada desde serie diaria (típicamente 5 min con piranómetro).
                if has_radiation:
                    et0, balance = _accumulate_et0_from_series(
                        chart_epochs=chart_epochs,
                        chart_temps=chart_temps,
                        chart_humidities=chart_humidities,
                        chart_solar_radiations=chart_solar_radiations,
                        chart_winds=chart_winds,
                        fallback_wind=base.get("wind", 2.0),
                        lat=base.get("lat", float("nan")),
                        elevation_m=z,
                        precip_total=base["precip_total"],
                    )

                # Guardar en session_state para acceso desde otras tabs
                store_chart_series(
                    st.session_state,
                    {
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
                        "has_data": has_chart_data,
                    }
                )
                _set_chart_series_owner("WU", station_id.upper())
                st.session_state["chart_et0"] = et0
                st.session_state["chart_balance"] = balance

        except WuError as e:
            _cancel_connection_loading()
            if e.kind == "unauthorized":
                st.error("❌ API key inválida o sin permisos.")
            elif e.kind == "notfound":
                st.error("❌ Station ID no encontrado.")
            elif e.kind == "ratelimit":
                st.error("❌ Demasiadas peticiones. Aumenta el refresh.")
            elif e.kind == "timeout":
                st.error("❌ Timeout consultando Weather Underground.")
            elif e.kind == "network":
                st.error("❌ Error de red.")
            else:
                st.error("❌ Error consultando Weather Underground.")
        except Exception as err:
            _cancel_connection_loading()
            # Usar concatenación simple para evitar cualquier problema con format specifiers
            st.error("❌ Error inesperado: " + str(err))
            logger.error(f"Error inesperado: {repr(err)}")

if st.session_state.get(CONNECTION_LOADING) and _has_live_connection_payload(base):
    st.session_state[CONNECTED] = True
    st.session_state.pop(CONNECTION_LOADING, None)
    clear_connection_loading_overlay()

if st.session_state.get("demo_radiation", False):
    demo_solar = _first_valid_float(st.session_state.get("demo_solar"), default=650.0)
    demo_uv = _first_valid_float(st.session_state.get("demo_uv"), default=6.0)
    demo_lat = _first_valid_float(
        base.get("lat"),
        st.session_state.get("provider_station_lat"),
        st.session_state.get("aemet_station_lat"),
        st.session_state.get("station_lat"),
        default=41.3874,
    )
    demo_lon = _first_valid_float(
        base.get("lon"),
        st.session_state.get("provider_station_lon"),
        st.session_state.get("aemet_station_lon"),
        st.session_state.get("station_lon"),
        default=2.1686,
    )
    demo_alt = _first_valid_float(
        z,
        st.session_state.get("provider_station_alt"),
        st.session_state.get("aemet_station_alt"),
        st.session_state.get("station_elevation"),
        default=12.0,
    )

    demo_series = _build_demo_radiation_series(demo_solar, demo_uv)
    demo_epoch = demo_series["epochs"][-1] if demo_series["epochs"] else int(time.time())

    base.update({
        "epoch": int(base.get("epoch") or demo_epoch),
        "solar_radiation": demo_solar,
        "uv": demo_uv,
        "lat": demo_lat,
        "lon": demo_lon,
        "precip_total": _first_valid_float(base.get("precip_total"), default=0.0),
    })
    z = demo_alt
    solar_rad = demo_solar
    uv = demo_uv
    has_radiation = True
    st.session_state["demo_radiation_series"] = demo_series

    from models.radiation import penman_monteith_et0, sky_clarity_index

    clarity = sky_clarity_index(solar_rad, demo_lat, z, demo_epoch, demo_lon)

    demo_epochs = demo_series["epochs"]
    demo_temps = demo_series["temps"]
    demo_humidities = demo_series["humidities"]
    demo_winds = demo_series["winds"]
    demo_solars = demo_series["solar_radiations"]
    et0, balance = _accumulate_et0_from_series(
        chart_epochs=demo_epochs,
        chart_temps=demo_temps,
        chart_humidities=demo_humidities,
        chart_solar_radiations=demo_solars,
        chart_winds=demo_winds,
        fallback_wind=2.0,
        lat=demo_lat,
        elevation_m=z,
        precip_total=base["precip_total"],
    )

    st.session_state[STATION_LAT] = demo_lat
    st.session_state[STATION_LON] = demo_lon
    st.session_state[STATION_ELEVATION] = demo_alt
    if not connected:
        has_chart_data = demo_series.get("has_data", False)
        chart_epochs = demo_series["epochs"]
        chart_temps = demo_series["temps"]
        chart_humidities = demo_series["humidities"]
        chart_pressures = []
        chart_uv_indexes = demo_series["uv_indexes"]
        chart_solar_radiations = demo_series["solar_radiations"]
        chart_winds = demo_series["winds"]
        chart_gusts = demo_series["gusts"]
        chart_wind_dirs = demo_series["wind_dirs"]
        store_chart_series(
            st.session_state,
            {
                "epochs": chart_epochs,
                "temps": chart_temps,
                "humidities": chart_humidities,
                "pressures_abs": chart_pressures,
                "uv_indexes": chart_uv_indexes,
                "solar_radiations": chart_solar_radiations,
                "winds": chart_winds,
                "gusts": chart_gusts,
                "wind_dirs": chart_wind_dirs,
                "has_data": has_chart_data,
            }
        )
else:
    st.session_state.pop("demo_radiation_series", None)

if connected and int(base.get("epoch", 0) or 0) > 0:
    _store_runtime_snapshot(
        base=base,
        z=z,
        inst_mm_h=inst_mm_h,
        r1_mm_h=r1_mm_h,
        r5_mm_h=r5_mm_h,
        inst_label=inst_label,
        p_abs=p_abs,
        p_msl=p_msl,
        p_abs_disp=p_abs_disp,
        p_msl_disp=p_msl_disp,
        dp3=dp3,
        p_label=p_label,
        p_arrow=p_arrow,
        e=e,
        q_gkg=q_gkg,
        theta=theta,
        Tv=Tv,
        Te=Te,
        Tw=Tw,
        lcl=lcl,
        rho=rho,
        rho_v_gm3=rho_v_gm3,
        solar_rad=solar_rad,
        uv=uv,
        et0=et0,
        clarity=clarity,
        balance=balance,
        has_radiation=has_radiation,
        has_chart_data=has_chart_data,
    )

# Mostrar metadata si está conectado (común para AEMET y WU)
if connected:
    browser_tz_name = str(
        st.session_state.get("browser_tz") or st.query_params.get("_tz", "")
    ).strip()
    station_tz_name = str(
        base.get("station_tz", st.session_state.get("provider_station_tz", ""))
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
<style data-theme-hash="{css_hash}">
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

# ============================================================
# CONSTRUCCIÓN DE UI (SIEMPRE SE MUESTRA, CON O SIN DATOS)
# ============================================================

# TAB 1: OBSERVACIÓN
if active_tab == "observation":
    render_observation_tab(_build_observation_tab_context())

# ============================================================
# TAB 2: TENDENCIAS
# ============================================================

elif active_tab == "trends":
    render_trends_tab(_build_trends_tab_context())

# ============================================================
# TAB 3: HISTORICO
# ============================================================

elif active_tab == "historical":
    render_historical_tab(_build_historical_tab_context())

# ============================================================
# TAB 4: MAPA
# ============================================================

elif active_tab == "map":
    render_map_tab(_build_map_tab_context())

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

APP_VERSION = "Release Candidate"

footer_html = """
        <style>
        .mlb-footer{
            margin-top: 1.25rem;
            padding-top: 0.8rem;
            border-top: 1px solid var(--line);
            color: var(--muted);
            font-size: 0.92rem;
        }
        .mlb-footer-top{
            display: flex;
            align-items: center;
            gap: 0.65rem;
            flex-wrap: wrap;
        }
        .mlb-footer-news details{
            display: inline-block;
        }
        .mlb-footer-news summary{
            list-style: none;
            cursor: pointer;
            color: #2f9cff;
            text-decoration: underline;
            text-underline-offset: 2px;
        }
        .mlb-footer-news summary::-webkit-details-marker{
            display: none;
        }
        .mlb-footer-box{
            margin-top: 0.6rem;
            padding: 0.8rem 0.95rem;
            border-radius: 10px;
            border: 1px solid var(--line);
            background: rgba(66, 133, 244, 0.08);
            color: var(--text);
            max-width: 920px;
        }
        .mlb-footer-box h3{
            margin: 0.55rem 0 0.3rem 0;
            font-size: 1.05rem;
        }
        .mlb-footer-box h3:first-child{
            margin-top: 0;
        }
        .mlb-footer-box ul{
            margin: 0.12rem 0 0.35rem 1.1rem;
            padding: 0;
        }
        .mlb-footer-bottom{
            margin-top: 0.52rem;
            font-size: 0.86rem;
            opacity: 0.92;
        }
        </style>
        <div class="mlb-footer">
          <div class="mlb-footer-top">
            <span><b>MeteoLabX · %s</b></span>
            <span class="mlb-footer-news">
              <details>
                <summary>%s</summary>
                <div class="mlb-footer-box">
                  <h2 style="margin:0 0 0.6rem 0;">%s</h2>
                  <h3>%s</h3>
                  <ul>
                    <li>%s</li>
                    <li>%s</li>
                    <li>%s</li>
                  </ul>
                  <h3>%s</h3>
                  <ul>
                    <li>%s</li>
                  </ul>
                </div>
              </details>
            </span>
          </div>
          <div class="mlb-footer-bottom">%s: WU · AEMET · Meteocat · Euskalmet · Frost · Meteo-France · MeteoGalicia · NWS · POEM · %s · © 2026</div>
        </div>
        """
footer_html = footer_html % (
    t("footer.version", version=APP_VERSION),
    t("footer.news"),
    APP_VERSION,
    t("footer.improvements_title"),
    t("footer.improvements.french_language"),
    t("footer.improvements.performance"),
    t("footer.improvements.small_screens"),
    t("footer.fixes_title"),
    t("footer.fixes.autoconnect_startup"),
    t("footer.sources"),
    t("footer.unaffiliated"),
)

st.markdown(html_clean(footer_html), unsafe_allow_html=True)

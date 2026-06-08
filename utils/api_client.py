"""
Cliente HTTP del frontend Streamlit hacia el backend FastAPI.

Modo de uso:
- ``is_backend_enabled()`` → ``True`` si la env var ``METEOLABX_USE_API=1``
  está fijada. Por defecto ``False`` (modo legacy: Streamlit pega
  directamente a Weather Underground).
- ``backend_url()`` → URL base del backend (env ``METEOLABX_API_URL``,
  default ``http://localhost:8000``).
- ``fetch_wu_current_via_api(station_id, api_key)`` → equivalente
  funcional de ``api.weather_underground.fetch_wu_current``: devuelve el
  mismo ``dict`` (con NaN para valores ausentes) y lanza ``WuError`` con
  los mismos ``kind`` y ``status_code`` que la versión legacy. Eso
  garantiza que el resto del código de Streamlit (manejo de errores,
  caché, render) no necesita cambiar.

Diseño:
- Si el backend responde 200 + JSON con ``ok: false`` no es un caso
  esperable (la API responde con HTTP status apropiado y body
  ``ErrorResponse``). Lo tratamos defensivamente como ``WuError("http")``.
- ``requests`` (sync) en vez de ``httpx`` porque Streamlit es síncrono y
  ``requests`` ya está en ``requirements.txt``.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Mapping

import requests

# Importamos WuError del módulo legacy para mantener compatibilidad con
# todo el manejo de errores existente (try/except WuError as e: ...).
from api.weather_underground import WuError

logger = logging.getLogger(__name__)


# =====================================================================
# Configuración por env
# =====================================================================

def is_backend_enabled() -> bool:
    """``True`` si el frontend debe consumir el backend FastAPI."""
    return os.getenv("METEOLABX_USE_API", "").strip().lower() in ("1", "true", "yes", "on")


def is_processed_endpoint_enabled() -> bool:
    """
    ``True`` si el frontend debe usar ``/observations/current/processed``
    en vez de ``/current`` + cálculo local en el pipeline frontend.

    Es un flag separado y subordinado a ``is_backend_enabled``: solo
    aplica cuando el backend ya está activo. Permite probar el camino
    procesado de forma reversible sin tocar el resto del despliegue.
    """
    if not is_backend_enabled():
        return False
    return os.getenv("METEOLABX_USE_PROCESSED_API", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def backend_url() -> str:
    """URL base del backend. Sin trailing slash."""
    return os.getenv("METEOLABX_API_URL", "http://localhost:8000").rstrip("/")


# Tiempo total que esperamos al backend. Más largo que el timeout interno
# del backend hacia WU (15s) para dejar margen al backend a hacer su propio
# timeout y devolvernos un 504 limpio en lugar de cortar nosotros.
_FRONTEND_HTTP_TIMEOUT_S = 20.0


# =====================================================================
# Mapeo error_code (backend) → WuError(kind, status)
# =====================================================================

# Convertir error_code estable del contrato API a los kinds históricos
# de WuError. Esto es la frontera de contrato; si el backend añade
# nuevos error_codes, se mapean aquí.
_ERROR_CODE_TO_WUERROR_KIND: Mapping[str, str] = {
    "provider_unauthorized": "unauthorized",
    "station_not_found": "notfound",
    "provider_ratelimit": "ratelimit",
    "provider_timeout": "timeout",
    "provider_network_error": "network",
    "provider_http_error": "http",
    "provider_bad_response": "badjson",
}


def _raise_wuerror_from_response(response: requests.Response) -> None:
    """
    Traduce una respuesta HTTP de error del backend en ``WuError``.
    Llamar SOLO si ``response.status_code >= 400``.
    """
    error_code = ""
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error_code = str(body.get("error_code") or "").strip()
            detail = str(body.get("detail") or "").strip()
    except (ValueError, KeyError):
        # Backend devolvió algo que no es JSON parseable (proxy roto, 502
        # de plataforma, etc.). No tenemos error_code; usamos http.
        pass

    kind = _ERROR_CODE_TO_WUERROR_KIND.get(error_code, "http")
    if detail:
        logger.info(
            "Backend devolvió %s (%s): %s",
            response.status_code, error_code or "?", detail,
        )
    # WuError("unauthorized", 401) etc. — la firma legacy espera el
    # status code original del proveedor, no el HTTP del backend.
    raise WuError(kind, response.status_code)


# =====================================================================
# Conversión null → NaN (el frontend legacy espera NaN, no None)
# =====================================================================

def _null_to_nan(value: Any) -> float:
    """JSON ``null`` → ``float('nan')``; mantiene floats normales tal cual."""
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


# Campos numéricos del shape de ``CurrentObservation`` que el frontend
# legacy espera como float (NaN cuando ausente). ``epoch`` queda fuera
# porque es int y el frontend lo trata distinto.
_NUMERIC_FIELDS = (
    "Tc", "RH", "p_hpa", "Td", "wind", "gust",
    "feels_like", "heat_index", "wind_chill",
    "precip_rate", "precip_total", "wind_dir_deg",
    "solar_radiation", "uv",
    "lat", "lon", "elevation",
)


def _denormalize_for_legacy(body: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Convierte el JSON del endpoint en el ``dict`` que el resto de
    Streamlit consume (campos NaN, claves idénticas a ``fetch_wu_current``).

    Renombra ``uv`` → ``uv`` (idéntico) y mantiene todo lo demás. Si el
    contrato cambia en el backend, este es el único punto que tendría
    que tocar.
    """
    result: Dict[str, Any] = {key: _null_to_nan(body.get(key)) for key in _NUMERIC_FIELDS}

    # epoch (int) y campos de texto van tal cual con defaults razonables.
    epoch_raw = body.get("epoch", 0) or 0
    try:
        result["epoch"] = int(epoch_raw)
    except (TypeError, ValueError):
        result["epoch"] = 0
    result["time_local"] = str(body.get("time_local", "") or "")
    result["time_utc"] = str(body.get("time_utc", "") or "")
    return result


# =====================================================================
# Fetcher principal
# =====================================================================

def _post_to_backend(
    endpoint: str,
    station_id: str,
    api_key: str,
) -> Dict[str, Any]:
    """
    Hace POST a un endpoint del backend con el shape estándar
    ``{provider, station_id, api_key}`` y devuelve el body JSON parseado.

    Centraliza el manejo de timeout/red/HTTP errors → ``WuError`` que es
    el mismo para todos los endpoints. Cada wrapper público
    (``fetch_wu_current_via_api``, ``fetch_daily_timeseries_via_api``)
    solo se ocupa de denormalizar el body al shape legacy.
    """
    url = f"{backend_url()}{endpoint}"
    payload = {
        "provider": "WU",
        "station_id": station_id,
        "api_key": api_key,
    }

    try:
        response = requests.post(url, json=payload, timeout=_FRONTEND_HTTP_TIMEOUT_S)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend (%s): %s", endpoint, exc)
        raise WuError("timeout")
    except requests.RequestException as exc:
        logger.warning("No se puede contactar al backend en %s: %s", url, exc)
        raise WuError("network")

    if response.status_code >= 400:
        _raise_wuerror_from_response(response)

    try:
        body = response.json()
    except ValueError:
        raise WuError("badjson")
    if not isinstance(body, dict):
        raise WuError("badjson")
    return body


def fetch_wu_current_via_api(station_id: str, api_key: str) -> Dict[str, Any]:
    """
    Equivalente a ``api.weather_underground.fetch_wu_current`` pero
    enrutado a través del backend FastAPI.

    Misma firma, mismo shape de retorno, mismo set de errores
    (``WuError`` con ``kind`` y ``status_code`` históricos).
    """
    body = _post_to_backend("/v1/observations/current", station_id, api_key)
    return _denormalize_for_legacy(body)


# =====================================================================
# Series del día
# =====================================================================

# Campos del response de /series/today que el frontend espera como
# listas (con NaN para huecos, no None).
_SERIES_ARRAY_FIELDS = (
    "temps", "humidities", "dewpts", "pressures",
    "uv_indexes", "solar_radiations", "winds", "gusts", "wind_dirs",
)


def _empty_today_series_legacy() -> Dict[str, Any]:
    """
    Shape vacío que el legacy ``fetch_daily_timeseries`` devuelve ante
    errores. Lo devolvemos en el frontend cuando el backend lanza un
    error para no romper la UI (legacy nunca propagaba excepciones aquí).
    """
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "lat": float("nan"),
        "lon": float("nan"),
        "has_data": False,
    }


def _denormalize_series_for_legacy(body: Mapping[str, Any]) -> Dict[str, Any]:
    """``null`` → ``NaN`` en cada lista; ``lat``/``lon`` ídem; resto tal cual."""
    result: Dict[str, Any] = {}
    epochs_raw = body.get("epochs", []) or []
    result["epochs"] = [int(e) for e in epochs_raw if isinstance(e, (int, float))]

    for field in _SERIES_ARRAY_FIELDS:
        values = body.get(field, []) or []
        if not isinstance(values, list):
            values = []
        result[field] = [_null_to_nan(v) for v in values]

    result["lat"] = _null_to_nan(body.get("lat"))
    result["lon"] = _null_to_nan(body.get("lon"))
    result["has_data"] = bool(body.get("has_data", False))
    return result


def fetch_daily_timeseries_via_api_strict(station_id: str, api_key: str) -> Dict[str, Any]:
    """
    Versión "strict" del fetcher de series del día: si el backend falla,
    **lanza ``WuError``** en vez de devolver dict vacío. Pensada para
    callers que necesitan distinguir "backend caído" de "no hay datos".

    El dispatcher ``_fetch_today_series_via_active_source`` la usa para
    decidir entre seguir con el backend o caer a WU directo.
    """
    body = _post_to_backend("/v1/observations/series/today", station_id, api_key)
    return _denormalize_series_for_legacy(body)


def fetch_daily_timeseries_via_api(station_id: str, api_key: str) -> Dict[str, Any]:
    """
    Envoltorio "safe" sobre ``fetch_daily_timeseries_via_api_strict`` que
    nunca propaga errores: devuelve dict vacío con ``has_data: False`` en
    cualquier fallo del backend o de la red.

    Por qué existe: el legacy ``fetch_daily_timeseries`` tampoco propaga
    errores (la pestaña de gráficos sigue funcionando aunque la serie
    falle), así que mantenemos ese contrato para callers que esperan
    nunca-falla. Internamente delega en la versión strict y atrapa.
    """
    try:
        return fetch_daily_timeseries_via_api_strict(station_id, api_key)
    except WuError as exc:
        logger.info(
            "Backend devolvió error para series/today (%s); usando shape vacío legacy",
            exc.kind,
        )
        return _empty_today_series_legacy()


# =====================================================================
# Observación procesada (current + derivadas) en una sola llamada
# =====================================================================

# Campos del bloque ``derivatives`` que son floats (null → NaN en el
# frontend legacy, que espera floats para todas las magnitudes meteo).
_DERIVATIVES_FLOAT_FIELDS = (
    "z",
    "p_abs", "p_msl",
    "dp3", "rate_h",
    "inst_mm_h", "r5_mm_h", "r10_mm_h",
    "e_sat", "e", "Td_calc", "Tw", "q", "q_gkg",
    "theta", "Tv", "Te", "rho", "rho_v_gm3", "lcl",
    "solar_rad", "uv", "et0", "clarity", "balance",
)


def _denormalize_derivatives_for_legacy(deriv: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Convierte el bloque ``derivatives`` del response a la forma que
    espera ``ProcessedData`` del frontend: floats (con NaN) + strings
    + bools, sin Pydantic.
    """
    result: Dict[str, Any] = {}
    for field in _DERIVATIVES_FLOAT_FIELDS:
        result[field] = _null_to_nan(deriv.get(field))
    # Strings con sus valores por defecto del schema (ya vienen del backend).
    result["p_abs_disp"] = str(deriv.get("p_abs_disp", "—"))
    result["p_msl_disp"] = str(deriv.get("p_msl_disp", "—"))
    result["p_label"] = str(deriv.get("p_label", "—"))
    result["p_arrow"] = str(deriv.get("p_arrow", "•"))
    result["inst_label"] = str(deriv.get("inst_label", "Sin precipitación"))
    # Bools
    result["has_radiation"] = bool(deriv.get("has_radiation", False))
    result["has_chart_data"] = bool(deriv.get("has_chart_data", False))
    return result


# =====================================================================
# AEMET — current observation
# =====================================================================

def fetch_aemet_current_via_api_strict(station_id: str) -> Dict[str, Any]:
    """
    Llama a ``POST /v1/observations/current`` con ``provider="AEMET"``.

    A diferencia de WU, AEMET usa la API key del servidor (no per-user),
    así que NO enviamos credenciales desde el frontend. El backend lee
    su propia ``METEOLABX_AEMET_API_KEY``.

    Devuelve el ``dict`` canónico con shape de ``CurrentObservation``
    (las mismas keys que el servicio ``server.services.aemet``).
    Null → NaN como en el resto de clientes legacy.

    Lanza ``WuError`` en cualquier fallo (variante strict). El wrapper
    en el módulo legacy (``services/aemet.py``) traduce esos errores al
    contrato esperado por ``get_aemet_data``.
    """
    url = f"{backend_url()}/v1/observations/current"
    payload = {
        "provider": "AEMET",
        "station_id": station_id,
        # AEMET ignora api_key del body (usa la del servidor), pero el
        # schema Pydantic acepta string vacío sin problemas.
        "api_key": "",
    }

    try:
        response = requests.post(url, json=payload, timeout=_FRONTEND_HTTP_TIMEOUT_S)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend AEMET: %s", exc)
        raise WuError("timeout")
    except requests.RequestException as exc:
        logger.warning("Backend no alcanzable en %s: %s", url, exc)
        raise WuError("network")

    if response.status_code >= 400:
        _raise_wuerror_from_response(response)

    try:
        body = response.json()
    except ValueError:
        raise WuError("badjson")
    if not isinstance(body, dict):
        raise WuError("badjson")

    # Denormalización AEMET: usamos la base de WU + campos extra que
    # el schema canónico AEMET trae y que el frontend legacy de AEMET
    # necesita (presión absoluta nativa, código IDEMA, nombre de
    # estación).
    result = _denormalize_for_legacy(body)
    result["p_abs_hpa"] = _null_to_nan(body.get("p_abs_hpa"))
    result["idema"] = str(body.get("idema", "") or "")
    result["station_name"] = str(body.get("station_name", "") or "")
    return result


def fetch_aemet_current_processed_via_api(
    station_id: str,
    *,
    sun_tz_name: str = "",
    max_data_age_minutes: float = 60.0,
) -> Dict[str, Any]:
    """
    Llama a ``POST /v1/observations/current/processed`` con
    ``provider="AEMET"``. Como AEMET usa la API key del servidor, el
    body no lleva credenciales del usuario.

    Devuelve un ``dict`` con tres bloques:

        {
            "observation": {...},   # CurrentObservation con NaN
            "derivatives": {...},   # ProcessedData-like con NaN
            "warnings": [...],
        }

    Lanza ``WuError`` en cualquier fallo (cliente strict; el caller
    decide si traducir a fallback legacy o propagar).
    """
    url = f"{backend_url()}/v1/observations/current/processed"
    payload = {
        "provider": "AEMET",
        "station_id": station_id,
        "api_key": "",  # AEMET ignora el body.api_key (usa la del servidor)
        "sun_tz_name": sun_tz_name,
        "max_data_age_minutes": float(max_data_age_minutes),
    }

    try:
        response = requests.post(url, json=payload, timeout=_FRONTEND_HTTP_TIMEOUT_S)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend AEMET /processed: %s", exc)
        raise WuError("timeout")
    except requests.RequestException as exc:
        logger.warning("Backend no alcanzable en %s: %s", url, exc)
        raise WuError("network")

    if response.status_code >= 400:
        _raise_wuerror_from_response(response)

    try:
        body = response.json()
    except ValueError:
        raise WuError("badjson")
    if not isinstance(body, dict):
        raise WuError("badjson")

    observation_block = body.get("observation", {}) or {}
    derivatives_block = body.get("derivatives", {}) or {}
    warnings_block = body.get("warnings", []) or []

    return {
        # Observación canónica + extras AEMET (idema, station_name, p_abs_hpa).
        "observation": {
            **_denormalize_for_legacy(observation_block),
            "p_abs_hpa": _null_to_nan(observation_block.get("p_abs_hpa")),
            "idema": str(observation_block.get("idema", "") or ""),
            "station_name": str(observation_block.get("station_name", "") or ""),
        },
        # Derivadas (32 campos como WU).
        "derivatives": _denormalize_derivatives_for_legacy(derivatives_block),
        # Warnings emitidos por el pipeline del backend.
        "warnings": [str(w) for w in warnings_block if isinstance(w, str)],
    }


def fetch_aemet_today_series_via_api_strict(station_id: str) -> Dict[str, Any]:
    """
    Llama a ``POST /v1/observations/series/today`` con
    ``provider="AEMET"``. Como en ``/current``, AEMET usa la API key
    del servidor (no per-user), así que el body solo lleva station_id.

    Devuelve el dict canónico de series (TodaySeries-shape) con
    ``null`` convertidos a ``NaN`` en arrays paralelas. Lanza
    ``WuError`` en cualquier fallo (variante strict).
    """
    url = f"{backend_url()}/v1/observations/series/today"
    payload = {"provider": "AEMET", "station_id": station_id, "api_key": ""}

    try:
        response = requests.post(url, json=payload, timeout=_FRONTEND_HTTP_TIMEOUT_S)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend AEMET series: %s", exc)
        raise WuError("timeout")
    except requests.RequestException as exc:
        logger.warning("Backend no alcanzable en %s: %s", url, exc)
        raise WuError("network")

    if response.status_code >= 400:
        _raise_wuerror_from_response(response)

    try:
        body = response.json()
    except ValueError:
        raise WuError("badjson")
    if not isinstance(body, dict):
        raise WuError("badjson")

    return _denormalize_series_for_legacy(body)


def fetch_wu_current_processed_via_api(
    station_id: str,
    api_key: str,
    *,
    sun_tz_name: str = "",
    max_data_age_minutes: float = 60.0,
) -> Dict[str, Any]:
    """
    Llama a ``POST /v1/observations/current/processed`` y devuelve un
    dict combinado con tres claves:

        {
            "observation": {...},  # CurrentObservation con NaN
            "derivatives": {...},  # ProcessedData-like con NaN
            "warnings": [...],
        }

    El bloque ``observation`` ya viene mutado con ``Td``, ``feels_like``
    y ``heat_index`` calculados por el pipeline del backend; el bloque
    ``derivatives`` reemplaza al cálculo local del frontend.

    Como el backend hace BOTH current+series+pipeline en una sola
    petición HTTP, esto sustituye 2-3 ida-vueltas del flujo legacy por 1.

    Lanza ``WuError`` con los mismos códigos de los demás clientes para
    mantener uniformidad en el manejo de errores del frontend.
    """
    url = f"{backend_url()}/v1/observations/current/processed"
    payload = {
        "provider": "WU",
        "station_id": station_id,
        "api_key": api_key,
        "sun_tz_name": sun_tz_name,
        "max_data_age_minutes": float(max_data_age_minutes),
    }

    try:
        response = requests.post(url, json=payload, timeout=_FRONTEND_HTTP_TIMEOUT_S)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend /processed: %s", exc)
        raise WuError("timeout")
    except requests.RequestException as exc:
        logger.warning("Backend no alcanzable en %s: %s", url, exc)
        raise WuError("network")

    if response.status_code >= 400:
        _raise_wuerror_from_response(response)

    try:
        body = response.json()
    except ValueError:
        raise WuError("badjson")
    if not isinstance(body, dict):
        raise WuError("badjson")

    observation_block = body.get("observation", {}) or {}
    derivatives_block = body.get("derivatives", {}) or {}
    warnings_block = body.get("warnings", []) or []

    return {
        # Observación cruda como la devuelve ``/current``, ya mutada con
        # Td/feels_like/heat_index calculados.
        "observation": _denormalize_for_legacy(observation_block),
        # Derivadas en shape ProcessedData-like (32 campos).
        "derivatives": _denormalize_derivatives_for_legacy(derivatives_block),
        # Warnings emitidos por el pipeline (p. ej. datos antiguos).
        "warnings": [str(w) for w in warnings_block if isinstance(w, str)],
    }

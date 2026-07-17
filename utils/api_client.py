"""
Cliente HTTP del frontend Streamlit hacia el backend FastAPI.

Modo de uso (backend-first): Streamlit consume siempre FastAPI; no hay
flag para desactivarlo.
- ``backend_url()`` → URL base del backend (env ``METEOLABX_API_URL``,
  default ``http://localhost:8000``).
- Las funciones públicas devuelven el JSON canónico de FastAPI sin cambiar
  ``null``, completar claves ausentes ni aplanar bloques.

Diseño:
- Si el backend responde 200 + JSON con ``ok: false`` no es un caso
  esperable (la API responde con HTTP status apropiado y body
  ``ErrorResponse``). Lo tratamos defensivamente como ``BackendApiError("http")``.
- ``requests`` (sync) en vez de ``httpx`` porque Streamlit es síncrono y
  ``requests`` ya está en ``requirements.txt``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional

import requests

from utils.api_errors import BackendApiError

logger = logging.getLogger(__name__)


# =====================================================================
# Configuración por env
# =====================================================================

def backend_url() -> str:
    """URL base del backend. Sin trailing slash."""
    return os.getenv("METEOLABX_API_URL", "http://localhost:8000").rstrip("/")


def fetch_geocode_via_api(query: str, *, accept_language: str = "es,en") -> Dict[str, Any]:
    """Resolve a textual location through FastAPI's Nominatim endpoint."""
    return _request_json(
        "GET",
        "/v1/stations/geocode",
        params={"q": str(query or "").strip(), "lang": str(accept_language or "es,en")},
    )


def fetch_stations_near_via_api(
    lat: float,
    lon: float,
    *,
    max_results: int = 200,
    provider_ids: Optional[list[str]] = None,
    countries: Optional[list[str]] = None,
    radius_km: float = 2000.0,
    has_historical: bool = False,
    hide_historical_only: bool = False,
) -> Dict[str, Any]:
    """Search the canonical FastAPI station catalog near a point."""
    providers = ",".join(
        str(provider).strip().upper()
        for provider in (provider_ids or [])
        if str(provider).strip()
    )
    params: Dict[str, Any] = {
        "lat": float(lat),
        "lon": float(lon),
        "radius_km": float(radius_km),
        "limit": int(max_results),
    }
    if providers:
        params["providers"] = providers
    country_filter = ",".join(
        str(country).strip().upper()
        for country in (countries or [])
        if str(country).strip()
    )
    if country_filter:
        params["countries"] = country_filter
    if has_historical:
        params["has_historical"] = "true"
    if hide_historical_only:
        params["hide_historical_only"] = "true"
    return _request_json("GET", "/v1/stations/near", params=params)


def fetch_station_catalog_via_api(
    *,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    max_results: int = 50000,
    provider_ids: Optional[list[str]] = None,
    countries: Optional[list[str]] = None,
    has_historical: bool = False,
    hide_historical_only: bool = False,
) -> Dict[str, Any]:
    """Fetch visible catalog stations by metadata, without spatial clipping."""
    providers = ",".join(
        str(provider).strip().upper()
        for provider in (provider_ids or [])
        if str(provider).strip()
    )
    country_filter = ",".join(
        str(country).strip().upper()
        for country in (countries or [])
        if str(country).strip()
    )
    params: Dict[str, Any] = {"limit": int(max_results)}
    if lat is not None:
        params["lat"] = float(lat)
    if lon is not None:
        params["lon"] = float(lon)
    if providers:
        params["providers"] = providers
    if country_filter:
        params["countries"] = country_filter
    if has_historical:
        params["has_historical"] = "true"
    if hide_historical_only:
        params["hide_historical_only"] = "true"
    return _request_json("GET", "/v1/stations/catalog", params=params)


def fetch_station_by_slug_via_api(provider: str, slug: str) -> Dict[str, Any]:
    """Resolve a catalog station from ``provider`` + name slug (deep links).

    Devuelve la ficha canónica (``provider``/``station_id``/``name``/``lat``…)
    o lanza ``BackendApiError`` (``notfound`` si el slug no existe).
    """
    import urllib.parse as _urllib

    provider_token = _urllib.quote(str(provider or "").strip().upper(), safe="")
    slug_token = _urllib.quote(str(slug or "").strip().lower(), safe="")
    return _request_json(
        "GET",
        f"/v1/stations/{provider_token}/by-slug/{slug_token}",
    )


def fetch_station_countries_via_api(provider_ids: Optional[list[str]] = None) -> Dict[str, int]:
    """Return station country counts from the canonical FastAPI catalog."""
    providers = ",".join(
        str(provider).strip().upper()
        for provider in (provider_ids or [])
        if str(provider).strip()
    )
    params: Dict[str, Any] = {}
    if providers:
        params["providers"] = providers
    payload = _request_json("GET", "/v1/stations/countries", params=params)
    return {str(key): int(value) for key, value in payload.items()}


def fetch_ranking_via_api(
    *,
    providers: Optional[str] = None,
    country: Optional[str] = None,
    day: Optional[str] = None,
    exclude: Optional[str] = None,
    order: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Ranking diario top-N. ``providers`` = lista CSV para filtrar por país
    (sin valor → global); ``country`` = ISO2 para rankear ese país (incluye
    IEM multi-país); ``day`` = fecha local ISO a mostrar (sin valor → la
    principal); ``exclude`` = ISO2 CSV a quitar (p.ej. ``AQ`` Antártida);
    ``order`` = pares ``metrica:asc|desc`` CSV para forzar el sentido del orden
    (p.ej. ``tmax:asc`` da las máximas más bajas). Lanza ``BackendApiError`` si
    el backend falla."""
    params: Dict[str, Any] = {"limit": int(limit)}
    if providers:
        params["providers"] = str(providers)
    if country:
        params["country"] = str(country).strip().upper()
    if day:
        params["day"] = str(day).strip()
    if exclude:
        params["exclude"] = str(exclude).strip().upper()
    if order:
        params["order"] = str(order).strip()
    return _request_json("GET", "/v1/ranking", params=params)


def fetch_ranking_countries_via_api() -> list[str]:
    """ISO2 de los países que tienen datos de ranking hoy (para el selector)."""
    try:
        payload = _request_json("GET", "/v1/ranking/countries")
    except BackendApiError:
        return []
    raw = payload.get("countries") if isinstance(payload, dict) else None
    return [str(c).strip().upper() for c in raw if str(c).strip()] if isinstance(raw, list) else []


def fetch_country_by_tz_via_api(timezone: str) -> Optional[str]:
    """ISO2 aproximado del país a partir de una zona horaria IANA del
    navegador (``Europe/Berlin`` → ``DE``). ``None`` si no se conoce o falla."""
    tz = str(timezone or "").strip()
    if not tz:
        return None
    try:
        payload = _request_json("GET", "/v1/stations/country-by-tz", params={"tz": tz})
    except BackendApiError:
        return None
    value = str(payload.get("country") or "").strip().upper()
    return value or None


# Tiempo total que esperamos al backend. Más largo que el timeout interno
# del backend hacia WU (15s) para dejar margen al backend a hacer su propio
# timeout y devolvernos un 504 limpio en lugar de cortar nosotros.
_FRONTEND_HTTP_TIMEOUT_S = 20.0
# AEMET entrega primero una URL temporal y permite hasta 60 s para
# descargarla en el backend. El cliente debe esperar algo más que ese
# límite; de lo contrario corta una petición que FastAPI aún está
# resolviendo y muestra un timeout falso.
_AEMET_FRONTEND_HTTP_TIMEOUT_S = 75.0
# El histórico (sobre todo el ANUAL) agrega muchas llamadas al proveedor y es
# lento en TODOS los proveedores, no solo IEM/WeatherLink. El backend cachea el
# dataset (``/v1/climo/dataset`` con get_or_fetch), pero si el frontend corta
# antes (default 20s) el usuario ve un timeout FALSO en el primer intento y el
# dato "aparece" solo al cambiar de pestaña y volver (caché ya caliente). Un
# único timeout generoso para ese endpoint cubre a todos los proveedores.
_CLIMO_FRONTEND_HTTP_TIMEOUT_S = 180.0


# =====================================================================
# Mapeo error_code (backend) → BackendApiError(kind, status)
# =====================================================================

# Convertir el ``error_code`` estable del contrato API a categorías de UI.
# Si el backend añade
# nuevos error_codes, se mapean aquí.
_ERROR_CODE_TO_ERROR_KIND: Mapping[str, str] = {
    "provider_unauthorized": "unauthorized",
    "station_not_found": "notfound",
    "provider_ratelimit": "ratelimit",
    "provider_timeout": "timeout",
    "provider_network_error": "network",
    "provider_http_error": "http",
    "provider_bad_response": "badjson",
    "provider_no_current_data": "nodata",
}


def _raise_api_error_from_response(response: requests.Response) -> None:
    """
    Traduce una respuesta HTTP de error del backend en ``BackendApiError``.
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

    kind = _ERROR_CODE_TO_ERROR_KIND.get(error_code, "http")
    if detail:
        # ``warning`` (no ``info``) para que el detalle del backend —en
        # especial el cuerpo de validación 422 de FastAPI con el campo que
        # falla— sea visible en la terminal sin reconfigurar logging.
        logger.warning(
            "Backend devolvió %s (%s): %s",
            response.status_code, error_code or "?", detail,
        )
    # Conservamos el status HTTP para que la UI pueda mostrar contexto.
    raise BackendApiError(kind, response.status_code, detail=detail)


def _request_json(
    method: str,
    endpoint: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    params: Optional[Mapping[str, Any]] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute one backend request and enforce the canonical JSON-object response."""
    url = f"{backend_url()}{endpoint}"
    timeout = float(timeout_s or _FRONTEND_HTTP_TIMEOUT_S)
    try:
        if method.upper() == "GET":
            response = requests.get(url, params=dict(params or {}), timeout=timeout)
        else:
            response = requests.post(url, json=dict(payload or {}), timeout=timeout)
    except requests.Timeout as exc:
        logger.warning("Timeout contactando backend (%s): %s", endpoint, exc)
        raise BackendApiError("timeout") from exc
    except requests.RequestException as exc:
        logger.warning("Backend no alcanzable en %s: %s", url, exc)
        raise BackendApiError("network") from exc
    if response.status_code >= 400:
        _raise_api_error_from_response(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise BackendApiError("badjson") from exc
    if not isinstance(body, dict):
        raise BackendApiError("badjson")
    return body


# =====================================================================
# Estadísticas internas de uso
# =====================================================================

def track_station_visit_via_api(provider: str, station_id: str, name: str = "") -> None:
    """Registra una conexión a estación. Fire-and-forget: cualquier fallo se
    traga en silencio — las estadísticas nunca deben romper una conexión."""
    try:
        requests.post(
            f"{backend_url()}/v1/stats/visit",
            json={
                "provider": str(provider or "").strip().upper(),
                "station_id": str(station_id or "").strip(),
                "name": str(name or "").strip(),
            },
            timeout=2.0,
        )
    except Exception:
        pass


def track_station_error_via_api(
    provider: str,
    station_id: str,
    name: str = "",
    *,
    error_kind: str,
    status_code: Optional[int] = None,
) -> None:
    """Registra un error de conexión a estación. Fire-and-forget, igual que
    ``track_station_visit_via_api``: nunca debe romper el flujo de conexión."""
    try:
        requests.post(
            f"{backend_url()}/v1/stats/error",
            json={
                "provider": str(provider or "").strip().upper(),
                "station_id": str(station_id or "").strip(),
                "name": str(name or "").strip(),
                "error_kind": str(error_kind or "").strip().lower(),
                "status_code": status_code,
            },
            timeout=2.0,
        )
    except Exception:
        pass


def fetch_usage_stats_via_api(password: str) -> Dict[str, Any]:
    """Visitas agregadas por estación (panel interno). Lanza BackendApiError
    ('unauthorized' si la contraseña no es correcta)."""
    url = f"{backend_url()}/v1/stats/stations"
    try:
        response = requests.get(
            url, headers={"X-Stats-Password": str(password or "")}, timeout=10.0,
        )
    except requests.Timeout as exc:
        raise BackendApiError("timeout") from exc
    except requests.RequestException as exc:
        raise BackendApiError("network") from exc
    if response.status_code == 401:
        raise BackendApiError("unauthorized")
    if response.status_code >= 400:
        _raise_api_error_from_response(response)
    body = response.json()
    if not isinstance(body, dict):
        raise BackendApiError("badjson")
    return body


# =====================================================================
# Helper genérico de POST (proveedores con key de servidor)
# =====================================================================

def _post_observation_request(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST a un endpoint de observaciones con manejo estándar de errores
    (timeout/red/HTTP → ``BackendApiError``; body no-JSON → ``badjson``).

    El caller construye el payload canónico completo.
    """
    provider = payload.get("provider", "?")
    provider_norm = str(provider).strip().upper()
    if endpoint == "/v1/climo/dataset":
        # Histórico: lento en todos los proveedores (el anual agrega mucho). El
        # backend cachea, así que este timeout generoso solo se "paga" una vez.
        timeout_s = _CLIMO_FRONTEND_HTTP_TIMEOUT_S
    elif provider_norm == "AEMET":
        # Observación actual de AEMET: 2-step (URL temporal, hasta ~60s backend).
        timeout_s = _AEMET_FRONTEND_HTTP_TIMEOUT_S
    else:
        timeout_s = _FRONTEND_HTTP_TIMEOUT_S

    return _request_json("POST", endpoint, payload=payload, timeout_s=timeout_s)


# =====================================================================
# Cliente genérico por proveedor (current / series del día / reciente)
# =====================================================================
#
# Para los proveedores cuyo hook se construye entero desde el backend
# (NWS, FROST, POEM, METEOHUB_IT, IPMA, GEOSPHERE, SMHI, WEATHERLINK) no
# hace falta un trío de
# funciones por proveedor: estas reciben el provider y, si
# aplica, las credenciales per-user (WeatherLink).

def fetch_provider_current_via_api_strict(
    provider: str,
    station_id: str,
    *,
    api_key: str = "",
    api_secret: str = "",
) -> Dict[str, Any]:
    """``POST /v1/observations/current`` genérico. Lanza ``BackendApiError``."""
    payload = {"provider": provider, "station_id": station_id, "api_key": api_key}
    if api_secret:
        payload["api_secret"] = api_secret
    body = _post_observation_request("/v1/observations/current", payload)
    return body


def fetch_provider_today_series_via_api_strict(
    provider: str,
    station_id: str,
    *,
    api_key: str = "",
    api_secret: str = "",
    station_elevation: float | None = None,
    lookback_hours: int = 0,
) -> Dict[str, Any]:
    """``POST /v1/observations/series/today`` genérico. Lanza ``BackendApiError``."""
    payload = {"provider": provider, "station_id": station_id, "api_key": api_key}
    if api_secret:
        payload["api_secret"] = api_secret
    if station_elevation is not None and float(station_elevation) > 0:
        payload["station_elevation"] = float(station_elevation)
    if int(lookback_hours or 0) > 0:
        payload["lookback_hours"] = int(lookback_hours)
    body = _post_observation_request("/v1/observations/series/today", payload)
    return body


def fetch_provider_recent_series_via_api_strict(
    provider: str,
    station_id: str,
    *,
    api_key: str = "",
    api_secret: str = "",
    days_back: int = 7,
    station_elevation: float | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    ``POST /v1/observations/series/recent`` genérico: serie sinóptica
    (T/HR/presión MSL) de los últimos días para la pestaña Tendencias.
    ``calibration`` (offsets WU) se aplica en el backend antes de derivar,
    igual que en ``/current/processed``. Lanza ``BackendApiError`` en fallo.
    """
    payload = {
        "provider": provider,
        "station_id": station_id,
        "api_key": api_key,
        "api_secret": api_secret,
        "days_back": int(days_back),
    }
    if isinstance(calibration, Mapping) and calibration:
        payload["calibration"] = dict(calibration)
    if station_elevation is not None and float(station_elevation) > 0:
        payload["station_elevation"] = float(station_elevation)
    body = _post_observation_request(
        "/v1/observations/series/recent",
        payload,
    )
    return body


# =====================================================================
# CLIMO — datasets históricos / climogramas
# =====================================================================

def fetch_climo_dataset_via_api_strict(
    provider: str,
    station_id: str,
    *,
    api_key: str = "",
    api_secret: str = "",
    summary_mode: str = "monthly",
    periods=None,
    selected_years=None,
    selected_months=None,
    frost_period: str = "",
    frost_periods=None,
):
    """
    ``POST /v1/climo/dataset``: dataset histórico en el esquema común,
    deserializado de vuelta a ``pandas.DataFrame``.

    ``periods`` acepta los ``ClimogramPeriod`` del frontend (objetos con
    ``label``/``start``/``end``). Devuelve ``(df | None, extremes | None)``.
    Lanza ``BackendApiError`` en cualquier fallo del backend.
    """
    payload = {
        "provider": provider,
        "station_id": station_id,
        "api_key": api_key or "",
        "api_secret": api_secret or "",
        "summary_mode": summary_mode,
        "periods": [
            {
                "label": str(getattr(p, "label", "") or ""),
                "start": getattr(p, "start").isoformat(),
                "end": getattr(p, "end").isoformat(),
            }
            for p in (periods or [])
        ],
        "selected_years": [int(y) for y in (selected_years or [])],
        "selected_months": [int(m) for m in (selected_months or [])],
        "frost_period": frost_period or "",
        "frost_periods": [str(p) for p in (frost_periods or [])],
    }
    body = _post_observation_request("/v1/climo/dataset", payload)

    dataset_json = body.get("dataset")
    df = None
    if dataset_json:
        import io

        import pandas as pd

        try:
            df = pd.read_json(io.StringIO(dataset_json), orient="table")
        except ValueError:
            raise BackendApiError("badjson")
    extremes = body.get("extremes") if isinstance(body.get("extremes"), dict) else None
    return df, extremes


def fetch_provider_current_processed_via_api(
    provider: str,
    station_id: str,
    *,
    api_key: str = "",
    api_secret: str = "",
    sun_tz_name: str = "",
    max_data_age_minutes: float = 60.0,
    station_elevation: float | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    ``POST /v1/observations/current/processed`` genérico (cualquier
    proveedor). Devuelve los bloques canónicos sin modificarlos:

        {"observation": {...}, "derivatives": {...}, "warnings": [...],
         "daily_extremes": {...}|None, "station": {...}|None}

    Lanza ``BackendApiError`` en cualquier fallo. El frontend no recalcula estas
    derivadas localmente: el error se propaga al flujo de conexión.
    """
    payload = {
        "provider": provider,
        "station_id": station_id,
        "api_key": api_key,
        "api_secret": api_secret,
        "sun_tz_name": sun_tz_name,
        "max_data_age_minutes": float(max_data_age_minutes),
    }
    if station_elevation is not None and float(station_elevation) > 0:
        payload["station_elevation"] = float(station_elevation)
    if isinstance(calibration, Mapping) and calibration:
        payload["calibration"] = dict(calibration)
    body = _post_observation_request(
        "/v1/observations/current/processed", payload,
    )

    return body


def fetch_frost_period_options_via_api(station_id: str) -> Dict[str, Any]:
    """
    Periodos de normales climáticas disponibles de una estación Frost,
    para poblar el selector de climogramas: ``{"monthly": [...],
    "annual": [...]}``.

    No-fatal: ante cualquier error del backend devuelve listas vacías
    porque el selector tolera fallos del endpoint de disponibilidad.
    """
    try:
        body = _post_observation_request(
            "/v1/climo/frost/period-options",
            {"station_id": str(station_id or "").strip()},
        )
    except BackendApiError as exc:
        logger.info("Backend frost/period-options falló (%s); selector vacío", exc.kind)
        return {"monthly": [], "annual": []}

    def _str_list(value: Any) -> list:
        return [str(v) for v in value] if isinstance(value, list) else []

    return {
        "monthly": _str_list(body.get("monthly")),
        "annual": _str_list(body.get("annual")),
    }


def fetch_weatherlink_stations_via_api(api_key: str, api_secret: str) -> Dict[str, Any]:
    """Lista estaciones WeatherLink mediante FastAPI, nunca desde Streamlit."""
    body = _post_observation_request(
        "/v1/stations/weatherlink",
        {"api_key": str(api_key or ""), "api_secret": str(api_secret or "")},
    )
    stations = body.get("stations", [])
    return {
        "ok": True,
        "stations": stations if isinstance(stations, list) else [],
    }

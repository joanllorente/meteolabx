"""
Router de observaciones meteorológicas.

Endpoints:
- ``POST /v1/observations/current`` — observación actual de una estación

Es **POST** porque la petición lleva ``api_key`` y la API key no debe
viajar en URL (logs de proxy, referer headers, historial del navegador).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Request

from domain import observation_warnings
from domain.observation_pipeline import (
    ProcessingContext,
    process_observation,
)
from domain.trend_series import derive_trend_series
from models.thermodynamics import msl_to_absolute
from server.config import Settings, get_settings
from server.dependencies.http import (
    get_current_cache,
    get_http_client,
    get_series_cache,
)
from server.schemas.errors import ErrorResponse, ProviderError
from server.schemas.observation import (
    CurrentObservation,
    CurrentObservationRequest,
    DailyExtremes,
    ObservationDerivatives,
    ProcessedCurrentObservationRequest,
    ProcessedCurrentObservationResponse,
    RecentSeries,
    RecentSeriesRequest,
    StationInfo,
    TodaySeries,
    TodaySeriesRequest,
    _ProviderStationRequest,
)
from server.services import (
    aemet,
    euskalmet,
    frost,
    iem,
    meteocat,
    meteofrance,
    meteogalicia,
    meteohub,
    metoffice,
    nws,
    poem,
    stations,
    weatherlink,
    wu,
)
from server.services import ranking as ranking_svc
from server.services.cache import AsyncTTLCache, make_cache_key

logger = logging.getLogger(__name__)


_LOOKBACK_SERIES_FIELDS = (
    "temps",
    "humidities",
    "dewpts",
    "pressures",
    "pressures_abs",
    "uv_indexes",
    "solar_radiations",
    "precips",
    "winds",
    "gusts",
    "wind_dirs",
)


def _is_nan_value(value) -> bool:
    """
    ``True`` si ``value`` es "ausente": None o NaN. Para non-float que no
    sean None (str, int…) devuelve False — solo distinguimos "no llegó
    dato real" vs "llegó dato".
    """
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _float_or_nan(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _safe_epochs(data: dict) -> list[int]:
    epochs: list[int] = []
    for raw in data.get("epochs") or []:
        try:
            epoch = int(raw)
        except (TypeError, ValueError):
            continue
        if epoch > 0:
            epochs.append(epoch)
    return epochs


def _values_for_indices(data: dict, field: str, indices: list[int]) -> list:
    values = data.get(field)
    if not isinstance(values, (list, tuple)):
        return [None for _ in indices]
    return [values[index] if index < len(values) else None for index in indices]


def _prepend_lookback_points(today: dict, recent: dict, lookback_hours: int) -> dict:
    """
    Anteponer puntos previos a medianoche para que las derivadas 3h de
    ``/series/today`` puedan arrancar al inicio del día sin ensuciar la
    petición normal de Observación.
    """
    today_epochs = _safe_epochs(today)
    recent_epochs_raw = list(recent.get("epochs") or [])
    if not today_epochs or not recent_epochs_raw or lookback_hours <= 0:
        return dict(today)

    day_start_epoch = min(today_epochs)
    cutoff_epoch = day_start_epoch - int(lookback_hours) * 3600
    today_epoch_set = set(today_epochs)
    selected_indices: list[int] = []
    for index, raw_epoch in enumerate(recent_epochs_raw):
        try:
            epoch = int(raw_epoch)
        except (TypeError, ValueError):
            continue
        if cutoff_epoch <= epoch < day_start_epoch and epoch not in today_epoch_set:
            selected_indices.append(index)
    selected_indices.sort(key=lambda idx: int(recent_epochs_raw[idx]))
    if not selected_indices:
        return dict(today)

    result = dict(today)
    result["epochs"] = _values_for_indices(recent, "epochs", selected_indices) + list(today.get("epochs") or [])
    for field in _LOOKBACK_SERIES_FIELDS:
        result[field] = _values_for_indices(recent, field, selected_indices) + list(today.get(field) or [])
    for scalar in ("lat", "lon"):
        if _is_nan_value(result.get(scalar)) and not _is_nan_value(recent.get(scalar)):
            result[scalar] = recent.get(scalar)
    result["has_data"] = bool(result.get("epochs"))
    return result


def _series_station_elevation(body: _ProviderStationRequest, station: dict) -> float:
    user_elevation = getattr(body, "station_elevation", None)
    try:
        if user_elevation is not None and math.isfinite(float(user_elevation)):
            return float(user_elevation)
    except (TypeError, ValueError):
        pass
    return _float_or_nan(station.get("elevation"))


def _daily_extremes_from_ranking_store(
    store: ranking_svc.RankingStore | None,
    provider: str,
    station_id: str,
) -> dict:
    if store is None:
        return {}
    record = store.station_daily(provider, station_id)
    if record is None:
        return {}
    out = {
        "temp_max": record.tmax,
        "temp_min": record.tmin,
        "gust_max": record.gust,
        "precip_total": record.rain,
    }
    return {
        key: float(value)
        for key, value in out.items()
        if isinstance(value, (int, float)) and not _is_nan_value(float(value))
    }


def _overlay_daily_extremes(current: dict, daily_extremes: dict) -> dict:
    if not daily_extremes:
        return current
    out = dict(current)
    merged = dict(out.get("daily_extremes") or {})
    for source, target in (
        ("temp_max", "temp_max"),
        ("temp_min", "temp_min"),
        ("gust_max", "gust_max"),
    ):
        if source in daily_extremes:
            merged[target] = daily_extremes[source]
    out["daily_extremes"] = merged
    if "precip_total" in daily_extremes:
        out["precip_total"] = daily_extremes["precip_total"]
    return out


def _series_value_at(series: dict, key: str, index: int) -> float:
    values = series.get(key, [])
    if not isinstance(values, list) or index < 0 or index >= len(values):
        return float("nan")
    return _float_or_nan(values[index])


def _overlay_aemet_current_from_newer_series(current: dict, series: dict) -> dict:
    """
    AEMET tiene dos fuentes útiles:

    - ``/current``: buena para cards rápidas, pero a veces va rezagada.
    - ``/series/today``: diezminutal, necesaria para gráficos.

    Cuando ya tenemos ambas (endpoint ``/processed``), usamos el último
    punto de la serie si es más reciente que ``current``. No hace llamadas
    extra y mantiene el flujo del frontend Streamlit puro.
    """
    if not isinstance(current, dict) or not isinstance(series, dict):
        return current
    epochs = series.get("epochs", [])
    if not isinstance(epochs, list) or not epochs:
        return current

    latest_index = len(epochs) - 1
    try:
        latest_epoch = int(epochs[latest_index])
    except (TypeError, ValueError):
        return current

    current_epoch = _float_or_nan(current.get("epoch"))
    if not (_is_nan_value(current_epoch) or current_epoch <= 0 or latest_epoch > current_epoch):
        return current

    merged = dict(current)
    merged["epoch"] = latest_epoch

    for series_key, current_key in (
        ("temps", "Tc"),
        ("humidities", "RH"),
        ("winds", "wind"),
        ("gusts", "gust"),
        ("wind_dirs", "wind_dir_deg"),
    ):
        value = _series_value_at(series, series_key, latest_index)
        if not _is_nan_value(value):
            merged[current_key] = value

    # En el shape canónico del backend AEMET, ``pressures`` es presión
    # reducida al nivel del mar. Al actualizarla desde la serie, anulamos
    # la absoluta para que el glue posterior la recalcule con altitud+Tc.
    pressure_msl = _series_value_at(series, "pressures", latest_index)
    if not _is_nan_value(pressure_msl):
        merged["p_hpa"] = pressure_msl
        merged["p_abs_hpa"] = float("nan")

    return merged


def _aemet_current_from_series(station_id: str, series: dict) -> dict:
    """Construye una observación mínima con el último dato diezminutal."""
    epochs = series.get("epochs", []) if isinstance(series, dict) else []
    if not isinstance(epochs, list) or not epochs:
        return {}

    try:
        latest_epoch = int(epochs[-1])
    except (TypeError, ValueError):
        return {}

    def _latest(key: str) -> float:
        values = series.get(key, [])
        if not isinstance(values, list):
            return float("nan")
        for value in reversed(values):
            number = _float_or_nan(value)
            if not _is_nan_value(number):
                return number
        return float("nan")

    record = stations.get_station("AEMET", station_id) or {}
    dt_utc = datetime.fromtimestamp(latest_epoch, tz=timezone.utc)
    return {
        "Tc": _latest("temps"),
        "RH": _latest("humidities"),
        "p_hpa": _latest("pressures"),
        "p_abs_hpa": float("nan"),
        "Td": _latest("dewpts"),
        "wind": _latest("winds"),
        "gust": _latest("gusts"),
        "wind_dir_deg": _latest("wind_dirs"),
        "precip_rate": float("nan"),
        "precip_total": float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": latest_epoch,
        "time_utc": dt_utc.isoformat(),
        "time_local": dt_utc.isoformat(),
        "lat": record.get("lat"),
        "lon": record.get("lon"),
        "elevation": record.get("elevation"),
        "station_name": str(record.get("name") or station_id),
    }


router = APIRouter(prefix="/observations", tags=["observations"])


def _resolve_provider_fetchers(
    body: _ProviderStationRequest,
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    include_provider_daily_extremes: bool = True,
):
    """
    Dispatch único por proveedor para los tres endpoints de observación.

    Devuelve ``(cache_secret, current_fetcher, series_fetcher)``:

    - ``cache_secret``: componente secreto de la cache key. Para
      proveedores per-user (WU) es la api_key del usuario (aísla cachés
      entre usuarios); para proveedores con key de servidor (AEMET,
      Meteocat) es la key del backend (cache compartido entre usuarios).
    - ``current_fetcher`` / ``series_fetcher``: callables sin argumentos
      que hacen el fetch real contra el proveedor.

    Valida credenciales según el modelo de auth de cada proveedor y
    lanza ``ProviderError`` si faltan o el proveedor no está soportado.
    """
    if body.provider == "WU":
        if not body.api_key:
            raise ProviderError(
                "missing_api_key",
                provider="WU",
                detail="WU requires per-user api_key in request body",
                status_code=400,
            )
        return (
            body.api_key,
            lambda: wu.fetch_current(body.station_id, body.api_key, client=http),
            lambda: wu.fetch_today_series(body.station_id, body.api_key, client=http),
        )

    if body.provider == "AEMET":
        # AEMET usa key del servidor; ignoramos body.api_key.
        aemet_key = settings.aemet_api_key
        # /processed obtiene current y serie en paralelo. Si el endpoint
        # current se atasca, la serie puede construir las cards; no tiene
        # sentido bloquear todo el dashboard durante 60 s antes del fallback.
        current_timeouts = (
            {"step1_timeout_s": 10.0, "step2_timeout_s": 20.0}
            if isinstance(body, ProcessedCurrentObservationRequest)
            else {}
        )
        return (
            aemet_key,
            lambda: aemet.fetch_current(
                body.station_id,
                aemet_key,
                client=http,
                include_daily_extremes=include_provider_daily_extremes,
                **current_timeouts,
            ),
            lambda: aemet.fetch_today_series(body.station_id, aemet_key, client=http),
        )

    if body.provider == "METEOCAT":
        # Meteocat también usa key del servidor.
        meteocat_key = settings.meteocat_api_key
        return (
            meteocat_key,
            lambda: meteocat.fetch_current(body.station_id, meteocat_key, client=http),
            lambda: meteocat.fetch_today_series(body.station_id, meteocat_key, client=http),
        )

    if body.provider == "EUSKALMET":
        # Credenciales del servidor: JWT (manual o autogenerado de PEM)
        # + api key opcional. El JWT rota por hora; el cache_secret usa
        # la api key + iss para no invalidar el caché en cada rotación.
        jwt = euskalmet.resolve_jwt(
            settings.euskalmet_jwt,
            settings.euskalmet_private_key_path,
            settings.euskalmet_jwt_iss,
            settings.euskalmet_jwt_email,
        )
        euskalmet_secret = settings.euskalmet_api_key or settings.euskalmet_jwt_iss
        return (
            euskalmet_secret,
            lambda: euskalmet.fetch_current(
                body.station_id, jwt, settings.euskalmet_api_key, client=http,
            ),
            lambda: euskalmet.fetch_today_series(
                body.station_id, jwt, settings.euskalmet_api_key, client=http,
            ),
        )

    if body.provider == "METEOGALICIA":
        # API pública sin credenciales; el cache_secret es constante.
        return (
            "public",
            lambda: meteogalicia.fetch_current(
                body.station_id,
                client=http,
                include_daily_extremes=include_provider_daily_extremes,
            ),
            lambda: meteogalicia.fetch_today_series(body.station_id, client=http),
        )

    if body.provider == "NWS":
        # API pública (solo User-Agent identificativo).
        return (
            "public",
            lambda: nws.fetch_current(body.station_id, client=http),
            lambda: nws.fetch_today_series(body.station_id, client=http),
        )

    if body.provider == "METEOFRANCE":
        # Key del servidor (header apikey).
        mf_key = settings.meteofrance_api_key
        return (
            mf_key,
            lambda: meteofrance.fetch_current(body.station_id, mf_key, client=http),
            lambda: meteofrance.fetch_today_series(body.station_id, mf_key, client=http),
        )

    if body.provider == "WEATHERLINK":
        # Doble credencial per-user (api_key + api_secret), como WU.
        if not body.api_key or not body.api_secret:
            raise ProviderError(
                "missing_api_key",
                provider="WEATHERLINK",
                detail="WeatherLink requires per-user api_key and api_secret",
                status_code=400,
            )
        return (
            f"{body.api_key}:{body.api_secret}",
            lambda: weatherlink.fetch_current(
                body.station_id, body.api_key, body.api_secret, client=http,
            ),
            lambda: weatherlink.fetch_today_series(
                body.station_id, body.api_key, body.api_secret, client=http,
            ),
        )

    if body.provider == "METEOHUB_IT":
        # API pública; el servicio re-minusculiza el id codificado.
        return (
            "public",
            lambda: meteohub.fetch_current(body.station_id, client=http),
            lambda: meteohub.fetch_today_series(body.station_id, client=http),
        )

    if body.provider == "IEM":
        # API pública; station_id interno = network|station para evitar
        # colisiones entre redes del agregador.
        return (
            "public",
            lambda: iem.fetch_current(body.station_id, client=http),
            lambda: iem.fetch_today_series(body.station_id, client=http),
        )

    if body.provider == "POEM":
        # Auth opcional server-side; el cache secret refleja la config.
        poem_secret = f"{settings.poem_bearer_token}{settings.poem_api_key}{settings.poem_basic_user}"
        return (
            poem_secret or "public",
            lambda: poem.fetch_current(body.station_id, client=http, settings=settings),
            lambda: poem.fetch_today_series(body.station_id, client=http, settings=settings),
        )

    if body.provider == "FROST":
        # Credenciales Basic del servidor.
        frost_id = settings.frost_client_id
        frost_secret = settings.frost_client_secret
        return (
            f"{frost_id}:{frost_secret}",
            lambda: frost.fetch_current(
                body.station_id, frost_id, frost_secret, client=http,
            ),
            lambda: frost.fetch_today_series(
                body.station_id, frost_id, frost_secret, client=http,
            ),
        )

    if body.provider == "METOFFICE":
        # Key del servidor (header apikey). El servicio re-minusculiza
        # el geohash (el schema lo normaliza a mayúsculas).
        mo_key = settings.metoffice_api_key
        return (
            mo_key,
            lambda: metoffice.fetch_current(body.station_id, mo_key, client=http),
            lambda: metoffice.fetch_today_series(body.station_id, mo_key, client=http),
        )

    raise ProviderError(
        "unsupported_provider",
        provider=body.provider,
        detail=f"Provider not implemented: {body.provider}",
        status_code=400,
    )


@router.post(
    "/current",
    response_model=CurrentObservation,
    summary="Observación meteorológica actual",
    description=(
        "Devuelve la observación más reciente de una estación del proveedor "
        "indicado (``WU``, ``AEMET`` o ``METEOCAT``); a medida que se "
        "migren más proveedores se añadirán variantes del request "
        "manteniendo retrocompatibilidad."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "API key inválida del proveedor."},
        404: {"model": ErrorResponse, "description": "Estación no encontrada."},
        429: {"model": ErrorResponse, "description": "Rate limit del proveedor."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
        504: {"model": ErrorResponse, "description": "Timeout contactando al proveedor."},
    },
)
async def post_current(
    body: CurrentObservationRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
    cache: AsyncTTLCache[dict] = Depends(get_current_cache),
    settings: Settings = Depends(get_settings),
) -> CurrentObservation:
    raw = await _fetch_current_dispatch(body, http, cache, settings)
    return CurrentObservation.from_provider_dict(raw)


async def _fetch_current_dispatch(
    body: _ProviderStationRequest,
    http: httpx.AsyncClient,
    cache: AsyncTTLCache[dict],
    settings: Settings,
) -> dict:
    """
    Despacha la observación actual al servicio correspondiente y la
    cachea. Separado de ``post_current`` para que ``/processed`` pueda
    reutilizarlo sin duplicar la lógica de dispatch.
    """
    cache_secret, current_fetcher, _series_fetcher = _resolve_provider_fetchers(
        body, http, settings,
    )
    key = make_cache_key(body.provider, "current", body.station_id, cache_secret)
    return await cache.get_or_fetch(key, current_fetcher)


@router.post(
    "/series/today",
    response_model=TodaySeries,
    summary="Series temporales del día actual",
    description=(
        "Devuelve las series temporales (~5 min de resolución) del día "
        "actual para una estación. Pensado para alimentar gráficos de "
        "temperatura, humedad, presión, radiación y viento en la pestaña "
        "Observación. Si el proveedor devuelve un payload válido pero "
        "vacío, ``has_data`` viene a ``false`` con listas vacías; si hay "
        "un error de red o autenticación, se devuelve ``ErrorResponse`` "
        "con el mismo mapeo que ``/observations/current``."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "API key inválida del proveedor."},
        404: {"model": ErrorResponse, "description": "Estación no encontrada."},
        429: {"model": ErrorResponse, "description": "Rate limit del proveedor."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
        504: {"model": ErrorResponse, "description": "Timeout contactando al proveedor."},
    },
)
async def post_today_series(
    body: TodaySeriesRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
) -> TodaySeries:
    cache_secret, _current_fetcher, series_fetcher = _resolve_provider_fetchers(
        body, http, settings,
    )
    key = make_cache_key(body.provider, "series_today", body.station_id, cache_secret)
    raw = await cache.get_or_fetch(key, series_fetcher)
    lookback_hours = int(body.lookback_hours or 0)
    if lookback_hours > 0 and raw.get("has_data", False):
        try:
            recent_body = RecentSeriesRequest(
                provider=body.provider,
                station_id=body.station_id,
                api_key=body.api_key,
                api_secret=body.api_secret,
                days_back=1,
                station_elevation=body.station_elevation,
            )
            recent_secret, recent_fetcher = _resolve_recent_fetcher(
                recent_body, http, settings, fine=True,
            )
            recent_key = make_cache_key(
                body.provider, "series_recent_1_fine", body.station_id, recent_secret,
            )
            recent_raw = await cache.get_or_fetch(recent_key, recent_fetcher)
            raw = _prepend_lookback_points(raw, recent_raw, lookback_hours)
        except ProviderError as exc:
            logger.info(
                "Lookback no disponible para series/today provider=%s station=%s: %s",
                body.provider, body.station_id, exc.detail or exc.error_code,
            )
        except Exception:
            logger.warning(
                "No se pudo anteponer lookback a series/today provider=%s station=%s",
                body.provider, body.station_id,
                exc_info=True,
            )
    station = stations.get_station(body.provider, body.station_id) or {}
    return TodaySeries.from_provider_dict(derive_trend_series(
        raw,
        period="today",
        station_elevation=_series_station_elevation(body, station),
        station_lat=_float_or_nan(station.get("lat")),
        station_lon=_float_or_nan(station.get("lon")),
        station_tz=str(station.get("tz") or ""),
    ))


@router.post(
    "/current/processed",
    response_model=ProcessedCurrentObservationResponse,
    summary="Observación actual con derivadas meteorológicas",
    description=(
        "Combina la observación actual (``/current``) con la serie del día "
        "(``/series/today``) y ejecuta el pipeline puro "
        "``domain.observation_pipeline.process_observation`` sobre ambas. "
        "El resultado incluye termodinámica completa (Td, Tw, θ, ρ…), "
        "tendencia de presión 3h, claridad del cielo, ET0 acumulada hoy y "
        "balance hídrico.\n\n"
        "**Resiliencia**: si la serie del día falla (red/rate-limit), se "
        "usa una serie vacía y la respuesta sigue siendo válida — solo "
        "ET0, balance y la tendencia desde serie quedarán como ``null``. "
        "Si el ``current`` falla, se devuelve ``ErrorResponse`` con el "
        "mapeo estándar.\n\n"
        "Pensado para que el frontend pueda obtener TODA la info de la "
        "card de Observación con UNA petición en vez de calcular en cliente."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "API key inválida del proveedor."},
        404: {"model": ErrorResponse, "description": "Estación no encontrada."},
        429: {"model": ErrorResponse, "description": "Rate limit del proveedor."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
        504: {"model": ErrorResponse, "description": "Timeout contactando al proveedor."},
    },
)
async def post_current_processed(
    request: Request,
    body: ProcessedCurrentObservationRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
    current_cache: AsyncTTLCache[dict] = Depends(get_current_cache),
    series_cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
) -> ProcessedCurrentObservationResponse:
    ranking_store: ranking_svc.RankingStore | None = getattr(request.app.state, "ranking_store", None)
    ranking_extremes = _daily_extremes_from_ranking_store(
        ranking_store,
        body.provider,
        body.station_id,
    )
    use_ranking_extremes = bool(
        ranking_extremes
        and body.provider in {"AEMET", "METEOGALICIA"}
    )
    # Dispatch + validación per-proveedor (WU lleva api_key del cliente;
    # AEMET y Meteocat usan la del servidor).
    provider_cache_secret, current_fetcher, series_fetcher = _resolve_provider_fetchers(
        body,
        http,
        settings,
        include_provider_daily_extremes=not use_ranking_extremes,
    )

    # Lanzamos los dos fetches en paralelo, ambos pasando por sus
    # cachés respectivos. ``current`` es obligatorio; ``series`` es
    # best-effort para no degradar la respuesta si la serie falla
    # puntualmente. Reusamos los mismos cachés que ``/current`` y
    # ``/series/today``, así si un cliente ya los pidió por separado
    # este endpoint hace hit.
    current_cache_kind = "current_no_daily_extremes" if use_ranking_extremes else "current"
    current_key = make_cache_key(body.provider, current_cache_kind, body.station_id, provider_cache_secret)
    series_key = make_cache_key(body.provider, "series_today", body.station_id, provider_cache_secret)
    current_task = current_cache.get_or_fetch(current_key, current_fetcher)
    series_task = series_cache.get_or_fetch(series_key, series_fetcher)
    current_raw, series_result = await asyncio.gather(
        current_task,
        series_task,
        return_exceptions=True,
    )

    # ``current`` falló: propagamos el ProviderError (FastAPI handler lo
    # serializa). Si fuese otro tipo de excepción inesperada, también la
    # propagamos para que aflore en logs.
    if isinstance(current_raw, BaseException):
        current_error = current_raw
        if (
            body.provider == "AEMET"
            and not isinstance(series_result, BaseException)
            and isinstance(series_result, dict)
            and series_result.get("has_data")
        ):
            current_raw = _aemet_current_from_series(body.station_id, series_result)
            if not current_raw:
                raise current_error
            logger.warning(
                "AEMET current falló para station=%s (%s); usando último punto diezminutal",
                body.station_id,
                type(current_error).__name__,
            )
        else:
            raise current_error

    # ``series`` falló: nos quedamos con dict vacío. Logueamos a nivel
    # info para tener trazabilidad sin alarmar.
    if isinstance(series_result, BaseException):
        logger.info(
            "Series del día falló para %s station=%s (%s); usando serie vacía",
            body.provider, body.station_id, type(series_result).__name__,
        )
        series_dict = {"epochs": [], "has_data": False}
    else:
        series_dict = series_result

    # La caché contiene siempre datos crudos. Los offsets WU son personales
    # y se aplican sobre copias después del cache hit, antes del pipeline.
    if body.provider == "WU" and body.calibration:
        from domain.wu_calibration import (
            apply_wu_current_calibration,
            apply_wu_series_calibration,
        )

        current_raw = apply_wu_current_calibration(current_raw, body.calibration)
        series_dict = apply_wu_series_calibration(series_dict, body.calibration)

    if body.provider == "AEMET":
        current_raw = _overlay_aemet_current_from_newer_series(current_raw, series_dict)
    if use_ranking_extremes:
        current_raw = _overlay_daily_extremes(current_raw, ranking_extremes)

    # ---- Glue de presión: garantizar p_abs_hpa para el pipeline ----
    # El pipeline puro espera ``p_abs_hpa`` (presión absoluta) y
    # ``p_hpa`` (MSL). Cada proveedor expone uno u otro nativamente:
    #
    #   - WU: solo MSL. Computamos absoluta vía ``msl_to_absolute``.
    #   - AEMET: expone ambos (``pres_nmar`` MSL + ``pres`` absoluta).
    #     Si la estación reporta los dos no hay glue; si solo trae MSL,
    #     el cálculo es idéntico al de WU.
    #   - Meteocat: solo absoluta nativa; su servicio ya deriva la MSL
    #     con la inversa, así que aquí llega con ambas y no hay glue.
    #
    # Por simplicidad y robustez aplicamos la misma regla siempre: si
    # falta ``p_abs_hpa`` pero tenemos MSL+altitud+Tc, lo derivamos.
    base_for_pipeline = dict(current_raw)

    # WU (y ocasionalmente otros) devuelve a ratos el current sin
    # presión aunque la serie del día sí la traiga. Sin este fallback
    # la card de presión muestra "—" mientras Tendencias (que usa la
    # serie) sí la pinta. Tomamos el último punto válido de la serie.
    if _is_nan_value(base_for_pipeline.get("p_hpa")):
        for value in reversed(series_dict.get("pressures", []) or []):
            if isinstance(value, (int, float)) and not _is_nan_value(float(value)):
                base_for_pipeline["p_hpa"] = float(value)
                break

    # ---- Resolución de altitud (misma prioridad que el legacy) ------
    # 1. Altitud introducida por el usuario (body.station_elevation > 0):
    #    SUSTITUYE a la del proveedor — el usuario conoce su estación.
    # 2. Elevación reportada por el proveedor/catálogo.
    # 3. 0 m como último recurso, con warning: la absoluta y la
    #    termodinámica (θ, ρ, q…) salen sesgadas sin altitud real.
    elevation_warning: Optional[Dict[str, Any]] = None
    user_elevation = body.station_elevation
    if user_elevation is not None and float(user_elevation) > 0:
        base_for_pipeline["elevation"] = float(user_elevation)
    elif _is_nan_value(base_for_pipeline.get("elevation")):
        base_for_pipeline["elevation"] = 0.0
        elevation_warning = observation_warnings.missing_elevation()

    if _is_nan_value(base_for_pipeline.get("p_abs_hpa")):
        p_hpa = base_for_pipeline.get("p_hpa")
        tc = base_for_pipeline.get("Tc")
        if (
            p_hpa is not None and not _is_nan_value(p_hpa)
            and tc is not None and not _is_nan_value(tc)
        ):
            base_for_pipeline["p_abs_hpa"] = msl_to_absolute(
                float(p_hpa), float(base_for_pipeline["elevation"]), float(tc),
            )

    # El pipeline lee la presión de la serie por la clave
    # ``pressures_abs``; la serie canónica trae ``pressures`` (MSL).
    # Sin esta reconstrucción la tendencia 3h desde serie nunca se
    # calculaba en el backend (Δ3h/etiqueta/flecha salían vacíos).
    series_for_pipeline = dict(series_dict)
    if "pressures_abs" not in series_for_pipeline:
        _elev = base_for_pipeline.get("elevation")
        _z = float(_elev) if isinstance(_elev, (int, float)) and not _is_nan_value(float(_elev)) else 0.0
        # MSL → absoluta con la aproximación de escala de altura constante
        # (H≈8000 m). No usamos la hipsométrica completa porque no tenemos la
        # temperatura MEDIA de la capa estación↔nivel del mar; esto además
        # mantiene paridad con el frontend legacy.
        _factor = math.exp(_z / 8000.0)
        series_for_pipeline["pressures_abs"] = [
            (float(p) / _factor)
            if isinstance(p, (int, float)) and not _is_nan_value(float(p))
            else float("nan")
            for p in series_dict.get("pressures", []) or []
        ]

    # Pipeline puro.
    ctx = ProcessingContext(
        provider_name=body.provider,
        provider_for_pressure=body.provider,
        sun_tz_name=body.sun_tz_name,
        max_data_age_minutes=body.max_data_age_minutes,
        series_override=series_for_pipeline,
        # ``owner_station_id`` solo importa para el frontend (chart
        # series ownership). El backend no lo persiste; lo dejamos vacío.
        owner_station_id="",
    )
    result = process_observation(base_for_pipeline, ctx)

    response_warnings = list(result.warnings)
    if elevation_warning:
        response_warnings.append(elevation_warning)

    return ProcessedCurrentObservationResponse(
        observation=CurrentObservation.from_provider_dict(result.base),
        derivatives=ObservationDerivatives.from_mapping(result.derivatives),
        warnings=response_warnings,
        station=_build_station_info(body.provider, body.station_id, current_raw, series_dict),
        daily_extremes=_build_daily_extremes(
            current_raw, series_dict, provider=body.provider,
        ),
        # ``series_for_pipeline`` (no ``series_dict``) porque incluye
        # ``pressures_abs`` reconstruido; el frontend consume la presión de
        # la serie por esa clave (tendencia 3h, θe, razón de mezcla).
        series=TodaySeries.from_provider_dict(derive_trend_series(
            series_for_pipeline,
            period="today",
            station_elevation=float(base_for_pipeline.get("elevation", 0.0) or 0.0),
            station_lat=_float_or_nan(base_for_pipeline.get("lat")),
            station_lon=_float_or_nan(base_for_pipeline.get("lon")),
            station_tz=body.sun_tz_name,
            fallback_pressure_abs=_float_or_nan(base_for_pipeline.get("p_abs_hpa")),
        )),
    )


def _build_station_info(
    provider: str,
    station_id: str,
    current: dict,
    series: Optional[dict] = None,
) -> StationInfo:
    """
    Metadata de la estación: catálogo del backend cuando existe; para
    proveedores per-user sin catálogo (WU, WeatherLink) se rellena con
    lo que trae la propia observación.

    ``sensors``: del catálogo si lo hay; para WU (sin catálogo) se
    **detecta** a partir de la observación + serie del día (presencia de
    valores válidos), devolviendo capacidades canónicas. Antes esta
    detección vivía en el frontend; ahora es autoritativa del backend.
    """
    record = stations.get_station(provider, station_id) or {}

    def _meta(key: str, fallback_key: str):
        value = record.get(key)
        if value is not None and value != "":
            return value
        fallback = current.get(fallback_key)
        if isinstance(fallback, float) and math.isnan(fallback):
            return None
        return fallback

    sensors = record.get("sensors")
    if sensors is None and provider == "WU":
        from domain.wu_calibration import detect_wu_sensor_presence

        sensors = detect_wu_sensor_presence(current, series or {})

    return StationInfo(
        provider=provider,
        network=str(record.get("network") or ""),
        station_id=station_id,
        name=str(_meta("name", "station_name") or ""),
        lat=_meta("lat", "lat"),
        lon=_meta("lon", "lon"),
        elevation=_meta("elevation", "elevation"),
        tz=record.get("tz"),
        country=record.get("country"),
        region=record.get("region"),
        locality=record.get("locality"),
        connectable=bool(record.get("connectable", True)),
        sensors=sensors,
    )


def _series_extreme(values, fn) -> float:
    valid = [
        float(v) for v in (values or [])
        if isinstance(v, (int, float)) and not _is_nan_value(float(v))
    ]
    return fn(valid) if valid else float("nan")


def _build_daily_extremes(
    current: dict,
    series_dict: dict,
    *,
    provider: str = "",
) -> DailyExtremes:
    """
    Extremos del día: max/min de la serie del día + la observación
    actual (mismo criterio que aplicaba el frontend). La precipitación
    diaria viene del ``/current`` (acumulado del proveedor).
    """

    def _with_current(series_value: float, current_key: str, fn) -> float:
        current_value = current.get(current_key)
        candidates = [
            v for v in (series_value, current_value)
            if isinstance(v, (int, float)) and not _is_nan_value(float(v))
        ]
        return fn(candidates) if candidates else float("nan")

    def _none_if_nan(value: float):
        return None if _is_nan_value(value) else float(value)

    temps = series_dict.get("temps", [])
    rhs = series_dict.get("humidities", [])
    gusts = series_dict.get("gusts", [])
    provider_extremes = series_dict.get("daily_extremes", {})
    if not isinstance(provider_extremes, dict):
        provider_extremes = {}
    current_extremes = current.get("daily_extremes", {})
    if not isinstance(current_extremes, dict):
        current_extremes = {}

    if str(provider or "").strip().upper() == "METEOCAT":
        # Meteocat publica extremos diarios específicos: 40=Tx, 42=Tn,
        # 3=HRx y 44=HRn. La temperatura instantánea del gráfico (32) no
        # es equivalente y nunca debe utilizarse como fallback.
        return DailyExtremes(
            temp_max=_none_if_nan(_float_or_nan(provider_extremes.get("temp_max"))),
            temp_min=_none_if_nan(_float_or_nan(provider_extremes.get("temp_min"))),
            rh_max=_none_if_nan(_float_or_nan(provider_extremes.get("rh_max"))),
            rh_min=_none_if_nan(_float_or_nan(provider_extremes.get("rh_min"))),
            gust_max=_none_if_nan(_float_or_nan(provider_extremes.get("gust_max"))),
            precip_total=_none_if_nan(
                current.get("precip_total")
                if isinstance(current.get("precip_total"), (int, float))
                else float("nan")
            ),
        )

    if str(provider or "").strip().upper() == "AEMET":
        # AEMET publica Tx/Tn oficiales en el endpoint climatológico diario.
        # La serie diezminutal y los extremos parciales del current pueden no
        # reproducir la tabla oficial, así que no se usan como fallback.
        current_extremes = current.get("daily_extremes", {})
        if not isinstance(current_extremes, dict):
            current_extremes = {}
        return DailyExtremes(
            temp_max=_none_if_nan(_float_or_nan(current_extremes.get("temp_max"))),
            temp_min=_none_if_nan(_float_or_nan(current_extremes.get("temp_min"))),
            rh_max=_none_if_nan(_float_or_nan(current_extremes.get("rh_max"))),
            rh_min=_none_if_nan(_float_or_nan(current_extremes.get("rh_min"))),
            gust_max=_none_if_nan(_float_or_nan(current_extremes.get("gust_max"))),
            precip_total=_none_if_nan(
                current.get("precip_total")
                if isinstance(current.get("precip_total"), (int, float))
                else float("nan")
            ),
        )

    if str(provider or "").strip().upper() == "METEOGALICIA":
        # MeteoGalicia publica Tx/Tn en el resumen diario oficial
        # (datosDiariosEstacionsMeteo). La serie horaria usa medias por hora
        # y no debe sustituir esos extremos.
        current_extremes = current.get("daily_extremes", {})
        if not isinstance(current_extremes, dict):
            current_extremes = {}
        return DailyExtremes(
            temp_max=_none_if_nan(_float_or_nan(current_extremes.get("temp_max"))),
            temp_min=_none_if_nan(_float_or_nan(current_extremes.get("temp_min"))),
            rh_max=_none_if_nan(_float_or_nan(current_extremes.get("rh_max"))),
            rh_min=_none_if_nan(_float_or_nan(current_extremes.get("rh_min"))),
            gust_max=_none_if_nan(_float_or_nan(current_extremes.get("gust_max"))),
            precip_total=_none_if_nan(
                current.get("precip_total")
                if isinstance(current.get("precip_total"), (int, float))
                else float("nan")
            ),
        )

    if str(provider or "").strip().upper() == "METEOFRANCE":
        # Météo-France: tx/tn y ux/un son extremos intra-horarios oficiales.
        # La serie t/u contiene instantáneas y no es un fallback válido.
        return DailyExtremes(
            temp_max=_none_if_nan(_float_or_nan(provider_extremes.get("temp_max"))),
            temp_min=_none_if_nan(_float_or_nan(provider_extremes.get("temp_min"))),
            rh_max=_none_if_nan(_float_or_nan(provider_extremes.get("rh_max"))),
            rh_min=_none_if_nan(_float_or_nan(provider_extremes.get("rh_min"))),
            gust_max=_none_if_nan(_float_or_nan(provider_extremes.get("gust_max"))),
            precip_total=_none_if_nan(
                current.get("precip_total")
                if isinstance(current.get("precip_total"), (int, float))
                else float("nan")
            ),
        )

    def _provider_or_series(key: str, values, fn) -> float:
        provider_value = provider_extremes.get(key)
        if isinstance(provider_value, (int, float)) and not _is_nan_value(float(provider_value)):
            return float(provider_value)
        current_value = current_extremes.get(key)
        if isinstance(current_value, (int, float)) and not _is_nan_value(float(current_value)):
            return float(current_value)
        return _series_extreme(values, fn)

    return DailyExtremes(
        temp_max=_none_if_nan(_with_current(_provider_or_series("temp_max", temps, max), "Tc", max)),
        temp_min=_none_if_nan(_with_current(_provider_or_series("temp_min", temps, min), "Tc", min)),
        rh_max=_none_if_nan(_with_current(_provider_or_series("rh_max", rhs, max), "RH", max)),
        rh_min=_none_if_nan(_with_current(_provider_or_series("rh_min", rhs, min), "RH", min)),
        gust_max=_none_if_nan(_with_current(_provider_or_series("gust_max", gusts, max), "gust", max)),
        precip_total=_none_if_nan(
            current.get("precip_total") if isinstance(current.get("precip_total"), (int, float)) else float("nan")
        ),
    )


def _resolve_recent_fetcher(
    body: RecentSeriesRequest,
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    fine: bool = False,
):
    """
    Dispatch del endpoint ``/series/recent``. Devuelve
    ``(cache_secret, fetcher)``. Solo los proveedores cuya pestaña de
    tendencias necesita serie reciente del backend están implementados;
    el resto devuelve ``unsupported_provider``.

    ``fine=True`` lo pide el lookback de ``/series/today``: los proveedores
    que remuestrean la serie reciente a buckets de 3 h (AEMET/Meteocat/
    MeteoGalicia) la sirven a 1 h, para que la tendencia de presión 3h
    pueda arrancar a las 00:00 local en vez de tarde.
    """
    days = int(body.days_back)

    if body.provider == "WU":
        if not body.api_key:
            raise ProviderError(
                "missing_api_key",
                provider="WU",
                detail="WU requires per-user api_key in request body",
                status_code=400,
            )
        return (
            body.api_key,
            lambda: wu.fetch_recent_series(
                body.station_id, body.api_key, days_back=days, client=http,
            ),
        )

    if body.provider == "AEMET":
        aemet_key = settings.aemet_api_key
        return (
            aemet_key,
            lambda: aemet.fetch_recent_series(
                body.station_id, aemet_key, days_back=days, client=http, fine=fine,
            ),
        )

    if body.provider == "METEOCAT":
        meteocat_key = settings.meteocat_api_key
        return (
            meteocat_key,
            lambda: meteocat.fetch_recent_series(
                body.station_id, meteocat_key, days_back=days, client=http, fine=fine,
            ),
        )

    if body.provider == "METEOGALICIA":
        return (
            "public",
            lambda: meteogalicia.fetch_recent_series(
                body.station_id, days_back=days, client=http, fine=fine,
            ),
        )

    if body.provider == "METEOFRANCE":
        mf_key = settings.meteofrance_api_key
        return (
            mf_key,
            lambda: meteofrance.fetch_recent_series(
                body.station_id, mf_key, days_back=days, client=http,
            ),
        )

    if body.provider == "NWS":
        return (
            "public",
            lambda: nws.fetch_recent_series(body.station_id, days_back=days, client=http),
        )

    if body.provider == "FROST":
        frost_id = settings.frost_client_id
        frost_secret = settings.frost_client_secret
        return (
            f"{frost_id}:{frost_secret}",
            lambda: frost.fetch_recent_series(
                body.station_id, frost_id, frost_secret, days_back=days, client=http,
            ),
        )

    if body.provider == "POEM":
        poem_secret = f"{settings.poem_bearer_token}{settings.poem_api_key}{settings.poem_basic_user}"
        return (
            poem_secret or "public",
            lambda: poem.fetch_recent_series(
                body.station_id, days_back=days, client=http, settings=settings,
            ),
        )

    if body.provider == "METEOHUB_IT":
        return (
            "public",
            lambda: meteohub.fetch_recent_series(body.station_id, days_back=days, client=http),
        )

    if body.provider == "IEM":
        return (
            "public",
            lambda: iem.fetch_recent_series(body.station_id, days_back=days, client=http),
        )

    if body.provider == "WEATHERLINK":
        if not body.api_key or not body.api_secret:
            raise ProviderError(
                "missing_api_key",
                provider="WEATHERLINK",
                detail="WeatherLink requires per-user api_key and api_secret",
                status_code=400,
            )
        return (
            f"{body.api_key}:{body.api_secret}",
            lambda: weatherlink.fetch_recent_series(
                body.station_id, body.api_key, body.api_secret,
                days_back=days, client=http,
            ),
        )

    if body.provider == "EUSKALMET":
        # Sin endpoint sinóptico: la serie reciente reusa el día anterior
        # (slots de 10 min). Solo la consume el lookback de /series/today.
        jwt = euskalmet.resolve_jwt(
            settings.euskalmet_jwt,
            settings.euskalmet_private_key_path,
            settings.euskalmet_jwt_iss,
            settings.euskalmet_jwt_email,
        )
        euskalmet_secret = settings.euskalmet_api_key or settings.euskalmet_jwt_iss
        return (
            euskalmet_secret,
            lambda: euskalmet.fetch_recent_series(
                body.station_id, jwt, settings.euskalmet_api_key,
                days_back=days, client=http,
            ),
        )

    if body.provider == "METOFFICE":
        # Feed rodante de ~24 h: la serie reciente reusa el día anterior
        # filtrando ese feed. Solo la consume el lookback de /series/today.
        mo_key = settings.metoffice_api_key
        return (
            mo_key,
            lambda: metoffice.fetch_recent_series(
                body.station_id, mo_key, days_back=days, client=http,
            ),
        )

    raise ProviderError(
        "unsupported_provider",
        provider=body.provider,
        detail=f"Recent series not implemented for provider: {body.provider}",
        status_code=400,
    )


@router.post(
    "/series/recent",
    response_model=RecentSeries,
    summary="Serie reciente (ventana de días) para tendencias",
    description=(
        "Devuelve temperatura, humedad y presión MSL de los últimos "
        "``days_back`` días (por defecto 7) a resolución sinóptica "
        "(~1 punto/hora). Alimenta la pestaña Tendencias y el bloque "
        "``_series_7d`` del frontend. Proveedores soportados: ``NWS``, "
        "``FROST``, ``POEM``, ``METEOHUB_IT`` y ``WEATHERLINK``."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Proveedor no soportado o credenciales ausentes."},
        401: {"model": ErrorResponse, "description": "API key inválida del proveedor."},
        429: {"model": ErrorResponse, "description": "Rate limit del proveedor."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
        504: {"model": ErrorResponse, "description": "Timeout contactando al proveedor."},
    },
)
async def post_recent_series(
    body: RecentSeriesRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
) -> RecentSeries:
    cache_secret, fetcher = _resolve_recent_fetcher(body, http, settings)
    key = make_cache_key(
        body.provider, f"series_recent_{int(body.days_back)}",
        body.station_id, cache_secret,
    )
    raw = await cache.get_or_fetch(key, fetcher)
    # Calibración WU: se aplica DESPUÉS del caché (el raw crudo se cachea
    # compartido entre usuarios; los offsets son per-user) y ANTES de
    # derivar, igual que /current/processed con la serie del día. No muta
    # el dict cacheado (apply_* devuelve copia).
    if body.provider == "WU" and body.calibration:
        from domain.wu_calibration import apply_wu_series_calibration

        raw = apply_wu_series_calibration(raw, body.calibration)
    station = stations.get_station(body.provider, body.station_id) or {}
    return RecentSeries.from_provider_dict(derive_trend_series(
        raw,
        period="synoptic",
        station_elevation=_series_station_elevation(body, station),
    ))

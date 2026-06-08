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

import httpx
from fastapi import APIRouter, Depends

from domain.observation_pipeline import (
    ProcessingContext,
    process_observation,
)
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
    ObservationDerivatives,
    ProcessedCurrentObservationRequest,
    ProcessedCurrentObservationResponse,
    TodaySeries,
    TodaySeriesRequest,
    _ProviderStationRequest,
)
from server.services import aemet, wu
from server.services.cache import AsyncTTLCache, make_cache_key

logger = logging.getLogger(__name__)


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


router = APIRouter(prefix="/observations", tags=["observations"])


@router.post(
    "/current",
    response_model=CurrentObservation,
    summary="Observación meteorológica actual",
    description=(
        "Devuelve la observación más reciente de una estación del proveedor "
        "indicado. Hoy solo se soporta ``WU`` (Weather Underground); a "
        "medida que se migren más proveedores se añadirán variantes del "
        "request manteniendo retrocompatibilidad."
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
    if body.provider == "WU":
        if not body.api_key:
            raise ProviderError(
                "missing_api_key",
                provider="WU",
                detail="WU requires per-user api_key in request body",
                status_code=400,
            )
        key = make_cache_key("WU", "current", body.station_id, body.api_key)
        return await cache.get_or_fetch(
            key,
            lambda: wu.fetch_current(body.station_id, body.api_key, client=http),
        )

    if body.provider == "AEMET":
        # AEMET usa key del servidor; ignoramos body.api_key.
        aemet_key = settings.aemet_api_key
        # Cache key incluye un identificador estable del lado backend,
        # NO la API key (no varía entre usuarios).
        key = make_cache_key("AEMET", "current", body.station_id, aemet_key)
        return await cache.get_or_fetch(
            key,
            lambda: aemet.fetch_current(body.station_id, aemet_key, client=http),
        )

    raise ProviderError(
        "unsupported_provider",
        provider=body.provider,
        detail=f"Provider not implemented: {body.provider}",
        status_code=400,
    )


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
    if body.provider == "WU":
        if not body.api_key:
            raise ProviderError(
                "missing_api_key",
                provider="WU",
                detail="WU requires per-user api_key in request body",
                status_code=400,
            )
        key = make_cache_key("WU", "series_today", body.station_id, body.api_key)
        raw = await cache.get_or_fetch(
            key,
            lambda: wu.fetch_today_series(body.station_id, body.api_key, client=http),
        )
        return TodaySeries.from_provider_dict(raw)

    if body.provider == "AEMET":
        aemet_key = settings.aemet_api_key
        key = make_cache_key("AEMET", "series_today", body.station_id, aemet_key)
        raw = await cache.get_or_fetch(
            key,
            lambda: aemet.fetch_today_series(body.station_id, aemet_key, client=http),
        )
        return TodaySeries.from_provider_dict(raw)

    raise ProviderError(
        "unsupported_provider",
        provider=body.provider,
        detail=f"Provider not implemented: {body.provider}",
        status_code=400,
    )


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
    body: ProcessedCurrentObservationRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
    current_cache: AsyncTTLCache[dict] = Depends(get_current_cache),
    series_cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
) -> ProcessedCurrentObservationResponse:
    # Dispatch + validación per-proveedor. WU lleva api_key del cliente,
    # AEMET usa la del servidor.
    if body.provider == "WU":
        if not body.api_key:
            raise ProviderError(
                "missing_api_key",
                provider="WU",
                detail="WU requires per-user api_key in request body",
                status_code=400,
            )
        provider_cache_secret = body.api_key
        current_fetcher = lambda: wu.fetch_current(body.station_id, body.api_key, client=http)
        series_fetcher = lambda: wu.fetch_today_series(body.station_id, body.api_key, client=http)
    elif body.provider == "AEMET":
        aemet_key = settings.aemet_api_key
        provider_cache_secret = aemet_key  # key del servidor → cache compartido entre usuarios
        current_fetcher = lambda: aemet.fetch_current(body.station_id, aemet_key, client=http)
        series_fetcher = lambda: aemet.fetch_today_series(body.station_id, aemet_key, client=http)
    else:
        raise ProviderError(
            "unsupported_provider",
            provider=body.provider,
            detail=f"Provider not implemented: {body.provider}",
            status_code=400,
        )

    # Lanzamos los dos fetches en paralelo, ambos pasando por sus
    # cachés respectivos. ``current`` es obligatorio; ``series`` es
    # best-effort para no degradar la respuesta si la serie falla
    # puntualmente. Reusamos los mismos cachés que ``/current`` y
    # ``/series/today``, así si un cliente ya los pidió por separado
    # este endpoint hace hit.
    current_key = make_cache_key(body.provider, "current", body.station_id, provider_cache_secret)
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
        raise current_raw

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

    # ---- Glue de presión: garantizar p_abs_hpa para el pipeline ----
    # El pipeline puro espera ``p_abs_hpa`` (presión absoluta) y
    # ``p_hpa`` (MSL). Cada proveedor expone uno u otro nativamente:
    #
    #   - WU: solo MSL. Computamos absoluta vía ``msl_to_absolute``.
    #   - AEMET: expone ambos (``pres_nmar`` MSL + ``pres`` absoluta).
    #     Si la estación reporta los dos no hay glue; si solo trae MSL,
    #     el cálculo es idéntico al de WU.
    #   - (Futuro) Meteocat: solo absoluta. Computar MSL con la inversa.
    #
    # Por simplicidad y robustez aplicamos la misma regla siempre: si
    # falta ``p_abs_hpa`` pero tenemos MSL+altitud+Tc, lo derivamos.
    base_for_pipeline = dict(current_raw)
    if _is_nan_value(base_for_pipeline.get("p_abs_hpa")):
        p_hpa = base_for_pipeline.get("p_hpa")
        tc = base_for_pipeline.get("Tc")
        elevation = base_for_pipeline.get("elevation")
        if (
            p_hpa is not None and not _is_nan_value(p_hpa)
            and tc is not None and not _is_nan_value(tc)
            and elevation is not None and not _is_nan_value(elevation)
        ):
            base_for_pipeline["p_abs_hpa"] = msl_to_absolute(
                float(p_hpa), float(elevation), float(tc),
            )

    # Pipeline puro.
    ctx = ProcessingContext(
        provider_name=body.provider,
        provider_for_pressure=body.provider,
        sun_tz_name=body.sun_tz_name,
        max_data_age_minutes=body.max_data_age_minutes,
        series_override=series_dict,
        # ``owner_station_id`` solo importa para el frontend (chart
        # series ownership). El backend no lo persiste; lo dejamos vacío.
        owner_station_id="",
    )
    result = process_observation(base_for_pipeline, ctx)

    return ProcessedCurrentObservationResponse(
        observation=CurrentObservation.from_provider_dict(result.base),
        derivatives=ObservationDerivatives.from_processed_obs(result.processed),
        warnings=list(result.warnings),
    )

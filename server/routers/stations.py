"""
Router de inventario de estaciones.

Sirve los catálogos normalizados de ``server/services/stations.py``:
búsqueda por cercanía con filtros de proveedor y sensores (la misma
semántica que el filtro del mapa: la estación debe declarar TODOS los
sensores pedidos), ficha de estación individual y conteos por
proveedor.

Son GET (no llevan credenciales): los catálogos son públicos y así las
respuestas son cacheables por URL.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Query

from server.schemas.errors import ErrorResponse, ProviderError
from server.schemas.observation import StationInfo
from server.schemas.stations import (
    StationSearchResponse,
    StationWithDistance,
    GeocodeResponse,
    WeatherLinkStationsRequest,
    WeatherLinkStationsResponse,
)
from server.dependencies.http import get_http_client
from server.dependencies.http import get_series_cache
from server.services import stations
from server.services.cache import AsyncTTLCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get(
    "/geocode",
    response_model=GeocodeResponse,
    summary="Geocodificación textual mediante Nominatim",
)
async def get_geocode(
    q: str = Query(min_length=1, max_length=200),
    lang: str = Query(default="es,en", max_length=64),
    http: httpx.AsyncClient = Depends(get_http_client),
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
) -> GeocodeResponse:
    from server.services import geocoding

    clean_query = q.strip()
    clean_lang = lang.strip() or "es,en"
    key = f"nominatim:geocode:{clean_lang.lower()}:{clean_query.lower()}"
    result = await cache.get_or_fetch(
        key,
        lambda: geocoding.geocode(clean_query, accept_language=clean_lang, client=http),
        ttl_s=24 * 60 * 60,
    )
    return GeocodeResponse(**result)


@router.get(
    "/providers",
    summary="Proveedores con catálogo y nº de estaciones",
    response_model=Dict[str, int],
)
async def get_providers() -> Dict[str, int]:
    return stations.provider_counts()


@router.get(
    "/countries",
    summary="Países presentes en el catálogo y nº de estaciones",
    response_model=Dict[str, int],
)
async def get_countries(
    providers: str = Query(default="", description="Lista separada por comas; vacío = todos."),
) -> Dict[str, int]:
    provider_list: Optional[List[str]] = [
        p for p in (item.strip().upper() for item in providers.split(",")) if p
    ] or None
    return stations.country_counts(providers=provider_list)


@router.get(
    "/country-by-tz",
    summary="País (ISO2) aproximado a partir de una zona horaria IANA",
    response_model=Dict[str, Optional[str]],
)
async def get_country_by_tz(
    tz: str = Query(description="Zona horaria IANA del navegador, p. ej. Europe/Berlin."),
) -> Dict[str, Optional[str]]:
    return {"country": stations.country_for_timezone(tz)}


@router.get(
    "/near",
    response_model=StationSearchResponse,
    summary="Estaciones cercanas a un punto",
    description=(
        "Busca estaciones a menos de ``radius_km`` del punto, ordenadas "
        "por distancia. ``providers`` y ``sensors`` van separados por "
        "comas; ``sensors`` filtra a estaciones que declaren TODOS los "
        "sensores pedidos (thermometer, hygrometer, barometer, "
        "anemometer, wind_vane, rain_gauge, pyranometer, uv)."
    ),
)
async def get_stations_near(
    lat: float = Query(ge=-90.0, le=90.0),
    lon: float = Query(ge=-180.0, le=180.0),
    radius_km: float = Query(default=50.0, gt=0.0, le=2000.0),
    providers: str = Query(default="", description="Lista separada por comas; vacío = todos."),
    countries: str = Query(default="", description="Lista separada por comas; vacío = todos."),
    sensors: str = Query(default="", description="Lista separada por comas; vacío = sin filtro."),
    has_historical: bool = Query(default=False, description="Si true, solo estaciones con histórico disponible."),
    hide_historical_only: bool = Query(default=False, description="Si true, oculta estaciones archivadas sin observación actual."),
    limit: int = Query(default=200, ge=1, le=50000),
) -> StationSearchResponse:
    provider_list: Optional[List[str]] = [
        p for p in (item.strip().upper() for item in providers.split(",")) if p
    ] or None
    sensor_list: Optional[List[str]] = [
        s for s in (item.strip().lower() for item in sensors.split(",")) if s
    ] or None
    country_list: Optional[List[str]] = [
        c for c in (item.strip().upper() for item in countries.split(",")) if c
    ] or None

    results = stations.search_near(
        lat, lon,
        radius_km=radius_km,
        providers=provider_list,
        countries=country_list,
        sensors=sensor_list,
        has_historical=has_historical,
        hide_historical_only=hide_historical_only,
        limit=limit,
    )
    return StationSearchResponse(
        count=len(results),
        stations=[StationWithDistance(**row) for row in results],
    )


@router.get(
    "/catalog",
    response_model=StationSearchResponse,
    summary="Catálogo visible filtrado por país/proveedor",
    description=(
        "Devuelve estaciones del catálogo sin recorte espacial. Requiere "
        "``countries`` para evitar cargas globales accidentales; ``lat`` y "
        "``lon`` son opcionales y solo sirven para ordenar/calcular distancia."
    ),
)
async def get_stations_catalog(
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0),
    providers: str = Query(default="", description="Lista separada por comas; vacío = todos."),
    countries: str = Query(default="", description="Lista separada por comas; requerido."),
    sensors: str = Query(default="", description="Lista separada por comas; vacío = sin filtro."),
    has_historical: bool = Query(default=False, description="Si true, solo estaciones con histórico disponible."),
    hide_historical_only: bool = Query(default=False, description="Si true, oculta estaciones archivadas sin observación actual."),
    limit: int = Query(default=50000, ge=1, le=250000),
) -> StationSearchResponse:
    provider_list: Optional[List[str]] = [
        p for p in (item.strip().upper() for item in providers.split(",")) if p
    ] or None
    sensor_list: Optional[List[str]] = [
        s for s in (item.strip().lower() for item in sensors.split(",")) if s
    ] or None
    country_list: Optional[List[str]] = [
        c for c in (item.strip().upper() for item in countries.split(",")) if c
    ] or None

    results = stations.search_catalog(
        lat=lat,
        lon=lon,
        providers=provider_list,
        countries=country_list,
        sensors=sensor_list,
        has_historical=has_historical,
        hide_historical_only=hide_historical_only,
        limit=limit,
    )
    return StationSearchResponse(
        count=len(results),
        stations=[StationWithDistance(**row) for row in results],
    )


@router.post(
    "/weatherlink",
    response_model=WeatherLinkStationsResponse,
    summary="Estaciones personales de WeatherLink",
    responses={
        401: {"model": ErrorResponse, "description": "Credenciales rechazadas."},
        429: {"model": ErrorResponse, "description": "Límite del proveedor."},
        502: {"model": ErrorResponse, "description": "Error de WeatherLink."},
        504: {"model": ErrorResponse, "description": "Timeout de WeatherLink."},
    },
)
async def post_weatherlink_stations(
    body: WeatherLinkStationsRequest,
    http: httpx.AsyncClient = Depends(get_http_client),
) -> WeatherLinkStationsResponse:
    from server.services import weatherlink

    station_rows = await weatherlink.fetch_stations(
        body.api_key, body.api_secret, client=http,
    )
    return WeatherLinkStationsResponse(stations=station_rows)


@router.get(
    "/{provider}/by-slug/{slug}",
    response_model=StationInfo,
    summary="Ficha de una estación por slug de nombre (deep links)",
    description=(
        "Resuelve una estación a partir del slug de su nombre, p. ej. "
        "``/stations/AEMET/by-slug/barcelona-drassanes``. Sirve para abrir "
        "links compartibles del tipo ``?e=<provider>~<slug>``."
    ),
    responses={404: {"model": ErrorResponse, "description": "Estación no encontrada."}},
)
async def get_station_by_slug(provider: str, slug: str) -> StationInfo:
    record = stations.find_by_slug(provider, slug)
    if record is None:
        raise ProviderError(
            "station_not_found",
            provider=str(provider).strip().upper(),
            detail=f"No station matches slug: {slug}",
            status_code=404,
        )
    return StationInfo(**{k: v for k, v in record.items() if k != "distance_km"})


@router.get(
    "/{provider}/{station_id}",
    response_model=StationInfo,
    summary="Ficha de una estación del catálogo",
    responses={404: {"model": ErrorResponse, "description": "Estación no encontrada."}},
)
async def get_station(provider: str, station_id: str) -> StationInfo:
    record = stations.get_station(provider, station_id)
    if record is None:
        raise ProviderError(
            "station_not_found",
            provider=str(provider).strip().upper(),
            detail=f"Station not in catalog: {station_id}",
            status_code=404,
        )
    return StationInfo(**{k: v for k, v in record.items() if k != "distance_km"})

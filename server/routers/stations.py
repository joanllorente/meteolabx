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

import asyncio
import logging
from collections import OrderedDict
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

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

# La interpolación mundial y su textura se calculan una vez por refresh del
# ranking. Los parámetros de viewport se conservan por compatibilidad, pero el
# frontend normal usa una única imagen mundial y no solicita nada al mover.
_temperature_field_cache: Dict[str, object] = {
    "data_key": None,
    "grid": None,
    "images": OrderedDict(),
}
_temperature_field_lock = asyncio.Lock()
_TEMPERATURE_FIELD_VIEWPORT_CACHE_SIZE = 12
_wind_field_cache: Dict[str, object] = {
    "data_key": None,
    "png": None,
}
_wind_field_lock = asyncio.Lock()
_precipitation_field_cache: Dict[str, object] = {"data_key": None, "png": None}
_precipitation_field_lock = asyncio.Lock()


@router.get(
    "/temperature-field.png",
    summary="Campo mundial de temperatura interpolado (PNG RGBA)",
    description=(
        "Interpolación local/regional de las temperaturas instantáneas que "
        "el refresh horario del ranking trae en bulk. "
        "Transparente donde no hay estaciones cerca. Pensado para la capa "
        "térmica del mapa; bounds: lon -180..180, lat -60..85."
    ),
    responses={404: {"model": ErrorResponse, "description": "El ranking aún no tiene datos."}},
)
async def get_temperature_field(
    request: Request,
    west: Optional[float] = Query(default=None, ge=-180.0, le=180.0),
    south: Optional[float] = Query(default=None, ge=-60.0, le=85.0),
    east: Optional[float] = Query(default=None, ge=-180.0, le=180.0),
    north: Optional[float] = Query(default=None, ge=-60.0, le=85.0),
    width: int = Query(default=1600, ge=256, le=2048),
    height: int = Query(default=1000, ge=256, le=2048),
) -> Response:
    from server.services.temperature_field import (
        COLOR_SCALE_VERSION,
        FIELD_ALGORITHM_VERSION,
        GLOBAL_RENDER_SIZE,
        interpolate_grid,
        render_global_grid_png,
        render_grid_png,
    )

    store = getattr(request.app.state, "ranking_store", None)
    points = store.current_temperature_points() if store is not None else []
    if not points:
        raise ProviderError(
            "no_data", provider="RANKING",
            detail="No hay temperaturas instantáneas todavía", status_code=404,
        )
    requested = (west, south, east, north)
    if any(value is not None for value in requested) and not all(
        value is not None for value in requested
    ):
        raise ProviderError(
            "invalid_viewport",
            provider="RANKING",
            detail="west, south, east y north deben enviarse juntos",
            status_code=400,
        )
    bounds = None
    if all(value is not None for value in requested):
        bounds = tuple(float(value) for value in requested)
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ProviderError(
                "invalid_viewport",
                provider="RANKING",
                detail="Los límites del viewport no son válidos",
                status_code=400,
            )

    snapshot_key = store.updated_at.isoformat() if store.updated_at else str(len(points))
    data_key = (
        f"field-{FIELD_ALGORITHM_VERSION}:"
        f"palette-{COLOR_SCALE_VERSION}:{snapshot_key}"
    )
    image_key = (
        ("global", *GLOBAL_RENDER_SIZE)
        if bounds is None
        else (bounds, int(width), int(height))
    )
    async with _temperature_field_lock:
        if _temperature_field_cache["data_key"] != data_key:
            temp, mask = await asyncio.to_thread(
                interpolate_grid,
                points,
            )
            # El recorte vuelve a colorear la rejilla, pero no necesita dobles
            # de 64 bits. Mantenerla compacta evita retener decenas de MB extra
            # entre peticiones de distintos viewports.
            _temperature_field_cache["grid"] = (
                temp.astype("float32", copy=False),
                mask.astype("float16", copy=False),
            )
            _temperature_field_cache["images"] = OrderedDict()
            _temperature_field_cache["data_key"] = data_key

        images = _temperature_field_cache["images"]
        png = images.get(image_key)
        if png is None:
            temp, mask = _temperature_field_cache["grid"]
            if bounds is None:
                png = await asyncio.to_thread(render_global_grid_png, temp, mask)
            else:
                png = await asyncio.to_thread(
                    render_grid_png,
                    temp,
                    mask,
                    bounds=bounds,
                    width=width,
                    height=height,
                )
            images[image_key] = png
            images.move_to_end(image_key)
            while len(images) > _TEMPERATURE_FIELD_VIEWPORT_CACHE_SIZE:
                images.popitem(last=False)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get(
    "/current-temperatures",
    summary="Temperatura instantánea por estación (para las etiquetas del mapa)",
    response_model=Dict[str, object],
)
async def get_current_temperatures(request: Request) -> Dict[str, object]:
    store = getattr(request.app.state, "ranking_store", None)
    records = store.current_temperature_records() if store is not None else []
    return {
        "count": len(records),
        "updated_at": (
            store.updated_at.isoformat()
            if store is not None and store.updated_at is not None
            else None
        ),
        "points": [
            {
                "lat": rec.lat,
                "lon": rec.lon,
                "t": round(rec.tcur, 1),
                "provider": rec.provider,
                "station_id": rec.station_id,
                "name": rec.name,
                "tmax": rec.tmax,
                "tmin": rec.tmin,
                "time": rec.local_time,
                "country": rec.country,
            }
            for rec in records
        ],
    }


@router.get(
    "/wind-field.png",
    summary="Campo mundial de velocidad del viento interpolado (PNG RGBA)",
    responses={404: {"model": ErrorResponse, "description": "Aún no hay viento reciente."}},
)
async def get_wind_field(request: Request) -> Response:
    from server.services.temperature_field import (
        GLOBAL_RENDER_SIZE,
        interpolate_grid,
        render_global_grid_png,
    )
    from server.services.wind_field import (
        BAND_SIZE_KMH,
        COLOR_SCALE_VERSION,
        COLOR_STOPS,
        FIELD_ALGORITHM_VERSION,
    )

    store = getattr(request.app.state, "ranking_store", None)
    points = store.current_wind_points() if store is not None else []
    if not points:
        raise ProviderError(
            "no_data", provider="RANKING",
            detail="No hay vectores de viento recientes todavía", status_code=404,
        )
    snapshot_key = store.updated_at.isoformat() if store.updated_at else str(len(points))
    data_key = (
        f"wind-field-{FIELD_ALGORITHM_VERSION}:"
        f"palette-{COLOR_SCALE_VERSION}:{snapshot_key}:{len(points)}"
    )
    async with _wind_field_lock:
        if _wind_field_cache["data_key"] != data_key:
            speed, mask = await asyncio.to_thread(interpolate_grid, points)
            png = await asyncio.to_thread(
                render_global_grid_png,
                speed.astype("float32", copy=False),
                mask.astype("float16", copy=False),
                width=GLOBAL_RENDER_SIZE[0],
                height=GLOBAL_RENDER_SIZE[1],
                color_stops=COLOR_STOPS,
                band_size=BAND_SIZE_KMH,
            )
            _wind_field_cache["data_key"] = data_key
            _wind_field_cache["png"] = png
        png = _wind_field_cache["png"]
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get(
    "/current-winds",
    summary="Viento instantáneo por estación para el mapa",
    response_model=Dict[str, object],
)
async def get_current_winds(request: Request) -> Dict[str, object]:
    store = getattr(request.app.state, "ranking_store", None)
    records = store.current_wind_records() if store is not None else []
    return {
        "count": len(records),
        "updated_at": (
            store.updated_at.isoformat()
            if store is not None and store.updated_at is not None
            else None
        ),
        "points": [
            {
                "lat": rec.lat,
                "lon": rec.lon,
                "speed": rec.wind,
                "direction": rec.wind_dir,
                "gust": rec.gust,
                "provider": rec.provider,
                "station_id": rec.station_id,
                "name": rec.name,
                "time": rec.local_time,
                "country": rec.country,
            }
            for rec in records
        ],
    }


@router.get(
    "/precipitation-field.png",
    summary="Precipitacion acumulada en las ultimas 24 h (PNG RGBA)",
    responses={404: {"model": ErrorResponse, "description": "Aun no hay acumulados 24 h."}},
)
async def get_precipitation_field(request: Request) -> Response:
    from server.services.precipitation_field import (
        BAND_SIZE_MM,
        COLOR_SCALE_VERSION,
        COLOR_STOPS,
        FIELD_ALGORITHM_VERSION,
        interpolate_precipitation_grid,
    )
    from server.services.temperature_field import GLOBAL_RENDER_SIZE, render_global_grid_png

    store = getattr(request.app.state, "ranking_store", None)
    points = store.current_precipitation_points() if store is not None else []
    if not points:
        raise ProviderError(
            "no_data", provider="RANKING",
            detail="No hay acumulados moviles de 24 horas todavia", status_code=404,
        )
    snapshot_key = store.updated_at.isoformat() if store.updated_at else str(len(points))
    data_key = (
        f"precipitation-field-{FIELD_ALGORITHM_VERSION}:"
        f"palette-{COLOR_SCALE_VERSION}:{snapshot_key}:{len(points)}"
    )
    async with _precipitation_field_lock:
        if _precipitation_field_cache["data_key"] != data_key:
            amount, mask = await asyncio.to_thread(interpolate_precipitation_grid, points)
            png = await asyncio.to_thread(
                render_global_grid_png,
                amount.astype("float32", copy=False),
                mask.astype("float16", copy=False),
                width=GLOBAL_RENDER_SIZE[0],
                height=GLOBAL_RENDER_SIZE[1],
                color_stops=COLOR_STOPS,
                band_size=BAND_SIZE_MM,
                preserve_mask_alpha=True,
            )
            _precipitation_field_cache["data_key"] = data_key
            _precipitation_field_cache["png"] = png
        png = _precipitation_field_cache["png"]
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get(
    "/precipitations-24h",
    summary="Precipitacion movil de 24 h por estacion",
    response_model=Dict[str, object],
)
async def get_precipitations_24h(request: Request) -> Dict[str, object]:
    store = getattr(request.app.state, "ranking_store", None)
    records = store.current_precipitation_records() if store is not None else []
    return {
        "count": len(records),
        "updated_at": (
            store.updated_at.isoformat()
            if store is not None and store.updated_at is not None else None
        ),
        "points": [
            {
                "lat": rec.lat,
                "lon": rec.lon,
                "amount": rec.rain_24h,
                "observed_at": rec.rain_24h_at,
                "provider": rec.provider,
                "station_id": rec.station_id,
                "name": rec.name,
                "time": rec.local_time,
                "country": rec.country,
            }
            for rec in records
        ],
    }


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

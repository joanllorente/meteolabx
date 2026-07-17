"""
Router de datasets históricos / climogramas.

Cada proveedor tiene un port async puro de su climo en
``server/services/*_climo.py``; el endpoint despacha a su rama en
``_run_async_port`` (hecho: WU, AEMET, METEOCAT, METEOFRANCE,
METEOGALICIA, FROST). El antiguo dispatcher legacy en threadpool
(``utils.historical_dispatch``) ya no se usa desde aquí.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client, get_series_cache
from server.schemas.climo import (
    ClimoDatasetRequest,
    ClimoDatasetResponse,
    FrostPeriodOptionsRequest,
    FrostPeriodOptionsResponse,
)
from server.schemas.errors import ErrorResponse, ProviderError
from server.services.cache import AsyncTTLCache, make_cache_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/climo", tags=["climo"])

# Proveedores con datos históricos/climogramas.
CLIMO_PROVIDERS = (
    "WU", "AEMET", "METEOCAT", "METEOFRANCE", "METEOGALICIA", "FROST",
    "WEATHERLINK", "IEM", "GEOSPHERE", "SMHI", "ECCC",
)


def _serialize_dataset(dataset: Any) -> Optional[str]:
    """DataFrame → JSON ``orient="table"`` (round-trip con dtypes)."""
    try:
        import pandas as pd
    except Exception:  # pragma: no cover - pandas siempre está
        return None
    if not isinstance(dataset, pd.DataFrame) or dataset.empty:
        return None
    return dataset.reset_index(drop=True).to_json(orient="table", date_format="iso")


async def _run_async_port(
    body: ClimoDatasetRequest, client: httpx.AsyncClient, settings: Settings,
) -> Tuple[Any, Any]:
    if body.provider == "METEOCAT":
        from server.services import meteocat_climo

        return await meteocat_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.meteocat_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
    if body.provider == "METEOGALICIA":
        from server.services import meteogalicia_climo

        dataset = await meteogalicia_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "WU":
        from server.services import wu_climo

        dataset = await wu_climo.fetch_climo_daily_for_periods(
            client,
            body.station_id,
            body.api_key or "",
            [(p.start, p.end) for p in body.periods],
        )
        return dataset, None
    if body.provider == "WEATHERLINK":
        from server.services import weatherlink_climo

        dataset = await weatherlink_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or "",
            body.api_secret or "",
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "GEOSPHERE":
        from server.services import geosphere_climo

        dataset = await geosphere_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "ECCC":
        from server.services import eccc_climo

        dataset = await eccc_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "SMHI":
        from server.services import smhi_climo

        dataset = await smhi_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "IEM":
        from server.services import iem_climo

        dataset = await iem_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "AEMET":
        from server.services import aemet_climo

        dataset = await aemet_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.aemet_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "METEOFRANCE":
        from server.services import meteofrance_climo

        dataset = await meteofrance_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.meteofrance_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "FROST":
        from server.services import frost_climo

        dataset = await frost_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            selected_months=[int(m) for m in body.selected_months],
            frost_period=body.frost_period,
            frost_periods=list(body.frost_periods),
            client_id=settings.frost_client_id,
            client_secret=settings.frost_client_secret,
        )
        return dataset, None
    raise ProviderError(  # pragma: no cover - el endpoint valida contra CLIMO_PROVIDERS
        "unsupported_provider", provider=body.provider, status_code=400,
    )


async def _fetch_dataset(
    body: ClimoDatasetRequest,
    settings: Settings,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    try:
        dataset, extremes = await _run_async_port(body, client, settings)
    except ProviderError:
        raise
    except Exception as exc:
        logger.warning(
            "Climo async falló para %s/%s: %s",
            body.provider, body.station_id, exc,
        )
        raise ProviderError(
            "provider_bad_response",
            provider=body.provider,
            detail=f"Climo dispatch failed: {exc}",
            status_code=502,
        ) from exc

    serialized = _serialize_dataset(dataset)
    return {
        "dataset": serialized,
        "extremes": extremes if isinstance(extremes, dict) and extremes else None,
        "has_data": serialized is not None,
    }


@router.post(
    "/dataset",
    response_model=ClimoDatasetResponse,
    summary="Dataset histórico / de climograma",
    description=(
        "Devuelve el dataset diario/mensual/anual que alimenta la pestaña "
        "de Históricos y Climogramas, en el esquema de columnas común "
        "(date, epoch, temp_mean/max/min, wind_mean, gust_max, "
        "precip_total + extras por proveedor), serializado como JSON "
        "``orient='table'`` de pandas. Proveedores: "
        "``WU``, ``AEMET``, ``METEOCAT``, ``METEOFRANCE``, "
        "``METEOGALICIA``, ``FROST``, ``WEATHERLINK`` e ``IEM``."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Proveedor sin datos históricos."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
    },
)
async def post_climo_dataset(
    body: ClimoDatasetRequest,
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> ClimoDatasetResponse:
    if body.provider not in CLIMO_PROVIDERS:
        raise ProviderError(
            "unsupported_provider",
            provider=body.provider,
            detail=f"Historical dataset not available for provider: {body.provider}",
            status_code=400,
        )

    fingerprint = "|".join(
        [
            body.summary_mode,
            ",".join(f"{p.start}:{p.end}" for p in body.periods),
            ",".join(str(y) for y in body.selected_years),
            ",".join(str(m) for m in body.selected_months),
            body.frost_period,
            ",".join(body.frost_periods),
        ]
    )
    key = make_cache_key(
        body.provider,
        f"climo_{fingerprint}",
        body.station_id,
        f"{body.api_key}:{body.api_secret}" if body.api_secret else body.api_key or "server",
    )
    raw = await cache.get_or_fetch(key, lambda: _fetch_dataset(body, settings, client))
    return ClimoDatasetResponse(**raw)


@router.post(
    "/frost/period-options",
    response_model=FrostPeriodOptionsResponse,
    summary="Periodos de normales climáticas disponibles (Frost)",
    description=(
        "Periodos de normales (p. ej. ``1991/2020``) que la estación Frost "
        "publica con datos, separados en mensual/anual, para poblar el "
        "selector de la pestaña de climogramas."
    ),
    responses={502: {"model": ErrorResponse, "description": "Error upstream del proveedor."}},
)
async def post_frost_period_options(
    body: FrostPeriodOptionsRequest,
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> FrostPeriodOptionsResponse:
    from server.services import frost_climo

    async def _fetch() -> Dict[str, Any]:
        return await frost_climo.fetch_period_options(
            client,
            body.station_id,
            client_id=settings.frost_client_id,
            client_secret=settings.frost_client_secret,
        )

    key = make_cache_key("FROST", "frost_period_options", body.station_id, "server")
    raw = await cache.get_or_fetch(key, _fetch)
    return FrostPeriodOptionsResponse(**raw)

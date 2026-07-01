"""
Normales climáticas de Frost (frost.met.no) como servicio async puro.

Implementa la rama FROST de ``/v1/climo/dataset`` de forma asíncrona.

- Transporte: ``httpx.AsyncClient`` inyectado, HTTP Basic con las
  credenciales del servidor (``frost_client_id``/``frost_client_secret``).
- Parsing/ensamblado: ``domain/parsing/frost_climo``.
- Modos (idénticos a ``_fetch_frost_historical_dataset``):
  ``monthly`` → normales mensuales de UN periodo para los meses
  seleccionados; ``annual`` → fila anual por cada periodo seleccionado.
- Antes de pedir normales se consulta ``/climatenormals/available`` y
  solo se piden los elementos publicados para el periodo (Frost da 400
  si se piden elementos inexistentes).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from domain.parsing.frost_climo import (
    CLIMO_MONTHLY_ELEMENT_MAP,
    CLIMO_YEARLY_ELEMENT_MAP,
    build_monthly_rows,
    build_period_options,
    build_yearly_row,
    climo_value_map,
    empty_climo_df,
    parse_available_climo_elements,
    rows_to_climo_df,
)

logger = logging.getLogger(__name__)

PROVIDER = "FROST"
BASE_URL = "https://frost.met.no"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
}


async def _request_json(
    client: httpx.AsyncClient,
    endpoint: str,
    params: Dict[str, Any],
    client_id: str,
    client_secret: str,
) -> Any:
    try:
        response = await client.get(
            f"{BASE_URL}{endpoint}",
            params=params,
            headers=HEADERS,
            auth=(client_id, client_secret),
        )
    except httpx.TimeoutException as exc:
        raise ProviderError("provider_timeout", provider=PROVIDER, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("provider_network", provider=PROVIDER, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {response.status_code} en {endpoint}",
            status_code=502 if response.status_code >= 500 else 400,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("provider_bad_response", provider=PROVIDER, detail=str(exc)) from exc


async def _available_elements_by_period(
    client: httpx.AsyncClient,
    station_id: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, List[str]]:
    payload = await _request_json(
        client,
        "/climatenormals/available/v0.jsonld",
        {"sources": str(station_id).strip().upper()},
        client_id, client_secret,
    )
    return parse_available_climo_elements(payload)


async def fetch_period_options(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    client_id: str,
    client_secret: str,
) -> Dict[str, List[str]]:
    """
    Periodos de normales disponibles para los climogramas, para poblar
    el selector de la UI: ``{"monthly": [...], "annual": [...]}``.
    """
    available = await _available_elements_by_period(client, station_id, client_id, client_secret)
    return build_period_options(available)


async def _fetch_normals(
    client: httpx.AsyncClient,
    station_id: str,
    period: str,
    elements: Sequence[str],
    client_id: str,
    client_secret: str,
) -> Any:
    return await _request_json(
        client,
        "/climatenormals/v0.jsonld",
        {
            "sources": str(station_id).strip().upper(),
            "period": str(period).strip(),
            "elements": ",".join(elements),
        },
        client_id, client_secret,
    )


async def fetch_climo_monthly_for_period(
    client: httpx.AsyncClient,
    station_id: str,
    period: str,
    months: Sequence[int],
    *,
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    """Normales mensuales de un periodo para los meses seleccionados."""
    period = str(period or "").strip()
    selected_months = sorted({int(month) for month in months if 1 <= int(month) <= 12})
    if not period or not selected_months:
        return empty_climo_df()

    available = await _available_elements_by_period(client, station_id, client_id, client_secret)
    available_set = set(available.get(period, []))
    elements = [e for e in CLIMO_MONTHLY_ELEMENT_MAP.values() if e in available_set]
    if not elements:
        return empty_climo_df()

    payload = await _fetch_normals(client, station_id, period, elements, client_id, client_secret)
    rows = build_monthly_rows(period, selected_months, available_set, climo_value_map(payload))
    return rows_to_climo_df(rows)


async def fetch_climo_yearly_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[str],
    *,
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    """Una fila anual por cada periodo de normales seleccionado."""
    selected_periods = [str(period).strip() for period in periods if str(period).strip()]
    if not selected_periods:
        return empty_climo_df()

    available = await _available_elements_by_period(client, station_id, client_id, client_secret)

    async def _row_for(period: str) -> Optional[Dict[str, Any]]:
        available_set = set(available.get(period, []))
        elements = [e for e in CLIMO_YEARLY_ELEMENT_MAP.values() if e in available_set]
        if not elements:
            return None
        payload = await _fetch_normals(
            client, station_id, period, elements, client_id, client_secret,
        )
        return build_yearly_row(period, available_set, climo_value_map(payload))

    rows = [row for row in await asyncio.gather(*(
        _row_for(period) for period in selected_periods
    )) if row is not None]
    return rows_to_climo_df(rows)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    summary_mode: str,
    selected_months: Sequence[int],
    frost_period: str,
    frost_periods: Sequence[str],
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    """Selección de modo idéntica a ``_fetch_frost_historical_dataset``."""
    if summary_mode == "monthly":
        return await fetch_climo_monthly_for_period(
            client, station_id, frost_period, selected_months,
            client_id=client_id, client_secret=client_secret,
        )
    return await fetch_climo_yearly_for_periods(
        client, station_id, frost_periods,
        client_id=client_id, client_secret=client_secret,
    )

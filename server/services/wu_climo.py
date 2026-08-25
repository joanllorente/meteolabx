"""
Histórico diario de WU (v2/pws/history/daily) como servicio async puro.

Implementa la rama WU de ``/v1/climo/dataset`` de forma asíncrona.

- Transporte: ``httpx.AsyncClient`` inyectado; api_key per-user de WU.
- Parsing/ensamblado: ``domain/parsing/wu_climo``.
- El API de WU limita las ventanas del history/daily → los periodos se
  trocean en chunks de ≤31 días y se descargan con concurrencia
  acotada (semáforo) para no tropezar con el rate limit.
- La calibración per-user NO se aplica aquí: es estado del frontend
  (igual que con la serie hourly/7day, se aplica en el caller).
- Errores por chunk: best-effort como el resto de fetchers climo (un
  chunk caído no tumba el dataset), pero credenciales inválidas (401)
  cortan en seco con ``ProviderError`` — reintentar chunk a chunk con
  una key mala solo quema cuota.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from server.services.climo_cache import get_or_fetch_climo_block
from domain.parsing.wu_climo import (
    clip_period_tuples_to_today,
    empty_daily_dataframe,
    merge_daily_chunks,
    normalize_wu_daily_payload,
)

logger = logging.getLogger(__name__)

PROVIDER = "WU"
HISTORY_DAILY_URL = "https://api.weather.com/v2/pws/history/daily"

# Concurrencia máxima de chunks simultáneos contra api.weather.com.
_MAX_CONCURRENT_CHUNKS = 4


class _TransientChunkFailure(Exception):
    """Evita cachear como «sin datos» un fallo temporal de WU."""


async def _fetch_chunk_payload_uncached(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    station_id: str,
    api_key: str,
    start_txt: str,
    end_txt: str,
) -> Dict[str, Any]:
    params = {
        "stationId": str(station_id).strip(),
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal",
        "startDate": start_txt,
        "endDate": end_txt,
    }
    async with semaphore:
        try:
            response = await client.get(HISTORY_DAILY_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "Chunk WU history %s→%s falló para %s: %s",
                start_txt, end_txt, station_id, exc,
            )
            raise _TransientChunkFailure() from exc

    if response.status_code == 401:
        raise ProviderError(
            "provider_unauthorized", provider=PROVIDER,
            detail="WU API key inválida para history/daily",
            status_code=401,
        )
    if response.status_code in (204, 404):
        return {"observations": []}
    if response.status_code >= 400:
        logger.warning(
            "Chunk WU history %s→%s devolvió HTTP %s para %s",
            start_txt, end_txt, response.status_code, station_id,
        )
        raise _TransientChunkFailure()

    try:
        payload = response.json()
    except ValueError as exc:
        raise _TransientChunkFailure() from exc
    if not isinstance(payload, dict):
        raise _TransientChunkFailure()
    return payload


async def _fetch_chunk_payload(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    station_id: str,
    api_key: str,
    start_txt: str,
    end_txt: str,
) -> Dict[str, Any]:
    try:
        return await get_or_fetch_climo_block(
            provider=PROVIDER,
            kind=f"daily:{start_txt}:{end_txt}",
            station_id=station_id,
            credential=api_key,
            client=client,
            end_date=date.fromisoformat(f"{end_txt[:4]}-{end_txt[4:6]}-{end_txt[6:8]}"),
            fetcher=lambda: _fetch_chunk_payload_uncached(
                client, semaphore, station_id, api_key, start_txt, end_txt,
            ),
        )
    except _TransientChunkFailure:
        return {"observations": []}


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    """Histórico diario para los periodos pedidos, en el esquema común."""
    if not periods:
        return empty_daily_dataframe()

    chunk_windows_set: set[Tuple[str, str]] = set()
    clipped = clip_period_tuples_to_today(list(periods), today_date=today_date)
    for start, end in clipped:
        cursor = start
        while cursor <= end:
            next_month = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
            month_end = next_month - timedelta(days=1)
            chunk_end = min(end, month_end)
            # Un mes natural tiene como máximo 31 días: cumple el límite de
            # WU y produce claves estables reutilizables entre selecciones.
            chunk_windows_set.add(
                (cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"))
            )
            cursor = chunk_end + timedelta(days=1)

    chunk_windows = sorted(chunk_windows_set)

    if not chunk_windows:
        return empty_daily_dataframe()

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHUNKS)
    payloads = await asyncio.gather(*(
        _fetch_chunk_payload(client, semaphore, station_id, api_key, start_txt, end_txt)
        for start_txt, end_txt in chunk_windows
    ))

    chunks = [normalize_wu_daily_payload(payload) for payload in payloads]
    return merge_daily_chunks(chunks)

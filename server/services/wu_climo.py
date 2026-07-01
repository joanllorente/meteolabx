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
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from domain.parsing.wu_climo import (
    clip_period_tuples_to_today,
    empty_daily_dataframe,
    iter_chunks,
    merge_daily_chunks,
    normalize_wu_daily_payload,
)

logger = logging.getLogger(__name__)

PROVIDER = "WU"
HISTORY_DAILY_URL = "https://api.weather.com/v2/pws/history/daily"

# Concurrencia máxima de chunks simultáneos contra api.weather.com.
_MAX_CONCURRENT_CHUNKS = 4


async def _fetch_chunk_payload(
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
            return {"observations": []}

    if response.status_code == 401:
        raise ProviderError(
            "provider_unauthorized", provider=PROVIDER,
            detail="WU API key inválida para history/daily",
            status_code=401,
        )
    if response.status_code >= 400:
        # 404/204 de WU = sin datos para la ventana; resto best-effort.
        if response.status_code not in (204, 404):
            logger.warning(
                "Chunk WU history %s→%s devolvió HTTP %s para %s",
                start_txt, end_txt, response.status_code, station_id,
            )
        return {"observations": []}

    try:
        payload = response.json()
    except ValueError:
        return {"observations": []}
    return payload if isinstance(payload, dict) else {"observations": []}


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

    chunk_windows: List[Tuple[str, str]] = []
    for start, end in clip_period_tuples_to_today(list(periods), today_date=today_date):
        for chunk_start, chunk_end in iter_chunks(start, end, max_days=31):
            chunk_windows.append(
                (chunk_start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"))
            )

    if not chunk_windows:
        return empty_daily_dataframe()

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHUNKS)
    payloads = await asyncio.gather(*(
        _fetch_chunk_payload(client, semaphore, station_id, api_key, start_txt, end_txt)
        for start_txt, end_txt in chunk_windows
    ))

    chunks = [normalize_wu_daily_payload(payload) for payload in payloads]
    return merge_daily_chunks(chunks)

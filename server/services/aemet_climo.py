"""
Climatología de AEMET OpenData como servicio async puro.

Implementa la rama AEMET de ``/v1/climo/dataset`` de forma asíncrona.

- Transporte: ``httpx.AsyncClient`` inyectado + patrón OpenData de dos
  pasos (reutiliza ``_fetch_aemet_two_step`` del servicio de
  observaciones, que ya mapea errores de body ``estado`` a
  ``ProviderError``).
- Parsing/ensamblado: ``domain/parsing/aemet_climo``.
- Límites del API respetados: diario ≤ ~6 meses por petición (chunks
  de 150 días), mensual/anual ≤ 36 meses (bloques de 3 años). AEMET
  ratelimita agresivo → concurrencia acotada a 2.
- Errores por chunk: best-effort (un chunk caído no tumba el dataset),
  salvo credenciales inválidas (401) que cortan en seco.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from server.services.aemet import BASE_URL, _fetch_aemet_two_step
from domain.parsing.aemet_climo import (
    _aemet_daily_record_to_row,
    _bucket_monthlyannual_records,
    _empty_climo_dataframe,
    _iter_date_chunks,
    _merge_daily_chunks,
    _monthly_year_df,
    _normalize_climo_daily_rows,
    _yearly_df,
)

logger = logging.getLogger(__name__)

PROVIDER = "AEMET"

# Concurrencia máxima contra OpenData (ratelimit agresivo).
_MAX_CONCURRENT = 2


async def _fetch_list(
    client: httpx.AsyncClient,
    endpoint_path: str,
    api_key: str,
) -> Optional[List[Any]]:
    """Two-step OpenData → lista de records; errores no-auth → None (best-effort)."""
    try:
        payload = await _fetch_aemet_two_step(
            endpoint_path, api_key,
            client=client, step1_timeout_s=15.0, step2_timeout_s=60.0,
        )
    except ProviderError as exc:
        if exc.error_code == "provider_unauthorized":
            raise
        logger.warning("Climo AEMET %s falló: %s", endpoint_path, exc.detail)
        return None
    return payload if isinstance(payload, list) else None


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    idema: str,
    api_key: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Climatología diaria para los periodos pedidos (chunks de 150 días)."""
    station = str(idema).strip().upper()
    if not station or not periods:
        return _empty_climo_dataframe(include_extras=False)

    windows: List[Tuple[date, date]] = []
    for start, end in periods:
        windows.extend(_iter_date_chunks(start, end, max_days=150))

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _chunk_df(chunk_start: date, chunk_end: date) -> pd.DataFrame:
        fecha_ini = quote(f"{chunk_start.strftime('%Y-%m-%d')}T00:00:00UTC", safe="")
        fecha_fin = quote(f"{chunk_end.strftime('%Y-%m-%d')}T23:59:59UTC", safe="")
        endpoint = (
            f"/valores/climatologicos/diarios/datos/"
            f"fechaini/{fecha_ini}/fechafin/{fecha_fin}/estacion/{station}"
        )
        async with semaphore:
            payload = await _fetch_list(client, endpoint, api_key)
        rows = []
        for record in payload or []:
            row = _aemet_daily_record_to_row(record)
            if row:
                rows.append(row)
        return _normalize_climo_daily_rows(rows)

    chunks = await asyncio.gather(*(_chunk_df(s, e) for s, e in windows))
    return _merge_daily_chunks(list(chunks))


async def _fetch_monthlyannual_raw(
    client: httpx.AsyncClient,
    idema: str,
    api_key: str,
    year_start: int,
    year_end: int,
) -> List[Any]:
    station = str(idema).strip().upper()
    if not station:
        return []
    y0, y1 = int(min(year_start, year_end)), int(max(year_start, year_end))
    endpoint = (
        f"/valores/climatologicos/mensualesanuales/datos/"
        f"anioini/{y0:04d}/aniofin/{y1:04d}/estacion/{station}"
    )
    payload = await _fetch_list(client, endpoint, api_key)
    return payload or []


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    idema: str,
    api_key: str,
    year: int,
) -> pd.DataFrame:
    """Resumen mensual de un año (con corrección de extremos del registro anual)."""
    yy = int(year)
    payload = await _fetch_monthlyannual_raw(client, idema, api_key, yy, yy)
    return _monthly_year_df(payload, yy)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    idema: str,
    api_key: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Resumen anual plurianual (bloques de 3 años por el límite de 36 meses)."""
    valid_years = sorted({int(y) for y in years})
    if not valid_years:
        return _empty_climo_dataframe(include_extras=True)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _block(chunk_start: int) -> List[Any]:
        chunk_end = min(chunk_start + 2, max(valid_years))
        async with semaphore:
            return await _fetch_monthlyannual_raw(
                client, idema, api_key, chunk_start, chunk_end,
            )

    blocks = await asyncio.gather(*(
        _block(start) for start in range(min(valid_years), max(valid_years) + 1, 3)
    ))

    monthly_metrics: Dict[Tuple[int, int], Dict[str, Any]] = {}
    annual_metrics: Dict[int, Dict[str, Any]] = {}
    for payload in blocks:
        chunk_monthly, chunk_annual = _bucket_monthlyannual_records(payload)
        monthly_metrics.update(chunk_monthly)
        annual_metrics.update(chunk_annual)

    return _yearly_df(valid_years, monthly_metrics, annual_metrics)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    idema: str,
    api_key: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    """Selecciona y ensambla el modo solicitado por el contrato HTTP."""
    if summary_mode == "monthly":
        return await fetch_climo_daily_for_periods(client, idema, api_key, periods)
    if len(selected_years) == 1:
        return await fetch_climo_monthly_for_year(client, idema, api_key, int(selected_years[0]))
    return await fetch_climo_yearly_for_years(client, idema, api_key, selected_years)

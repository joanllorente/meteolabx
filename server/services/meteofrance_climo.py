"""
Climatología de Météo-France como servicio async puro.

Implementa la rama METEOFRANCE de ``/v1/climo/dataset`` de forma asíncrona.

- Transporte: ``httpx.AsyncClient`` inyectado contra la API DPClim
  (``commande-station/{quotidienne,mensuelle}``): se crea una commande
  y se sondea ``/commande/fichier`` hasta obtener el CSV (201). El
  polling usa ``asyncio.sleep`` sin bloquear el event loop.
- Parsing/ensamblado: ``domain/parsing/meteofrance_climo``.
- Cuota 50/min compartida con las observaciones → concurrencia acotada
  a 2 commandes simultáneas.
- Errores por commande: best-effort (un año caído no tumba el dataset);
  401/403 cortan en seco.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from domain.parsing.meteofrance_climo import (
    _aggregate_yearly_from_monthly,
    _clamp_climo_dates,
    _csv_to_df,
    _empty_climo_df,
    _extract_command_id,
    _normalize_climo_rows,
    _parse_daily_climo_row,
    _parse_monthly_climo_row,
    _to_climo_end_iso,
    _to_day_start_iso,
    filter_daily_df_to_periods,
    group_period_tuples_by_year,
)

logger = logging.getLogger(__name__)

PROVIDER = "METEOFRANCE"
CLIMO_BASE_URL = "https://public-api.meteofrance.fr/public/DPClim/v1"

# Cuota 50/min compartida con observaciones → pocas commandes a la vez.
_MAX_CONCURRENT = 2
_POLL_MAX_ATTEMPTS = 10
_POLL_WAIT_SECONDS = 0.7


def _headers(api_key: str) -> Dict[str, str]:
    return {"apikey": api_key, "Accept": "*/*"}


async def _request_command_csv(
    client: httpx.AsyncClient,
    endpoint: str,
    station_id: str,
    start_iso: str,
    end_iso: str,
    api_key: str,
) -> Optional[str]:
    """Commande → poll → CSV. Errores no-auth → None (best-effort)."""
    params = {
        "id-station": str(station_id).strip(),
        "date-deb-periode": start_iso,
        "date-fin-periode": end_iso,
    }
    try:
        response = await client.get(
            f"{CLIMO_BASE_URL}{endpoint}", params=params, headers=_headers(api_key),
        )
    except httpx.HTTPError as exc:
        logger.warning("Climo MF commande %s falló para %s: %s", endpoint, station_id, exc)
        return None

    if response.status_code in (401, 403):
        raise ProviderError(
            "provider_unauthorized", provider=PROVIDER,
            detail=f"Météo-France climatología HTTP {response.status_code}",
            status_code=401,
        )
    if response.status_code >= 400:
        logger.warning(
            "Climo MF commande %s devolvió HTTP %s para %s",
            endpoint, response.status_code, station_id,
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        return None
    command_id = _extract_command_id(payload if isinstance(payload, dict) else {})
    if not command_id:
        logger.warning("Climo MF no devolvió id de commande para %s", station_id)
        return None

    for attempt in range(_POLL_MAX_ATTEMPTS):
        try:
            poll = await client.get(
                f"{CLIMO_BASE_URL}/commande/fichier",
                params={"id-cmde": command_id},
                headers=_headers(api_key),
            )
        except httpx.HTTPError as exc:
            logger.warning("Climo MF poll commande %s falló: %s", command_id, exc)
            return None
        if poll.status_code == 201:
            return poll.text
        if poll.status_code == 204:
            if attempt < _POLL_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_POLL_WAIT_SECONDS)
                continue
        else:
            break
    logger.warning("Climo MF: fichero no disponible para la commande %s", command_id)
    return None


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Climatología diaria: una commande por año natural, recortada a los periodos."""
    if not periods:
        return _empty_climo_df()

    by_year = group_period_tuples_by_year(list(periods))
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _year_chunk(year: int, year_start: date, year_end: date) -> pd.DataFrame:
        start, end = _clamp_climo_dates(year_start, year_end)
        if start is None or end is None:
            return _empty_climo_df()
        async with semaphore:
            csv_text = await _request_command_csv(
                client, "/commande-station/quotidienne", station_id,
                _to_day_start_iso(start), _to_climo_end_iso(end), api_key,
            )
        if not csv_text:
            return _empty_climo_df()
        raw_df = _csv_to_df(csv_text)
        rows = [_parse_daily_climo_row(rec) for rec in raw_df.to_dict("records")]
        chunk = _normalize_climo_rows([row for row in rows if row])
        return filter_daily_df_to_periods(chunk, list(periods), year)

    chunks = await asyncio.gather(*(
        _year_chunk(year, ys, ye) for year, (ys, ye) in sorted(by_year.items())
    ))
    non_empty = [c for c in chunks if not c.empty]
    if not non_empty:
        return _empty_climo_df()
    return _normalize_climo_rows(pd.concat(non_empty, ignore_index=True).to_dict("records"))


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    year: int,
) -> pd.DataFrame:
    """Resumen mensual de un año (commande mensuelle)."""
    yy = int(year)
    start, end = _clamp_climo_dates(date(yy, 1, 1), date(yy, 12, 31))
    if start is None or end is None:
        return _empty_climo_df()
    csv_text = await _request_command_csv(
        client, "/commande-station/mensuelle", station_id,
        _to_day_start_iso(start), _to_day_start_iso(end), api_key,
    )
    if not csv_text:
        return _empty_climo_df()
    raw_df = _csv_to_df(csv_text)
    rows = [_parse_monthly_climo_row(rec) for rec in raw_df.to_dict("records")]
    frame = _normalize_climo_rows([row for row in rows if row])
    if frame.empty:
        return frame
    return frame[frame["date"].dt.year == yy].reset_index(drop=True)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Agregado anual desde los mensuales de cada año."""
    valid_years = sorted({int(y) for y in years})
    if not valid_years:
        return _empty_climo_df()

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _monthly(year: int) -> pd.DataFrame:
        async with semaphore:
            return await fetch_climo_monthly_for_year(client, station_id, api_key, year)

    monthly_chunks = [
        chunk for chunk in await asyncio.gather(*(_monthly(y) for y in valid_years))
        if not chunk.empty
    ]
    if not monthly_chunks:
        return _empty_climo_df()
    monthly_df = pd.concat(monthly_chunks, ignore_index=True)
    yearly = _aggregate_yearly_from_monthly(monthly_df)
    return yearly[yearly["date"].dt.year.isin(valid_years)].reset_index(drop=True)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    api_key: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    """Selecciona y ensambla el modo solicitado por el contrato HTTP."""
    if summary_mode == "monthly":
        return await fetch_climo_daily_for_periods(client, station_id, api_key, periods)
    if len(selected_years) == 1:
        return await fetch_climo_monthly_for_year(client, station_id, api_key, int(selected_years[0]))
    return await fetch_climo_yearly_for_years(client, station_id, api_key, selected_years)

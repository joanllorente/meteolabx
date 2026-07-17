"""
Climo de GeoSphere Austria (archivo klima-v2) como servicio async puro.

Implementa la rama GEOSPHERE de ``/v1/climo/dataset``.

- Transporte: ``httpx.AsyncClient`` inyectado, sin credenciales.
- Parsing/ensamblado: ``domain/parsing/geosphere_climo``.
- La estación conectable (TAWES ``11035`` o KLIMA ``K105``) se traduce a
  su serie de archivo con ``klima_station_id`` del catálogo local (las
  series klima usan IDs propios; ver build_geosphere_inventory.py).
- Semántica de errores tolerante: cada petición fallida se degrada a
  "sin filas" (DataFrame vacío → has_data=False).
- Modos idénticos a MeteoGalicia: ``monthly`` → diarios (klima-v2-1d)
  de los periodos; 1 año → mensuales (klima-v2-1m) del año; varios
  años → mensuales agregados a nivel anual.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.geosphere_climo import (
    DAILY_PARAM_MAP,
    MONTHLY_PARAM_MAP,
    aggregate_monthly_rows_to_year,
    empty_climo_df,
    extract_climo_rows,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)
from server.services.geosphere import (
    BASE_URL,
    USER_AGENT,
    _klima_api_id,
    _station_row,
)

logger = logging.getLogger(__name__)

PROVIDER = "GEOSPHERE"
DAILY_URL = f"{BASE_URL}/station/historical/klima-v2-1d"
MONTHLY_URL = f"{BASE_URL}/station/historical/klima-v2-1m"


def _resolve_klima_id(station_id: str) -> str:
    """Estación del catálogo → ID de su serie klima canónica ('' si no hay)."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    klima_id = str(row.get("klima_station_id") or "").strip()
    if klima_id:
        return klima_id
    if station_id[:1].upper() == "K" and station_id[1:].isdigit():
        return _klima_api_id(station_id, row)
    return ""


async def _fetch_rows(
    client: httpx.AsyncClient,
    url: str,
    klima_id: str,
    start: date,
    end: date,
    *,
    param_map: Dict[str, Tuple[str, float]],
) -> List[Dict[str, Any]]:
    """Una petición al archivo produce filas parseadas; errores → []."""
    try:
        response = await client.get(
            url,
            params={
                "parameters": ",".join(param_map),
                "station_ids": klima_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "Climo GeoSphere falló para %s (%s→%s): %s", klima_id, start, end, exc,
        )
        return []
    return extract_climo_rows(payload, param_map)


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Modo Mensual: datos diarios para los periodos indicados."""
    klima_id = _resolve_klima_id(station_id)
    if not klima_id:
        return empty_climo_df()
    batches = await asyncio.gather(*(
        _fetch_rows(client, DAILY_URL, klima_id, start, end, param_map=DAILY_PARAM_MAP)
        for start, end in periods
    ))
    return rows_to_climo_df([row for batch in batches for row in batch])


async def _fetch_monthly_rows(
    client: httpx.AsyncClient, klima_id: str, year: int,
) -> List[Dict[str, Any]]:
    return await _fetch_rows(
        client, MONTHLY_URL, klima_id,
        date(year, 1, 1), date(year, 12, 31),
        param_map=MONTHLY_PARAM_MAP,
    )


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    station_id: str,
    year: int,
) -> pd.DataFrame:
    """Modo Anual (1 año): datos mensuales de un año."""
    klima_id = _resolve_klima_id(station_id)
    if not klima_id:
        return empty_climo_df()
    rows = await _fetch_monthly_rows(client, klima_id, year)
    return rows_to_climo_df(rows)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    station_id: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Modo Plurianual: mensuales de cada año agregados a nivel anual."""
    klima_id = _resolve_klima_id(station_id)
    if not klima_id:
        return empty_climo_df()
    unique_years = sorted(set(int(y) for y in years))
    batches = await asyncio.gather(*(
        _fetch_monthly_rows(client, klima_id, yr) for yr in unique_years
    ))
    yearly_rows: List[Dict[str, Any]] = []
    for yr, monthly in zip(unique_years, batches):
        row = aggregate_monthly_rows_to_year(yr, monthly)
        if row is not None:
            yearly_rows.append(row)
    return yearly_rows_to_climo_df(yearly_rows)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> Optional[pd.DataFrame]:
    """
    Selecciona el dataset canónico: ``monthly`` produce diarios por
    periodos; un año produce mensuales y varios años, agregados anuales.
    """
    if summary_mode == "monthly":
        return await fetch_climo_daily_for_periods(client, station_id, periods)
    if len(selected_years) == 1:
        return await fetch_climo_monthly_for_year(client, station_id, int(selected_years[0]))
    return await fetch_climo_yearly_for_years(client, station_id, selected_years)

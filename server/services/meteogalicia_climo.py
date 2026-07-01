"""
Climo de MeteoGalicia como servicio async puro.

Implementa la rama METEOGALICIA de ``/v1/climo/dataset`` de forma
asíncrona y conserva el contrato canónico del endpoint.

- Transporte: ``httpx.AsyncClient`` inyectado (sin streamlit/requests).
- Parsing/ensamblado: ``domain/parsing/meteogalicia_climo``.
- Semántica de errores tolerante: cada petición fallida se
  degrada a "sin filas" (el caller ve DataFrame vacío → has_data=False),
  nunca rompe el dataset entero por un periodo caído.
- Concurrencia: los periodos y años se descargan en paralelo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.meteogalicia_climo import (
    DAILY_PARAM_MAP,
    MONTHLY_PARAM_MAP,
    aggregate_monthly_rows_to_year,
    extract_climo_rows,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)

logger = logging.getLogger(__name__)

PROVIDER = "METEOGALICIA"
BASE_URL = "https://servizos.meteogalicia.gal/mgrss/observacion"
DAILY_ENDPOINT = f"{BASE_URL}/datosDiariosEstacionsMeteo.action"
MONTHLY_ENDPOINT = f"{BASE_URL}/datosMensuaisEstacionsMeteo.action"


def _climo_params(station_id: str, start: date, end: date) -> Dict[str, str]:
    return {
        "idEst": str(station_id).strip(),
        "dataIni": start.strftime("%d/%m/%Y"),
        "dataFin": end.strftime("%d/%m/%Y"),
    }


async def _fetch_rows(
    client: httpx.AsyncClient,
    endpoint: str,
    station_id: str,
    start: date,
    end: date,
    *,
    list_key: str,
    param_map: Dict[str, Tuple[str, float]],
    month_start: bool = False,
) -> List[Dict[str, Any]]:
    """Una petición climo produce filas parseadas; los errores devuelven []."""
    try:
        response = await client.get(
            endpoint,
            params=_climo_params(station_id, start, end),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "Climo MeteoGalicia %s falló para %s (%s→%s): %s",
            list_key, station_id, start, end, exc,
        )
        return []
    return extract_climo_rows(
        payload, station_id,
        list_key=list_key, param_map=param_map, month_start=month_start,
    )


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Modo Mensual: datos diarios para los periodos indicados."""
    batches = await asyncio.gather(*(
        _fetch_rows(
            client, DAILY_ENDPOINT, station_id, start, end,
            list_key="listDatosDiarios", param_map=DAILY_PARAM_MAP,
        )
        for start, end in periods
    ))
    rows: List[Dict[str, Any]] = [row for batch in batches for row in batch]
    return rows_to_climo_df(rows)


async def _fetch_monthly_rows(
    client: httpx.AsyncClient, station_id: str, year: int,
) -> List[Dict[str, Any]]:
    return await _fetch_rows(
        client, MONTHLY_ENDPOINT, station_id,
        date(year, 1, 1), date(year, 12, 31),
        list_key="listDatosMensuais", param_map=MONTHLY_PARAM_MAP,
        month_start=True,
    )


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    station_id: str,
    year: int,
) -> pd.DataFrame:
    """Modo Anual (1 año): datos mensuales de un año."""
    rows = await _fetch_monthly_rows(client, station_id, year)
    return rows_to_climo_df(rows)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    station_id: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Modo Plurianual: mensuales de cada año agregados a nivel anual."""
    unique_years = sorted(set(int(y) for y in years))
    batches = await asyncio.gather(*(
        _fetch_monthly_rows(client, station_id, yr) for yr in unique_years
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

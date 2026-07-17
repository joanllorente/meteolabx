"""
Climo de SMHI (corrected-archive) como servicio async puro.

Implementa la rama SMHI de ``/v1/climo/dataset``.

- Transporte: ``httpx.AsyncClient`` inyectado, sin credenciales.
- Parsing/ensamblado: ``domain/parsing/smhi_climo``.
- Fuentes por parámetro: ``corrected-archive`` (CSV, serie completa con
  control de calidad pero ~3 meses de decalaje) + ``latest-months``
  (JSON) para cubrir los meses recientes. Para una misma fecha gana la
  fuente más fresca.
- Semántica de errores tolerante: cada petición fallida se degrada a
  "sin filas" (DataFrame vacío → has_data=False).
- Modos idénticos a MeteoGalicia: ``monthly`` → diarios (params 2/19/
  20/5) de los periodos; 1 año → mensuales (22/23); varios años →
  mensuales agregados a nivel anual. SMHI no publica racha diaria en
  el archivo → ``gust_max`` queda vacío.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.smhi_climo import (
    DAILY_PARAM_FIELDS,
    MONTHLY_PARAM_FIELDS,
    aggregate_monthly_rows_to_year,
    merge_field_rows,
    parse_archive_csv,
    parse_recent_json,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)
from server.services.smhi import BASE_URL, USER_AGENT

logger = logging.getLogger(__name__)

PROVIDER = "SMHI"
HEADERS = {"User-Agent": USER_AGENT}


async def _fetch_param_rows(
    client: httpx.AsyncClient,
    station_id: str,
    parameter: str,
    field: str,
    *,
    include_recent: bool,
) -> List[Dict[str, Any]]:
    """Filas de un parámetro: archivo corregido + meses recientes."""
    base = f"{BASE_URL}/parameter/{parameter}/station/{station_id}/period"
    rows: List[List[Dict[str, Any]]] = []
    try:
        response = await client.get(
            f"{base}/corrected-archive/data.csv", headers=HEADERS, timeout=90.0,
        )
        if response.status_code == 200:
            rows.append(parse_archive_csv(response.text, field))
    except Exception as exc:
        logger.warning(
            "Climo SMHI: archivo del parámetro %s falló para %s: %s",
            parameter, station_id, exc,
        )
    if include_recent:
        try:
            response = await client.get(
                f"{base}/latest-months/data.json",
                headers={**HEADERS, "Accept": "application/json"}, timeout=30.0,
            )
            if response.status_code == 200:
                rows.append(parse_recent_json(response.json(), field))
        except Exception as exc:
            logger.warning(
                "Climo SMHI: latest-months del parámetro %s falló para %s: %s",
                parameter, station_id, exc,
            )
    return merge_field_rows(rows)


def _recent_window_touched(periods: Sequence[Tuple[date, date]]) -> bool:
    """¿Algún periodo pedido entra en los ~4 meses que cubre latest-months?"""
    if not periods:
        return False
    cutoff = date.today().toordinal() - 130
    return any(end.toordinal() >= cutoff for _start, end in periods)


async def _fetch_fields(
    client: httpx.AsyncClient,
    station_id: str,
    param_fields: Dict[str, str],
    *,
    include_recent: bool,
) -> List[Dict[str, Any]]:
    batches = await asyncio.gather(*(
        _fetch_param_rows(
            client, station_id, parameter, field, include_recent=include_recent,
        )
        for parameter, field in param_fields.items()
    ))
    return merge_field_rows(list(batches))


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Modo Mensual: datos diarios para los periodos indicados."""
    station_id = str(station_id).strip()
    if not station_id or not periods:
        return rows_to_climo_df([])
    rows = await _fetch_fields(
        client, station_id, DAILY_PARAM_FIELDS,
        include_recent=_recent_window_touched(periods),
    )
    wanted = [
        row for row in rows
        if any(
            start.isoformat() <= row["date"] <= end.isoformat()
            for start, end in periods
        )
    ]
    return rows_to_climo_df(wanted)


async def _fetch_monthly_rows(
    client: httpx.AsyncClient, station_id: str, year: int,
) -> List[Dict[str, Any]]:
    include_recent = year >= date.today().year - 1
    rows = await _fetch_fields(
        client, station_id, MONTHLY_PARAM_FIELDS, include_recent=include_recent,
    )
    prefix = f"{int(year):04d}-"
    return [row for row in rows if row["date"].startswith(prefix)]


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    station_id: str,
    year: int,
) -> pd.DataFrame:
    """Modo Anual (1 año): datos mensuales de un año."""
    rows = await _fetch_monthly_rows(client, str(station_id).strip(), int(year))
    return rows_to_climo_df(rows)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    station_id: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Modo Plurianual: mensuales de cada año agregados a nivel anual.

    El archivo llega entero en una descarga, así que se filtra por año
    en local (una pasada de red, no una por año).
    """
    station_id = str(station_id).strip()
    unique_years = sorted(set(int(y) for y in years))
    if not station_id or not unique_years:
        return rows_to_climo_df([])
    include_recent = max(unique_years) >= date.today().year - 1
    rows = await _fetch_fields(
        client, station_id, MONTHLY_PARAM_FIELDS, include_recent=include_recent,
    )
    yearly_rows: List[Dict[str, Any]] = []
    for year in unique_years:
        prefix = f"{year:04d}-"
        monthly = [row for row in rows if row["date"].startswith(prefix)]
        row = aggregate_monthly_rows_to_year(year, monthly)
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

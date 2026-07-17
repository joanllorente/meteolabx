"""
Climo de ECCC (climate-daily / climate-monthly) como servicio async puro.

Implementa la rama ECCC de ``/v1/climo/dataset``.

- Transporte: ``httpx.AsyncClient`` inyectado, sin credenciales.
- La estación conectable (SWOB ``msc_id`` o red CLIMATE) se traduce a su
  ``climate_identifier`` del catálogo local.
- Fuentes: ``climate-daily`` (diarios oficiales con extremos y racha,
  desde 1840) y ``climate-monthly`` (agregados mensuales). Ambos por
  ``CLIMATE_IDENTIFIER`` + rango ``LOCAL_DATE`` vía ``datetime=``.
- Modos idénticos a MeteoGalicia: ``monthly`` → diarios de los periodos;
  1 año → mensuales del año; varios años → mensuales agregados a nivel
  anual. En mensual, MIN/MAX_TEMPERATURE son los extremos ABSOLUTOS del
  mes → temp_abs_min/max (no hay media de máximas/mínimas mensual).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.meteogalicia_climo import (  # assemblers genéricos
    aggregate_monthly_rows_to_year,
    empty_climo_df,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)
from server.services.eccc import BASE_URL, USER_AGENT, _station_row

logger = logging.getLogger(__name__)

PROVIDER = "ECCC"
DAILY_URL = f"{BASE_URL}/collections/climate-daily/items"
MONTHLY_URL = f"{BASE_URL}/collections/climate-monthly/items"

DAILY_FIELD_MAP = {
    "MEAN_TEMPERATURE": "temp_mean",
    "MAX_TEMPERATURE": "temp_max",
    "MIN_TEMPERATURE": "temp_min",
    "TOTAL_PRECIPITATION": "precip_total",
    "SPEED_MAX_GUST": "gust_max",  # ya en km/h
}
MONTHLY_FIELD_MAP = {
    "MEAN_TEMPERATURE": "temp_mean",
    "MAX_TEMPERATURE": "temp_abs_max",
    "MIN_TEMPERATURE": "temp_abs_min",
    "TOTAL_PRECIPITATION": "precip_total",
}


def _resolve_climate_id(station_id: str) -> str:
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    return str(row.get("climate_identifier") or "").strip() or (
        station_id if str(row.get("network") or "").upper() == "CLIMATE" else ""
    )


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _rows_from_features(payload: Any, field_map: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for feature in (payload.get("features") or []) if isinstance(payload, dict) else []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        date_text = str(props.get("LOCAL_DATE") or "").strip()[:10]
        if len(date_text) == 7:  # mensual: "2026-06"
            date_text = f"{date_text}-01"
        if len(date_text) != 10:
            continue
        try:
            epoch = int(
                datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc).timestamp()
            )
        except ValueError:
            continue
        row: Dict[str, Any] = {"date": date_text, "epoch": epoch}
        has_value = False
        for source, field in field_map.items():
            value = _safe_float(props.get(source))
            if value == value:
                if field.startswith("precip") and value < 0.0:
                    value = 0.0
                row[field] = value
                has_value = True
            else:
                row[field] = float("nan")
        if has_value:
            rows.append(row)
    return rows


async def _fetch_range(
    client: httpx.AsyncClient,
    url: str,
    climate_id: str,
    start: date,
    end: date,
    *,
    field_map: Dict[str, str],
    limit: int = 10000,
) -> List[Dict[str, Any]]:
    """Un rango de fechas produce filas parseadas; los errores devuelven []."""
    try:
        response = await client.get(
            url,
            params={
                "f": "json",
                "CLIMATE_IDENTIFIER": climate_id,
                "datetime": f"{start.isoformat()}/{end.isoformat()}",
                "limit": limit,
            },
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=90.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "Climo ECCC falló para %s (%s→%s): %s", climate_id, start, end, exc,
        )
        return []
    return _rows_from_features(payload, field_map)


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Modo Mensual: datos diarios para los periodos indicados."""
    climate_id = _resolve_climate_id(station_id)
    if not climate_id or not periods:
        return empty_climo_df()
    batches = await asyncio.gather(*(
        _fetch_range(client, DAILY_URL, climate_id, start, end, field_map=DAILY_FIELD_MAP)
        for start, end in periods
    ))
    return rows_to_climo_df([row for batch in batches for row in batch])


async def fetch_climo_monthly_for_year(
    client: httpx.AsyncClient,
    station_id: str,
    year: int,
) -> pd.DataFrame:
    """Modo Anual (1 año): datos mensuales de un año."""
    climate_id = _resolve_climate_id(station_id)
    if not climate_id:
        return empty_climo_df()
    rows = await _fetch_range(
        client, MONTHLY_URL, climate_id,
        date(int(year), 1, 1), date(int(year), 12, 31),
        field_map=MONTHLY_FIELD_MAP,
    )
    return rows_to_climo_df(rows)


async def fetch_climo_yearly_for_years(
    client: httpx.AsyncClient,
    station_id: str,
    years: Sequence[int],
) -> pd.DataFrame:
    """Modo Plurianual: mensuales de cada año agregados a nivel anual."""
    climate_id = _resolve_climate_id(station_id)
    unique_years = sorted(set(int(y) for y in years))
    if not climate_id or not unique_years:
        return empty_climo_df()
    rows = await _fetch_range(
        client, MONTHLY_URL, climate_id,
        date(unique_years[0], 1, 1), date(unique_years[-1], 12, 31),
        field_map=MONTHLY_FIELD_MAP,
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

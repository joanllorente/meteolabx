"""
Histórico de DMI como servicio async puro.

Implementa la rama DMI de ``/v1/climo/dataset`` con la API de clima
(``opendataapi.dmi.dk/v2/climateData/collections/stationValue``): valores
DIARIOS que DMI calcula por estación (media, máxima y mínima, lluvia
acumulada, viento medio y racha de 3 s, dirección media), recalculados cada
hora para el día en curso. Empieza en 2011.

- Un parámetro por consulta, como metObs: cada año de cada parámetro es una
  consulta; los años cerrados se cachean largo.
- El día es el que declara DMI en ``from`` (local de la estación: 00-24 h en
  Dinamarca; algunas máximas de Groenlandia van de 06 a 06 UTC).
- Viento m/s → km/h.
- Devuelve diarios en todos los modos, como IEM: el resumen anual los agrega.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.periods import merge_date_periods
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.schemas.errors import ProviderError
from server.services import dmi
from server.services.climo_cache import get_or_fetch_climo_block

logger = logging.getLogger(__name__)

PROVIDER = "DMI"
CLIMATE_URL = "https://opendataapi.dmi.dk/v2/climateData/collections/stationValue/items"
FIRST_YEAR = 2011

# Parámetro diario de DMI → (columna del esquema, factor).
DAILY_PARAMETERS: Dict[str, Tuple[str, float]] = {
    "mean_temp": ("temp_mean", 1.0),
    "max_temp_w_date": ("temp_max", 1.0),
    "min_temp": ("temp_min", 1.0),
    "acc_precip": ("precip_total", 1.0),
    "mean_wind_speed": ("wind_mean", 3.6),
    "max_wind_speed_3sec": ("gust_max", 3.6),
    "mean_wind_dir": ("wind_dir_mean", 1.0),
}


def parse_station_values(payload: Any) -> Dict[str, float]:
    """GeoJSON de stationValue → {día ISO: valor}."""
    out: Dict[str, float] = {}
    if not isinstance(payload, dict):
        return out
    for feature in payload.get("features") or []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict) or props.get("validity") is False:
            continue
        try:
            value = float(props.get("value"))
        except (TypeError, ValueError):
            continue
        day = str(props.get("from") or "")[:10]
        if len(day) == 10 and value == value:
            out[day] = value
    return out


async def _fetch_year(
    client: httpx.AsyncClient, station_id: str, parameter: str, year: int,
) -> Dict[str, float]:
    params = {
        "stationId": station_id,
        "parameterId": parameter,
        "timeResolution": "day",
        # Holgura de un día por los husos: se filtra por año al montar.
        "datetime": f"{year - 1}-12-31T00:00:00Z/{year + 1}-01-01T23:59:59Z",
        "limit": 1000,
    }
    for attempt in range(len(dmi.RETRY_DELAYS_S) + 1):
        async with dmi._semaphore():
            response = await client.get(
                CLIMATE_URL, params=params, headers={"User-Agent": dmi.USER_AGENT}, timeout=60.0,
            )
        if response.status_code != 429 or attempt == len(dmi.RETRY_DELAYS_S):
            break
        await asyncio.sleep(dmi.RETRY_DELAYS_S[attempt])
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"DMI climateData HTTP {response.status_code}",
            status_code=502,
        )
    return {
        day: value for day, value in parse_station_values(response.json()).items()
        if day.startswith(f"{year}-")
    }


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    code = dmi.normalize_station_id(station_id)
    today = today_date or datetime.now(timezone.utc).date()
    clipped = merge_date_periods(clip_period_tuples_to_today(list(periods), today_date=today))
    clipped = [(max(start, date(FIRST_YEAR, 1, 1)), end) for start, end in clipped if end.year >= FIRST_YEAR]
    if not code or not clipped:
        return pd.DataFrame(columns=DAILY_SCHEMA)

    years = sorted({year for start, end in clipped for year in range(start.year, end.year + 1)})

    async def _block(parameter: str, year: int) -> Tuple[str, Dict[str, float]]:
        async def _fetch() -> Dict[str, float]:
            return await _fetch_year(client, code, parameter, year)

        try:
            values = await get_or_fetch_climo_block(
                provider=PROVIDER,
                kind=f"stationValue:{parameter}:{year}",
                station_id=code,
                credential="public",
                client=client,
                end_date=date(year, 12, 31),
                fetcher=_fetch,
            )
        except Exception as exc:
            logger.warning("Climo DMI: %s %s falló para %s: %s", parameter, year, code, exc)
            values = {}
        return parameter, values or {}

    results = await asyncio.gather(*(
        _block(parameter, year) for parameter in DAILY_PARAMETERS for year in years
    ))
    days: Dict[str, Dict[str, float]] = {}
    for parameter, values in results:
        column, factor = DAILY_PARAMETERS[parameter]
        for day, value in values.items():
            days.setdefault(day, {})[column] = value * factor

    rows: List[Dict[str, Any]] = []
    for day_text in sorted(days):
        day = date.fromisoformat(day_text)
        if not any(start <= day <= end for start, end in clipped):
            continue
        values = days[day_text]
        rows.append({
            "date": pd.Timestamp(day),
            "epoch": float(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()),
            **{column: values.get(column, math.nan) for column in DAILY_SCHEMA if column not in ("date", "epoch")},
        })
    if not rows:
        return pd.DataFrame(columns=DAILY_SCHEMA)
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    for column in [c for c in DAILY_SCHEMA if c != "date"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[DAILY_SCHEMA].reset_index(drop=True)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    return await fetch_climo_daily_for_periods(client, station_id, periods)

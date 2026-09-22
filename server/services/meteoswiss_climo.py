"""
Histórico de MeteoSwiss como servicio async puro.

Implementa la rama METEOSWISS de ``/v1/climo/dataset`` con los ficheros
DIARIOS de cada estación:

- ``d_historical``: desde el inicio de la serie hasta el 31 de diciembre
  pasado (se regenera una vez al año, hacia febrero). Pesa varios MB en las
  estaciones con serie larga (Adelboden empieza en 1901), así que se parsea a
  arrays compactos y se cachea unas horas.
- ``d_recent``: del 1 de enero a ayer, unos KB. Mientras el histórico del año
  pasado no está regenerado, sus días siguen aquí: los dos se combinan y
  manda ``recent``.

Columnas: media, máxima y mínima (``tre200d*``), viento medio y racha máxima
en km/h (``fu3010d0``/``d1``), dirección media y lluvia. La lluvia es la del
día UTC (``rka150d0``) donde existe; los pluviómetros manuales solo tienen la
de 6 a 6 UTC (``rre150d0``), anotada en el día en que empieza.

Los días de MeteoSwiss son UTC, no locales (una o dos horas de diferencia).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import httpx
import numpy as np
import pandas as pd

from domain.parsing.periods import merge_date_periods
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.services import meteoswiss
from server.services.climo_cache import get_or_fetch_climo_block

logger = logging.getLogger(__name__)

PROVIDER = "METEOSWISS"
HISTORICAL_TTL_S = 6 * 3600
RECENT_TTL_S = 3600

# Columna del esquema → columnas del CSV por preferencia.
DAILY_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "temp_mean": ("tre200d0",),
    "temp_max": ("tre200dx",),
    "temp_min": ("tre200dn",),
    "wind_mean": ("fu3010d0",),
    "gust_max": ("fu3010d1",),
    "wind_dir_mean": ("dkl010d0",),
    "precip_total": ("rka150d0", "rre150d0"),
}
FIELDS = tuple(DAILY_COLUMNS)

# (ordinales de día, valores float32 [días × campos]).
Block = Tuple[np.ndarray, np.ndarray]


def parse_daily(text: str) -> Block:
    """CSV diario → arrays compactos."""
    lines = text.splitlines()
    if len(lines) < 2:
        return np.empty(0, dtype=np.int32), np.empty((0, len(FIELDS)), dtype=np.float32)
    header = [name.strip() for name in lines[0].split(";")]
    indexes: List[Tuple[int, ...]] = [
        tuple(header.index(name) for name in names if name in header) for names in DAILY_COLUMNS.values()
    ]
    days: List[int] = []
    values: List[List[float]] = []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        try:
            day = datetime.strptime(parts[1].strip()[:10], "%d.%m.%Y").date()
        except ValueError:
            continue
        row = []
        for candidates in indexes:
            value = math.nan
            for index in candidates:
                raw = parts[index].strip() if index < len(parts) else ""
                if raw and raw != "-":
                    try:
                        value = float(raw)
                        break
                    except ValueError:
                        continue
            row.append(value)
        if any(not math.isnan(value) for value in row):
            days.append(day.toordinal())
            values.append(row)
    return np.asarray(days, dtype=np.int32), np.asarray(values, dtype=np.float32).reshape(-1, len(FIELDS))


async def _block(
    client: httpx.AsyncClient, collection: str, code: str, span: str, *, today: date,
) -> Block:
    async def _fetch() -> Block:
        text = await meteoswiss.get_text(
            client, meteoswiss.file_url(collection, code, f"d_{span}"), timeout_s=120.0,
        )
        return await asyncio.to_thread(parse_daily, text)

    try:
        block = await get_or_fetch_climo_block(
            provider=PROVIDER,
            kind=f"d_{span}",
            station_id=code,
            credential="public",
            client=client,
            end_date=today,
            fetcher=_fetch,
            ttl_s=HISTORICAL_TTL_S if span == "historical" else RECENT_TTL_S,
        )
    except Exception as exc:
        logger.warning("Climo MeteoSwiss: d_%s falló para %s: %s", span, code, exc)
        block = None
    if block is None:
        return np.empty(0, dtype=np.int32), np.empty((0, len(FIELDS)), dtype=np.float32)
    return block


async def latest_daily_precip(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    today_date: Optional[date] = None,
) -> Optional[Tuple[date, float]]:
    """Última lluvia diaria publicada: ``(día, mm)`` o ``None``.

    Es lo único que publican los pluviómetros manuales, y la ficha de
    Observación los dejaba en blanco: el dato estaba en la pestaña de al lado.
    Sale del mismo ``d_recent`` que el Histórico, con su misma caché. En los
    primeros días de enero ``recent`` aún puede no traer nada del año, y se
    mira el histórico.

    El día es el de INICIO de la ventana de 6 a 6 UTC: el 19 es la lluvia del
    19 a las 6 al 20 a las 6, que el observador lee la mañana del 20 y MeteoSwiss
    publica el 21.
    """
    code = meteoswiss.normalize_station_id(station_id)
    if not code:
        return None
    today = today_date or datetime.now(timezone.utc).date()
    collection = str(meteoswiss._station_row(code).get("collection") or meteoswiss.DEFAULT_COLLECTION)
    columna = FIELDS.index("precip_total")
    for span in ("recent", "historical"):
        days, values = await _block(client, collection, code, span, today=today)
        for index in range(len(days) - 1, -1, -1):
            value = float(values[index][columna])
            if not math.isnan(value):
                return date.fromordinal(int(days[index])), value
    return None


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    code = meteoswiss.normalize_station_id(station_id)
    today = today_date or datetime.now(timezone.utc).date()
    clipped = merge_date_periods(clip_period_tuples_to_today(list(periods), today_date=today))
    if not code or not clipped:
        return pd.DataFrame(columns=DAILY_SCHEMA)
    row = meteoswiss._station_row(code)
    collection = str(row.get("collection") or meteoswiss.DEFAULT_COLLECTION)

    spans = []
    if any(start < date(today.year, 1, 1) for start, _end in clipped):
        spans.append("historical")
    if any(end >= date(today.year - 1, 1, 1) for _start, end in clipped):
        spans.append("recent")
    blocks = await asyncio.gather(*(_block(client, collection, code, span, today=today) for span in spans))

    merged: Dict[int, np.ndarray] = {}
    for days, values in blocks:  # historical primero: recent lo pisa
        lo_hi = [(start.toordinal(), end.toordinal()) for start, end in clipped]
        mask = np.zeros(len(days), dtype=bool)
        for lo, hi in lo_hi:
            mask |= (days >= lo) & (days <= hi)
        for ordinal, value in zip(days[mask].tolist(), values[mask]):
            merged[ordinal] = value
    if not merged:
        return pd.DataFrame(columns=DAILY_SCHEMA)

    rows = []
    for ordinal in sorted(merged):
        day = date.fromordinal(ordinal)
        values = merged[ordinal]
        record = {
            "date": pd.Timestamp(day),
            "epoch": float(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()),
        }
        for field, value in zip(FIELDS, values.tolist()):
            record[field] = value if not math.isnan(value) else math.nan
        rows.append(record)
    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame:
            frame[column] = math.nan
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    for column in [c for c in DAILY_SCHEMA if c != "date"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").round(2)
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

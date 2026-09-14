"""
Histórico de LHMT (Lituania) como servicio async puro.

Implementa la rama LHMT de ``/v1/climo/dataset``.

La API de Meteo.lt solo sirve un día por petición, así que un año serían 365
llamadas contra un tope de 180 por minuto. El archivo se pide, en cambio, al
portal de datos abiertos de Lituania (data.gov.lt, API Spinta), donde LHMT
publica las mismas observaciones horarias de su red automática desde 2013 y
admite filtrar por estación y rango de fechas: un año entero de una estación
es UNA petición de ~8.800 filas.

- Transporte: ``httpx.AsyncClient`` inyectado, sin credenciales.
- El portal va con ~1 día de retraso y tiene días sueltos sin datos: los
  últimos días y los huecos se completan con la API de Meteo.lt (un día UTC
  por petición, días cerrados en caché) y, donde coinciden, gana ella.
- Los diarios se derivan de las muestras horarias en el día local
  (Europe/Vilnius): media, máxima y mínima de las instantáneas horarias
  (extremos ligeramente recortados), viento medio, dirección predominante,
  racha máxima y lluvia sumada. La lluvia de cada muestra es la de la hora
  PRECEDENTE, así que cuenta para el día en que empieza esa hora.
- Devuelve diarios en todos los modos, como IEM: el resumen anual los agrega.
- Semántica tolerante: un bloque que falla se registra y se omite.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.periods import merge_date_periods
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.services import lhmt
from server.services.climo_cache import get_or_fetch_climo_block

logger = logging.getLogger(__name__)

PROVIDER = "LHMT"
ARCHIVE_URL = "https://get.data.gov.lt/datasets/gov/lhmt/stebejimai/Matavimas"
ARCHIVE_FIELDS = (
    "stebejimo_laikas", "oro_temp", "vejo_greitis", "vejo_gusis",
    "vejo_kryptis", "kritutliu_kiekis",
)
# Un año horario son como mucho 8.784 filas: cabe en una página.
ARCHIVE_PAGE_LIMIT = 10_000
# El portal tiene un cortafuegos que corta ráfagas de peticiones seguidas.
ARCHIVE_CONCURRENCY = 2
# El archivo tiene huecos (en 2025 faltan ~35 días por estación). Un día local
# con menos muestras que esto se pide a la API, que guarda 10 años. Cada día
# local son dos días UTC: el tope deja una consulta en ~90 peticiones, lejos
# de las 180 por minuto aunque el archivo esté caído entero, y la pausa entre
# peticiones evita la ráfaga.
MIN_HOURS_PER_DAY = 20
API_ARCHIVE_YEARS = 10
MAX_GAP_DAYS = 45
API_REQUEST_PAUSE_S = 0.25

# Nombre de la columna del portal → clave de la muestra (mismas que la API).
_ARCHIVE_TO_SAMPLE = {
    "oro_temp": "airTemperature",
    "vejo_greitis": "windSpeed",
    "vejo_gusis": "windGust",
    "vejo_kryptis": "windDirection",
    "kritutliu_kiekis": "precipitation",
}


def _num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number


def _empty_daily_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_SCHEMA)


def _predominant_direction_degrees(values: Sequence[float]) -> float:
    valid = [v for v in values if not math.isnan(v)]
    if not valid:
        return math.nan
    counts: Dict[int, int] = {}
    for value in valid:
        sector = int((((value % 360.0) + 11.25) // 22.5)) % 16
        counts[sector] = counts.get(sector, 0) + 1
    return float(max(counts, key=counts.get) * 22.5)


def archive_samples(payload: Any) -> List[Dict[str, Any]]:
    """Filas del portal → muestras ``{epoch, airTemperature, ...}``."""
    if not isinstance(payload, dict):
        return []
    samples: List[Dict[str, Any]] = []
    for row in payload.get("_data") or []:
        if not isinstance(row, dict):
            continue
        epoch = lhmt.parse_observation_time(row.get("stebejimo_laikas"))
        if epoch is None:
            continue
        sample: Dict[str, Any] = {"epoch": epoch}
        for column, key in _ARCHIVE_TO_SAMPLE.items():
            sample[key] = row.get(column)
        samples.append(sample)
    return samples


def aggregate_daily(
    samples: Iterable[Dict[str, Any]],
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Muestras horarias → diarios locales dentro de ``periods``."""
    by_epoch = {int(sample["epoch"]): sample for sample in samples}
    buckets: Dict[date, Dict[str, list]] = {}

    def _bucket(day: date) -> Dict[str, list]:
        return buckets.setdefault(day, {
            "epoch": [], "temp": [], "wind": [], "wind_dir": [], "gust": [], "precip": [],
        })

    for epoch in sorted(by_epoch):
        sample = by_epoch[epoch]
        local = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(lhmt.STATION_TZ)
        bucket = _bucket(local.date())
        bucket["epoch"].append(float(epoch))
        temp = _num(sample.get("airTemperature"))
        wind = _num(sample.get("windSpeed"))
        gust = _num(sample.get("windGust"))
        direction = _num(sample.get("windDirection"))
        if not math.isnan(temp):
            bucket["temp"].append(temp)
        if not math.isnan(wind):
            bucket["wind"].append(wind * 3.6)
        if not math.isnan(gust):
            bucket["gust"].append((gust * 3.6, direction))
        if not math.isnan(direction):
            bucket["wind_dir"].append(direction)
        precip = _num(sample.get("precipitation"))
        if not math.isnan(precip):
            hour_start = datetime.fromtimestamp(
                epoch - 3600, tz=timezone.utc,
            ).astimezone(lhmt.STATION_TZ)
            _bucket(hour_start.date())["precip"].append(max(0.0, precip))

    rows: List[Dict[str, Any]] = []
    for day, values in sorted(buckets.items()):
        if not any(start <= day <= end for start, end in periods):
            continue
        if not (values["temp"] or values["wind"] or values["gust"] or values["precip"]):
            continue
        temps = values["temp"]
        gusts = values["gust"]
        strongest = max(gusts, key=lambda item: item[0]) if gusts else (math.nan, math.nan)
        rows.append({
            "date": pd.Timestamp(day),
            "epoch": max(values["epoch"]) if values["epoch"] else math.nan,
            "temp_mean": sum(temps) / len(temps) if temps else math.nan,
            "temp_max": max(temps) if temps else math.nan,
            "temp_min": min(temps) if temps else math.nan,
            "wind_mean": sum(values["wind"]) / len(values["wind"]) if values["wind"] else math.nan,
            "wind_dir_mean": _predominant_direction_degrees(values["wind_dir"]),
            "gust_max": strongest[0],
            "gust_dir_max": strongest[1],
            "precip_total": sum(values["precip"]) if values["precip"] else math.nan,
            "precip_rate_max": math.nan,
        })
    if not rows:
        return _empty_daily_dataframe()
    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = math.nan
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    for column in [c for c in DAILY_SCHEMA if c != "date"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[DAILY_SCHEMA].sort_values("date").reset_index(drop=True)


def local_day_gaps(
    samples: Iterable[Dict[str, Any]],
    periods: Sequence[Tuple[date, date]],
    *,
    min_hours: int = MIN_HOURS_PER_DAY,
) -> List[date]:
    """Días locales de ``periods`` con menos de ``min_hours`` muestras."""
    counts: Dict[date, int] = {}
    for sample in samples:
        local = datetime.fromtimestamp(int(sample["epoch"]), tz=timezone.utc).astimezone(lhmt.STATION_TZ)
        counts[local.date()] = counts.get(local.date(), 0) + 1
    gaps: List[date] = []
    for start, end in periods:
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if counts.get(day, 0) < min_hours:
                gaps.append(day)
    return gaps


def utc_days_for_local_days(days: Iterable[date]) -> List[date]:
    """Un día local de Vilnius (UTC+2/+3) empieza la víspera en UTC y su
    última lluvia llega en la muestra de las 00:00 del día siguiente."""
    utc_days = set()
    for day in days:
        begin, finish = _utc_window(day, day)
        cursor = begin.date()
        while cursor <= finish.date():
            utc_days.add(cursor)
            cursor += timedelta(days=1)
    return sorted(utc_days)


def split_by_year(periods: Sequence[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Trocea periodos en bloques de como mucho un año natural: cada bloque es
    una petición que cabe en una página y se cachea por separado."""
    chunks: List[Tuple[date, date]] = []
    for start, end in periods:
        cursor = start
        while cursor <= end:
            chunk_end = min(end, date(cursor.year, 12, 31))
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
    return chunks


def _utc_window(start: date, end: date) -> Tuple[datetime, datetime]:
    """Días locales [start, end] → ventana UTC. Se añade una hora al final para
    la lluvia de la última hora del día, que llega en la muestra de las 00:00."""
    begin = datetime.combine(start, time(0), tzinfo=lhmt.STATION_TZ).astimezone(timezone.utc)
    finish = datetime.combine(
        end + timedelta(days=1), time(1), tzinfo=lhmt.STATION_TZ,
    ).astimezone(timezone.utc)
    return begin, finish


def archive_query(station_id: str, start: date, end: date) -> str:
    begin, finish = _utc_window(start, end)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return (
        f'?stoties_kodas="{lhmt.normalize_station_id(station_id)}"'
        f'&stebejimo_laikas>="{begin.strftime(fmt)}"'
        f'&stebejimo_laikas<"{finish.strftime(fmt)}"'
        f"&select({','.join(ARCHIVE_FIELDS)})"
        f"&sort(stebejimo_laikas)&limit({ARCHIVE_PAGE_LIMIT})"
    )


async def _fetch_archive_chunk(
    client: httpx.AsyncClient, station_id: str, start: date, end: date,
) -> List[Dict[str, Any]]:
    response = await client.get(
        ARCHIVE_URL + archive_query(station_id, start, end),
        headers={"Accept": "application/json", "User-Agent": lhmt.USER_AGENT},
        timeout=90.0,
    )
    response.raise_for_status()
    samples = archive_samples(response.json())
    if len(samples) >= ARCHIVE_PAGE_LIMIT:
        logger.warning(
            "Climo LHMT: bloque %s→%s de %s llenó la página; puede faltar el final",
            start, end, station_id,
        )
    return samples


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    station_id = lhmt.normalize_station_id(station_id)
    today = today_date or datetime.now(lhmt.STATION_TZ).date()
    clipped = merge_date_periods(clip_period_tuples_to_today(list(periods), today_date=today))
    if not station_id or not clipped:
        return _empty_daily_dataframe()

    semaphore = asyncio.Semaphore(ARCHIVE_CONCURRENCY)

    async def _archive(start: date, end: date) -> List[Dict[str, Any]]:
        async with semaphore:
            try:
                samples = await get_or_fetch_climo_block(
                    provider=PROVIDER,
                    kind=f"archive:{start.isoformat()}:{end.isoformat()}",
                    station_id=station_id,
                    credential="public",
                    client=client,
                    end_date=end,
                    fetcher=lambda: _fetch_archive_chunk(client, station_id, start, end),
                )
                return samples or []
            except Exception as exc:
                logger.warning(
                    "Climo LHMT: archivo %s→%s falló para %s: %s",
                    start, end, station_id, exc,
                )
                return []

    now_utc = datetime.now(timezone.utc)
    api_semaphore = asyncio.Semaphore(lhmt.MAX_CONCURRENCY)

    async def _recent(day: date) -> List[Dict[str, Any]]:
        async with api_semaphore:
            try:
                return await lhmt._fetch_day_observations(
                    station_id, day, client, timeout_s=20.0, now_utc=now_utc,
                    pause_s=API_REQUEST_PAUSE_S if len(api_days) > lhmt.MAX_CONCURRENCY else 0.0,
                )
            except Exception as exc:
                logger.warning("Climo LHMT: día %s falló para %s: %s", day, station_id, exc)
                return []

    archive_batches = await asyncio.gather(
        *(_archive(start, end) for start, end in split_by_year(clipped))
    )
    archived = [sample for batch in archive_batches for sample in batch]

    # Huecos del archivo (incluidos los últimos días, que aún no ha publicado)
    # dentro de lo que guarda la API y desde que la estación tiene datos: un
    # año anterior a su instalación no son huecos que rellenar.
    api_floor = today - timedelta(days=365 * API_ARCHIVE_YEARS)
    station_start = lhmt.parse_observation_time(
        lhmt._station_row(station_id).get("data_start_utc"),
    )
    if station_start is not None:
        api_floor = max(api_floor, datetime.fromtimestamp(station_start, tz=timezone.utc).date())
    gaps = [day for day in local_day_gaps(archived, clipped) if day >= api_floor]
    if len(gaps) > MAX_GAP_DAYS:
        logger.warning(
            "Climo LHMT: %d días sin archivo para %s; se completan solo los %d más recientes",
            len(gaps), station_id, MAX_GAP_DAYS,
        )
        gaps = gaps[-MAX_GAP_DAYS:]
    api_days = [day for day in utc_days_for_local_days(gaps) if day <= now_utc.date()]
    recent_batches = await asyncio.gather(*(_recent(day) for day in api_days))

    samples: Dict[int, Dict[str, Any]] = {}
    for batch in [archived, *recent_batches]:  # la API, al final, gana
        for sample in batch:
            samples[int(sample["epoch"])] = sample
    return aggregate_daily(samples.values(), clipped)


async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> pd.DataFrame:
    return await fetch_climo_daily_for_periods(client, station_id, periods)

"""
Histórico de IMGW (Polonia) como servicio async puro.

Implementa la rama IMGW de ``/v1/climo/dataset`` con el archivo público de
datos diarios verificados (``danepubliczne.imgw.pl/data/dane_pomiarowo_
obserwacyjne/dane_meteorologiczne/dobowe``), en CSV cp1250 dentro de ZIP.

Tres tipos de estación, cada uno con su fichero:

- ``synop`` (sinópticas): ``s_d`` trae Tmáx/Tmín/Tmedia y lluvia; ``s_d_t``,
  el viento medio y la presión media.
- ``klimat`` (climatológicas): ``k_d`` con Tmáx/Tmín/Tmedia y lluvia; ``k_d_t``,
  el viento medio.
- ``opad`` (pluviométricas): ``o_d``, solo lluvia.

La agrupación de los ficheros cambia con los años (``Opis.txt``):

- año en curso: un ZIP por mes con todas las estaciones del tipo;
- 2001 hasta el año pasado: klimat y opad siguen por meses, synop pasa a un
  ZIP por estación y año (``{año}_{últimas 3 cifras del código}_s.zip``);
- antes de 2001, en carpetas de cinco años: klimat y opad un ZIP por año con
  todas las estaciones, synop un ZIP por estación con los cinco años.

Los meses aún no publicados (el archivo va de uno a dos meses por detrás) se
completan, en lo que alcance, con los diarios del almacén del poller.
Estados del CSV: «8» sin medida, «9» sin fenómeno (lluvia 0).
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import re
import zipfile
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from domain.parsing.periods import merge_date_periods
from domain.parsing.wu_climo import DAILY_SCHEMA, clip_period_tuples_to_today
from server.services import imgw
from server.services.climo_cache import get_or_fetch_climo_block

logger = logging.getLogger(__name__)

PROVIDER = "IMGW"
ARCHIVE_URL = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/dobowe"
KIND_SUFFIX = {"synop": "s", "klimat": "k", "opad": "o"}
DOWNLOAD_CONCURRENCY = 3

# Columnas (índice) de cada fichero. Los cinco primeros son código, nombre,
# año, mes y día; cada valor va seguido de su estado.
DAILY_COLUMNS = {
    "synop": {"temp_max": 5, "temp_min": 7, "temp_mean": 9, "precip_total": 13},
    "klimat": {"temp_max": 5, "temp_min": 7, "temp_mean": 9, "precip_total": 13},
    "opad": {"precip_total": 5},
}
DAILY_T_COLUMNS = {
    "synop": {"wind_mean": 7},
    "klimat": {"wind_mean": 9},
}


def _value(row: List[str], index: int, field: str) -> float:
    if index >= len(row):
        return math.nan
    status = row[index + 1].strip().strip('"') if index + 1 < len(row) else ""
    raw = row[index].strip().strip('"')
    if status == "8":
        return math.nan
    if status == "9" and field == "precip_total":
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return math.nan
    if field == "wind_mean":
        value *= 3.6
    return value


def parse_daily_csv(
    text: str, code: str, columns: Dict[str, int],
) -> Dict[date, Dict[str, float]]:
    out: Dict[date, Dict[str, float]] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or row[0].strip().strip('"') != code:
            continue
        try:
            day = date(int(row[2]), int(row[3]), int(row[4]))
        except ValueError:
            continue
        values = {field: _value(row, index, field) for field, index in columns.items()}
        out.setdefault(day, {}).update({k: v for k, v in values.items() if not math.isnan(v)})
    return out


def read_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Contenido de un miembro del ZIP.

    Algunos ficheros del archivo de IMGW están truncados en el propio servidor
    (2023_04_o.zip da 670.200 de 671.437 bytes y su CRC no cuadra): lo leído es
    válido hasta el último salto de línea, así que se aprovecha y solo se tira
    la línea cortada, en vez de perder el mes entero de todas las estaciones.
    """
    try:
        return archive.read(info)
    except (zipfile.BadZipFile, EOFError, zlib.error):
        chunks = []
        with archive.open(info) as member:
            member._expected_crc = None
            try:
                while True:
                    chunk = member.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except (EOFError, zlib.error):
                pass
        data = b"".join(chunks)
        logger.warning(
            "IMGW: %s truncado en origen (%d de %d bytes); se usa hasta la última línea completa",
            info.filename, len(data), info.file_size,
        )
        return data[: data.rfind(b"\n") + 1]

def rows_from_zip(payload: bytes, code: str, kind: str) -> Dict[date, Dict[str, float]]:
    """ZIP del archivo → {día: campos} de una estación (diario + medias)."""
    out: Dict[date, Dict[str, float]] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            name = info.filename
            if not name.endswith(".csv"):
                continue
            columns = DAILY_T_COLUMNS.get(kind) if "_d_t_" in name else DAILY_COLUMNS[kind]
            if not columns:
                continue
            text = read_zip_member(archive, info).decode("cp1250", "replace")
            for day, values in parse_daily_csv(text, code, columns).items():
                out.setdefault(day, {}).update(values)
    return out


async def _download(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    response = await client.get(url, headers={"User-Agent": imgw.USER_AGENT}, timeout=120.0)
    if response.status_code == 404:
        return b""  # no existe con esa agrupación: se prueba la siguiente
    response.raise_for_status()
    return response.content


_FOLDERS: Dict[str, List[str]] = {}


async def _five_year_folder(client: httpx.AsyncClient, kind: str, year: int) -> Optional[str]:
    """Carpeta de lustro (``1996_2000``) que contiene ``year``."""
    if kind not in _FOLDERS:
        response = await client.get(f"{ARCHIVE_URL}/{kind}/", headers={"User-Agent": imgw.USER_AGENT}, timeout=60.0)
        response.raise_for_status()
        _FOLDERS[kind] = re.findall(r'href="(\d{4}_\d{4})/"', response.text)
    for folder in _FOLDERS[kind]:
        first, last = (int(part) for part in folder.split("_"))
        if first <= year <= last:
            return folder
    return None


async def candidate_urls(
    client: httpx.AsyncClient, kind: str, code: str, year: int, month: int, *, today: date,
) -> List[str]:
    """URLs posibles, de la agrupación más probable a la menos."""
    suffix = KIND_SUFFIX[kind]
    station = code[-3:]
    if year <= 2000:
        folder = await _five_year_folder(client, kind, year)
        if folder is None:
            return []
        if kind == "synop":
            return [f"{ARCHIVE_URL}/synop/{folder}/{folder}_{station}_s.zip"]
        return [f"{ARCHIVE_URL}/{kind}/{folder}/{year}_{suffix}.zip"]
    monthly = f"{ARCHIVE_URL}/{kind}/{year}/{year}_{month:02d}_{suffix}.zip"
    if kind != "synop":
        return [monthly]
    yearly = f"{ARCHIVE_URL}/synop/{year}/{year}_{station}_s.zip"
    return [monthly, yearly] if year >= today.year else [yearly, monthly]


async def _station_month(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, kind: str, code: str,
    year: int, month: int, *, today: date,
) -> Dict[date, Dict[str, float]]:
    for url in await candidate_urls(client, kind, code, year, month, today=today):
        async def _fetch(url: str = url) -> Dict[str, Any]:
            async with semaphore:
                payload = await _download(client, url)
            if not payload:
                return {"missing": True, "rows": {}}
            rows = rows_from_zip(payload, code, kind)
            return {"missing": False, "rows": {day.isoformat(): values for day, values in rows.items()}}

        try:
            block = await get_or_fetch_climo_block(
                provider=PROVIDER,
                kind=f"archive:{url}",
                station_id=code,
                credential="public",
                client=client,
                # Año cerrado: dura; el año en curso se republica a menudo.
                ttl_s=30 * 86400 if year < today.year else 6 * 3600,
                fetcher=_fetch,
            )
        except Exception as exc:
            logger.warning("Climo IMGW: %s falló para %s: %s", url, code, exc)
            continue
        if block and not block.get("missing"):
            rows = {
                date.fromisoformat(day): values for day, values in block["rows"].items()
                if date.fromisoformat(day).year == year and date.fromisoformat(day).month == month
            }
            if kind == "opad" and rows:
                # En las pluviométricas un día ausente es «sin fenómeno» (formato
                # o_d): si la estación publicó ese mes, el resto de días son 0.
                cursor = date(year, month, 1)
                while cursor.month == month and cursor <= today:
                    rows.setdefault(cursor, {"precip_total": 0.0})
                    cursor += timedelta(days=1)
            return rows
    return {}


def _months(start: date, end: date) -> List[Tuple[int, int]]:
    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return months


def _store_daily(code: str, periods: Sequence[Tuple[date, date]]) -> Dict[date, Dict[str, float]]:
    """Diarios del almacén del poller para los días que el archivo aún no trae."""
    out: Dict[date, Dict[str, float]] = {}
    tz = imgw.STATION_TZ
    for start, end in periods:
        day = max(start, (datetime.now(tz) - timedelta(days=8)).date())
        while day <= end:
            begin = int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())
            finish = begin + 86400
            temps = [v for e, v in imgw.STORE.samples(code, "temp", since=begin) if e < finish]
            winds = [v for e, v in imgw.STORE.samples(code, "wind", since=begin) if e < finish]
            rains = [v for e, v in imgw.STORE.samples(code, "precip10", since=begin + 1) if e <= finish]
            values: Dict[str, float] = {}
            # Un día a medias no se presenta como diario: hace falta casi todo.
            if len(temps) >= 18:
                values.update(temp_max=max(temps), temp_min=min(temps), temp_mean=sum(temps) / len(temps))
            if len(winds) >= 18:
                values["wind_mean"] = sum(winds) / len(winds) * 3.6
            if len(rains) >= 120:
                values["precip_total"] = sum(max(0.0, v) for v in rains)
            if values:
                out[day] = values
            day += timedelta(days=1)
    return out


async def fetch_climo_daily_for_periods(
    client: httpx.AsyncClient,
    station_id: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today_date: Optional[date] = None,
) -> pd.DataFrame:
    code = imgw.normalize_station_id(station_id)
    today = today_date or datetime.now(imgw.STATION_TZ).date()
    clipped = merge_date_periods(clip_period_tuples_to_today(list(periods), today_date=today))
    row = imgw._station_row(code)
    kinds = [kind for kind in ("synop", "klimat", "opad") if kind in (row.get("archive_kinds") or [])]
    if not code or not clipped:
        return pd.DataFrame(columns=DAILY_SCHEMA)

    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    months = sorted({month for start, end in clipped for month in _months(start, end)})
    jobs = [
        _station_month(client, semaphore, kind, code, year, month, today=today)
        for kind in kinds for year, month in months
    ]
    days: Dict[date, Dict[str, float]] = {}
    # Prioridad synop > klimat > opad: el más completo pisa al resto.
    for result in reversed(await asyncio.gather(*jobs)):
        for day, values in result.items():
            days.setdefault(day, {}).update(values)
    for day, values in _store_daily(code, clipped).items():
        merged = days.setdefault(day, {})
        for field, value in values.items():
            merged.setdefault(field, value)

    rows = []
    for day in sorted(days):
        if not any(start <= day <= end for start, end in clipped):
            continue
        values = days[day]
        rows.append({
            "date": pd.Timestamp(day),
            "epoch": float(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()),
            "temp_mean": values.get("temp_mean", math.nan),
            "temp_max": values.get("temp_max", math.nan),
            "temp_min": values.get("temp_min", math.nan),
            "wind_mean": values.get("wind_mean", math.nan),
            "wind_dir_mean": math.nan,
            "gust_max": math.nan,
            "gust_dir_max": math.nan,
            "precip_total": values.get("precip_total", math.nan),
            "precip_rate_max": math.nan,
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

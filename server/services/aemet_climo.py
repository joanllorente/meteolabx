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
from datetime import date, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from server.services.aemet import _fetch_aemet_two_step
from domain.parsing.aemet_climo import (
    CLIMO_DAILY_SCHEMA,
    CLIMO_EXTRA_SCHEMA,
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
ROOT = Path(__file__).resolve().parents[2]
LEGACY_DAILY_DIR = ROOT / "data" / "aemet_legacy_daily"
LEGACY_DAILY_FILES = {
    "0076": "baic0007d.txt",   # Barcelona Aeropuerto / Aeroport del Prat
    "9771C": "baic0012d.txt",  # Lleida
    "9981A": "baic0005d.txt",  # Tortosa / Observatori de l'Ebre
}

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


def _legacy_float(value: Any) -> float:
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float("nan")
    return number if number != -999.9 else float("nan")


@lru_cache(maxsize=8)
def _legacy_daily_frame(idema: str) -> pd.DataFrame:
    station = str(idema or "").strip().upper()
    filename = LEGACY_DAILY_FILES.get(station)
    if not filename:
        return _empty_climo_dataframe(include_extras=False)
    path = LEGACY_DAILY_DIR / filename
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _empty_climo_dataframe(include_extras=False)

    rows: List[Dict[str, Any]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("ANY\tMES\tDIA"):
            in_table = True
            continue
        if not in_table:
            continue
        parts = stripped.split("\t")
        if len(parts) < 7:
            continue
        try:
            day = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        precip_total = _legacy_float(parts[3])
        temp_max = _legacy_float(parts[4])
        temp_min = _legacy_float(parts[5])
        solar_hours = _legacy_float(parts[6])
        temp_mean = (
            (temp_max + temp_min) / 2.0
            if not pd.isna(temp_max) and not pd.isna(temp_min)
            else float("nan")
        )
        rows.append(
            {
                "date": day.isoformat(),
                "temp_mean": temp_mean,
                "temp_max": temp_max,
                "temp_min": temp_min,
                "wind_mean": float("nan"),
                "wind_dir_mean": float("nan"),
                "gust_max": float("nan"),
                "precip_total": precip_total,
                "solar_hours": solar_hours,
            }
        )

    return _normalize_climo_daily_rows(rows)


def _legacy_coverage(idema: str) -> Optional[Tuple[date, date]]:
    frame = _legacy_daily_frame(idema)
    if frame.empty:
        return None
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.min().date(), dates.max().date()


def _legacy_daily_for_periods(idema: str, periods: Sequence[Tuple[date, date]]) -> pd.DataFrame:
    frame = _legacy_daily_frame(idema)
    if frame.empty or not periods:
        return _empty_climo_dataframe(include_extras=False)
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    mask = pd.Series(False, index=frame.index)
    for start, end in periods:
        mask |= (dates >= start) & (dates <= end)
    return frame.loc[mask].copy().reset_index(drop=True)


def _periods_outside_legacy_coverage(
    idema: str,
    periods: Sequence[Tuple[date, date]],
) -> List[Tuple[date, date]]:
    coverage = _legacy_coverage(idema)
    if coverage is None:
        return [(start, end) for start, end in periods if start <= end]
    coverage_start, coverage_end = coverage
    output: List[Tuple[date, date]] = []
    for start, end in periods:
        if start > end:
            continue
        if end < coverage_start or start > coverage_end:
            output.append((start, end))
            continue
        if start < coverage_start:
            output.append((start, min(end, coverage_start - timedelta(days=1))))
        if end > coverage_end:
            output.append((max(start, coverage_end + timedelta(days=1)), end))
    return [(start, end) for start, end in output if start <= end]


def _year_is_covered_by_legacy(idema: str, year: int) -> bool:
    coverage = _legacy_coverage(idema)
    if coverage is None:
        return False
    start, end = coverage
    return start <= date(int(year), 1, 1) and end >= date(int(year), 12, 31)


def _legacy_summary_row(frame: pd.DataFrame, day: date) -> Dict[str, Any]:
    temps_mean = pd.to_numeric(frame["temp_mean"], errors="coerce")
    temps_max = pd.to_numeric(frame["temp_max"], errors="coerce")
    temps_min = pd.to_numeric(frame["temp_min"], errors="coerce")
    precip = pd.to_numeric(frame["precip_total"], errors="coerce")
    solar = pd.to_numeric(frame.get("solar_hours", pd.Series(dtype=float)), errors="coerce")

    abs_max_date = None
    if temps_max.notna().any():
        abs_max_date = pd.to_datetime(frame.loc[temps_max.idxmax(), "date"]).strftime("%Y-%m-%d")
    abs_min_date = None
    if temps_min.notna().any():
        abs_min_date = pd.to_datetime(frame.loc[temps_min.idxmin(), "date"]).strftime("%Y-%m-%d")
    precip_max_date = None
    if precip.notna().any():
        precip_max_date = pd.to_datetime(frame.loc[precip.idxmax(), "date"]).strftime("%Y-%m-%d")

    return {
        "date": day.isoformat(),
        "epoch": float(pd.Timestamp(day).replace(tzinfo=timezone.utc).timestamp()),
        "temp_mean": float(temps_mean.mean()) if temps_mean.notna().any() else float("nan"),
        "temp_max": float(temps_max.mean()) if temps_max.notna().any() else float("nan"),
        "temp_min": float(temps_min.mean()) if temps_min.notna().any() else float("nan"),
        "wind_mean": float("nan"),
        "wind_dir_mean": float("nan"),
        "gust_max": float("nan"),
        "precip_total": float(precip.sum(min_count=1)) if precip.notna().any() else float("nan"),
        "solar_mean": float(solar.sum(min_count=1)) if solar.notna().any() else float("nan"),
        "solar_hours": float(solar.sum(min_count=1)) if solar.notna().any() else float("nan"),
        "precip_max_24h": float(precip.max()) if precip.notna().any() else float("nan"),
        "rain_days": float((precip > 0).sum()) if precip.notna().any() else float("nan"),
        "temp_abs_max": float(temps_max.max()) if temps_max.notna().any() else float("nan"),
        "temp_abs_max_date": abs_max_date,
        "temp_abs_min": float(temps_min.min()) if temps_min.notna().any() else float("nan"),
        "temp_abs_min_date": abs_min_date,
        "gust_abs_max_date": None,
        "precip_max_24h_date": precip_max_date,
        "tropical_nights": float((temps_min >= 20.0).sum()) if temps_min.notna().any() else float("nan"),
        "frost_nights": float((temps_min <= 0.0).sum()) if temps_min.notna().any() else float("nan"),
    }


def _legacy_monthly_for_year(idema: str, year: int) -> pd.DataFrame:
    if not _year_is_covered_by_legacy(idema, year):
        return _empty_climo_dataframe(include_extras=True)
    frame = _legacy_daily_for_periods(idema, [(date(int(year), 1, 1), date(int(year), 12, 31))])
    if frame.empty:
        return _empty_climo_dataframe(include_extras=True)
    dates = pd.to_datetime(frame["date"], errors="coerce")
    rows: List[Dict[str, Any]] = []
    for month in range(1, 13):
        month_frame = frame.loc[(dates.dt.year == int(year)) & (dates.dt.month == month)]
        if month_frame.empty:
            continue
        rows.append(_legacy_summary_row(month_frame, date(int(year), month, 1)))
    if not rows:
        return _empty_climo_dataframe(include_extras=True)
    return _merge_summary_frames([pd.DataFrame(rows)])


def _legacy_yearly_for_years(idema: str, years: Sequence[int]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in sorted({int(y) for y in years}):
        if not _year_is_covered_by_legacy(idema, year):
            continue
        frame = _legacy_daily_for_periods(idema, [(date(year, 1, 1), date(year, 12, 31))])
        if not frame.empty:
            rows.append(_legacy_summary_row(frame, date(year, 1, 1)))
    if not rows:
        return _empty_climo_dataframe(include_extras=True)
    return _merge_summary_frames([pd.DataFrame(rows)])


def _merge_summary_frames(chunks: Sequence[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [c for c in chunks if isinstance(c, pd.DataFrame) and not c.empty]
    if not non_empty:
        return _empty_climo_dataframe(include_extras=True)
    frame = pd.concat(non_empty, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = (
        frame.dropna(subset=["date"])
        .sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    for col in CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    return frame[CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA]


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

    legacy_df = _legacy_daily_for_periods(station, periods)
    api_periods = _periods_outside_legacy_coverage(station, periods)
    if not api_periods:
        return legacy_df if not legacy_df.empty else _empty_climo_dataframe(include_extras=False)

    windows: List[Tuple[date, date]] = []
    for start, end in api_periods:
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
    return _merge_daily_chunks([legacy_df, *chunks])


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
    legacy_df = _legacy_monthly_for_year(idema, yy)
    if not legacy_df.empty:
        return legacy_df
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

    legacy_df = _legacy_yearly_for_years(idema, valid_years)
    api_years = [year for year in valid_years if not _year_is_covered_by_legacy(idema, year)]
    if not api_years:
        return legacy_df if not legacy_df.empty else _empty_climo_dataframe(include_extras=True)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _block(chunk_start: int) -> List[Any]:
        chunk_end = min(chunk_start + 2, max(api_years))
        async with semaphore:
            return await _fetch_monthlyannual_raw(
                client, idema, api_key, chunk_start, chunk_end,
            )

    blocks = await asyncio.gather(*(
        _block(start) for start in range(min(api_years), max(api_years) + 1, 3)
    ))

    monthly_metrics: Dict[Tuple[int, int], Dict[str, Any]] = {}
    annual_metrics: Dict[int, Dict[str, Any]] = {}
    for payload in blocks:
        chunk_monthly, chunk_annual = _bucket_monthlyannual_records(payload)
        monthly_metrics.update(chunk_monthly)
        annual_metrics.update(chunk_annual)

    api_df = _yearly_df(api_years, monthly_metrics, annual_metrics)
    return _merge_summary_frames([legacy_df, api_df])


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

"""
Climatología de Meteocat como servicio async puro.

Implementa la rama METEOCAT de ``/v1/climo/dataset`` de forma asíncrona.

- Transporte: ``httpx.AsyncClient`` inyectado (reutiliza ``_get_json``
  del servicio de observaciones Meteocat) contra los endpoints
  estadísticos ``/variables/estadistics/{diaris,mensuals,anuals}``.
- Parsing/ensamblado/códigos: ``domain/parsing/meteocat_climo``.
- Selección de candidatos por altura del anemómetro (2/6/10 m): se
  prueban en orden y gana el primero con datos. Las descargas se
  memoizan por petición para no repetir llamadas.
- Errores por petición: best-effort (un mes/variable caído no tumba el
  dataset), salvo 401 (key de servidor mal configurada) que corta.
- ``fetch_climo_dataset`` devuelve ``(DataFrame, extremes|None)`` para
  conservar en un único contrato los extremos calculados.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from server.services.meteocat import BASE_URL, _get_json, _require_api_key
from domain.parsing import meteocat_climo as P

logger = logging.getLogger(__name__)

PROVIDER = "METEOCAT"


# =====================================================================
# Caché de peticiones estadísticas (memoiza por proceso de petición)
# =====================================================================

class _StatsClient:
    def __init__(self, client: httpx.AsyncClient, station_code: str, api_key: str):
        self.client = client
        self.code = str(station_code).strip().upper()
        self.api_key = api_key
        self._daily: Dict[Tuple[int, int, int], Dict[str, float]] = {}
        self._monthly: Dict[Tuple[int, int], Dict[str, Dict[str, Any]]] = {}
        self._annual: Dict[int, Dict[int, Dict[str, Any]]] = {}

    async def _safe_get(self, url: str, params: Dict[str, Any]) -> Any:
        """GET best-effort: 401 corta; otros errores → None (sin datos)."""
        try:
            return await _get_json(self.client, url, self.api_key, params=params)
        except ProviderError as exc:
            if exc.error_code == "provider_unauthorized":
                raise
            logger.warning("Climo Meteocat %s falló: %s", url, exc.detail)
            return None

    async def daily(self, var_code: int, year: int, month: int) -> Dict[str, float]:
        key = (int(var_code), int(year), int(month))
        if key in self._daily:
            return self._daily[key]
        payload = await self._safe_get(
            f"{BASE_URL}/variables/estadistics/diaris/{int(var_code)}",
            {"codiEstacio": self.code, "any": f"{int(year):04d}", "mes": f"{int(month):02d}"},
        )
        out = P.parse_daily_stats_values(payload) if payload is not None else {}
        self._daily[key] = out
        return out

    async def monthly(self, var_code: int, year: int) -> Dict[str, Dict[str, Any]]:
        key = (int(var_code), int(year))
        if key in self._monthly:
            return self._monthly[key]
        payload = await self._safe_get(
            f"{BASE_URL}/variables/estadistics/mensuals/{int(var_code)}",
            {"codiEstacio": self.code, "any": f"{int(year):04d}"},
        )
        out = P.parse_monthly_stats_by_month(payload) if payload is not None else {}
        self._monthly[key] = out
        return out

    async def annual(self, var_code: int) -> Dict[int, Dict[str, Any]]:
        key = int(var_code)
        if key in self._annual:
            return self._annual[key]
        payload = await self._safe_get(
            f"{BASE_URL}/variables/estadistics/anuals/{int(var_code)}",
            {"codiEstacio": self.code},
        )
        out = P.parse_annual_stats_by_year(payload) if payload is not None else {}
        self._annual[key] = out
        return out


async def _daily_candidates(stats: _StatsClient, candidates: Sequence[int], year: int, month: int) -> Dict[str, float]:
    """Primer candidato (en orden) con datos para ese mes."""
    for var_code in candidates:
        values = await stats.daily(int(var_code), year, month)
        if values:
            return values
    return {}


# =====================================================================
# Histórico diario (modo "monthly" del frontend con periodos cortos)
# =====================================================================

async def fetch_daily_history_for_periods(
    client: httpx.AsyncClient,
    station_code: str,
    api_key: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return P.empty_daily_df()

    stats = _StatsClient(client, code, api_key)
    start = min(p[0] for p in periods)
    end = max(p[1] for p in periods)

    rows_by_day: Dict[str, Dict[str, Any]] = {}
    months = list(P.iter_months(start, end))

    async def _resolve_month(yy: int, mm: int) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for metric_name, candidates in P.CLIMO_STAT_CODES.items():
            out[metric_name] = await _daily_candidates(stats, candidates, yy, mm)
        return out

    month_results = await asyncio.gather(*(_resolve_month(yy, mm) for yy, mm in months))

    for month_data in month_results:
        for metric_name, day_values in month_data.items():
            for day_txt, raw_value in day_values.items():
                row = rows_by_day.setdefault(day_txt, {
                    "date": day_txt, "epoch": float("nan"),
                    "temp_mean": float("nan"), "temp_max": float("nan"), "temp_min": float("nan"),
                    "wind_mean": float("nan"), "wind_dir_mean": float("nan"),
                    "gust_max": float("nan"), "precip_total": float("nan"),
                })
                value = float(raw_value)
                if metric_name in P.CLIMO_WIND_METRICS and not P._is_nan(value):
                    value = P.ms_to_kmh(value)
                row[metric_name] = value
                row["epoch"] = P.climo_epoch_from_label(day_txt)

    if not rows_by_day:
        return P.empty_daily_df()

    frame = pd.DataFrame(rows_by_day.values())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in P.CLIMO_DAILY_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "wind_dir_mean", "gust_max", "precip_total"]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
    if missing_mean.any():
        frame.loc[missing_mean, "temp_mean"] = (frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]) / 2.0
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame = frame.sort_values("date").reset_index(drop=True)
    mask = frame["date"].between(pd.to_datetime(start), pd.to_datetime(end))
    return frame.loc[mask].copy()[P.CLIMO_DAILY_SCHEMA]


# =====================================================================
# Histórico mensual
# =====================================================================

async def _fill_monthly(
    stats: _StatsClient,
    rows_by_month: Dict[Any, Dict[str, Any]],
    years: Sequence[int],
) -> None:
    for metric_name, candidates in P.MONTHLY_CLIMO_CODES.items():
        chosen_code: Optional[int] = None
        for candidate in candidates:
            has_data = False
            for yy in years:
                series = await stats.monthly(int(candidate), int(yy))
                if any(month_key in rows_by_month and P.metric_value_available(item)
                       for month_key, item in series.items()):
                    has_data = True
                    break
            if has_data:
                chosen_code = int(candidate)
                break
        if chosen_code is None:
            chosen_code = int(candidates[0]) if candidates else None
        if chosen_code is None:
            continue
        for yy in years:
            series = await stats.monthly(chosen_code, int(yy))
            for month_key, data in series.items():
                if month_key in rows_by_month and isinstance(data, dict):
                    P.apply_climo_metric_value(rows_by_month[month_key], metric_name, data)


async def fetch_monthly_history_for_year(
    client: httpx.AsyncClient, station_code: str, api_key: str, year: int,
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    if not code:
        return P.empty_annual_df()
    yy = int(year)
    stats = _StatsClient(client, code, api_key)
    rows_by_month = P.build_climo_rows([f"{yy:04d}-{mm:02d}-01" for mm in range(1, 13)])
    await _fill_monthly(stats, rows_by_month, [yy])
    return P.finalize_climo_rows(rows_by_month)


async def fetch_monthly_history_for_periods(
    client: httpx.AsyncClient, station_code: str, api_key: str,
    periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return P.empty_annual_df()
    stats = _StatsClient(client, code, api_key)
    requested_months = {f"{p[0].year:04d}-{p[0].month:02d}-01" for p in periods}
    rows_by_month = P.build_climo_rows(list(requested_months))
    years = sorted({p[0].year for p in periods})
    await _fill_monthly(stats, rows_by_month, years)
    return P.finalize_climo_rows(rows_by_month)


# =====================================================================
# Histórico anual
# =====================================================================

async def fetch_annual_history_for_years(
    client: httpx.AsyncClient, station_code: str, api_key: str, years: Sequence[int],
) -> pd.DataFrame:
    code = str(station_code).strip().upper()
    valid_years = sorted({int(y) for y in years})
    if not code or not valid_years:
        return P.empty_annual_df()

    stats = _StatsClient(client, code, api_key)
    rows_by_year: Dict[int, Dict[str, Any]] = {
        int(y): P.empty_climo_row(f"{int(y):04d}-01-01", P.climo_epoch_from_label(f"{int(y):04d}-01-01"))
        for y in valid_years
    }
    selected_years = set(valid_years)

    for metric_name, candidates in P.ANNUAL_CLIMO_CODES.items():
        chosen_code: Optional[int] = None
        for candidate in candidates:
            series = await stats.annual(int(candidate))
            if any(int(y) in selected_years and P.metric_value_available(item)
                   for y, item in series.items()):
                chosen_code = int(candidate)
                break
        if chosen_code is None:
            chosen_code = int(candidates[0]) if candidates else None
        if chosen_code is None:
            continue
        series = await stats.annual(chosen_code)
        for y in valid_years:
            data = series.get(int(y), {})
            if data:
                P.apply_climo_metric_value(rows_by_year[int(y)], metric_name, data)

    # temp_mean derivada (sin pisar la media anual nativa si vino).
    for y in valid_years:
        row = rows_by_year[int(y)]
        if P._is_nan(row["temp_mean"]) and not P._is_nan(row["temp_max"]) and not P._is_nan(row["temp_min"]):
            row["temp_mean"] = (row["temp_max"] + row["temp_min"]) / 2.0

    return P.finalize_climo_rows(rows_by_year, fill_temp_mean=False)


# =====================================================================
# Extremos derivados (extremes_overrides del frontend)
# =====================================================================

async def _daily_metric_for_months(
    stats: _StatsClient, candidates: Sequence[int], year: int, months: Sequence[int],
) -> Dict[str, float]:
    """Valores diarios acumulados sobre varios meses; candidato 'sticky'."""
    values_by_day: Dict[str, float] = {}
    chosen_code: Optional[int] = None
    for month in months:
        if chosen_code is not None:
            data = await stats.daily(chosen_code, year, month)
            if data:
                values_by_day.update(data)
                continue
        for candidate in candidates:
            data = await stats.daily(int(candidate), year, month)
            if data:
                values_by_day.update(data)
                chosen_code = int(candidate)
                break
    return values_by_day


def _extreme_min_of_max(tmax_days: Dict[str, float]) -> Optional[Dict[str, str]]:
    s = pd.to_numeric(pd.Series(tmax_days, dtype=float), errors="coerce").dropna()
    if s.empty:
        return None
    return {"Valor": f"{float(s.min()):.1f} °C", "Fecha": P.format_date_for_ui(str(s.idxmin()))}


def _extreme_max_of_min(tmin_days: Dict[str, float]) -> Optional[Dict[str, str]]:
    s = pd.to_numeric(pd.Series(tmin_days, dtype=float), errors="coerce").dropna()
    if s.empty:
        return None
    return {"Valor": f"{float(s.max()):.1f} °C", "Fecha": P.format_date_for_ui(str(s.idxmax()))}


def _extreme_windiest(wind_days: Dict[str, float]) -> Optional[Dict[str, str]]:
    s = pd.to_numeric(pd.Series(wind_days, dtype=float), errors="coerce").dropna()
    if s.empty:
        return None
    return {"Valor": f"{float(s.max()) * 3.6:.1f} km/h", "Fecha": P.format_date_for_ui(str(s.idxmax()))}


async def fetch_daily_extremes_for_year(
    client: httpx.AsyncClient, station_code: str, api_key: str, year: int,
) -> Dict[str, Dict[str, str]]:
    code = str(station_code).strip().upper()
    if not code:
        return {}
    yy = int(year)
    stats = _StatsClient(client, code, api_key)

    tmax_days = await _daily_metric_for_months(stats, [P.STAT_TEMP_MAX], yy, [11, 12, 1, 2, 3, 4])
    tmin_days = await _daily_metric_for_months(stats, [P.STAT_TEMP_MIN], yy, [5, 6, 7, 8, 9])
    wind_days = await _daily_metric_for_months(stats, P.WIND_MEAN_DAILY_CANDIDATES, yy, list(range(1, 13)))

    result: Dict[str, Dict[str, str]] = {}
    if (e := _extreme_min_of_max(tmax_days)):
        result["Mínima de máximas"] = e
    if (e := _extreme_max_of_min(tmin_days)):
        result["Máxima de mínimas"] = e
    if (e := _extreme_windiest(wind_days)):
        result["Día más ventoso (viento medio)"] = e
    return result


async def fetch_daily_extremes_for_periods(
    client: httpx.AsyncClient, station_code: str, api_key: str,
    periods: Sequence[Tuple[date, date]],
) -> Dict[str, Dict[str, str]]:
    code = str(station_code).strip().upper()
    if not code or not periods:
        return {}
    stats = _StatsClient(client, code, api_key)
    requested = sorted({(p[0].year, p[0].month) for p in periods})

    tmax_days: Dict[str, float] = {}
    tmin_days: Dict[str, float] = {}
    wind_days: Dict[str, float] = {}
    chosen_wind: Optional[int] = None

    for yy, mm in requested:
        tmax_days.update(await stats.daily(P.STAT_TEMP_MAX, yy, mm))
        tmin_days.update(await stats.daily(P.STAT_TEMP_MIN, yy, mm))
        if chosen_wind is not None:
            data = await stats.daily(chosen_wind, yy, mm)
            if data:
                wind_days.update(data)
                continue
        for wind_code in P.WIND_MEAN_DAILY_CANDIDATES:
            data = await stats.daily(int(wind_code), yy, mm)
            if data:
                chosen_wind = int(wind_code)
                wind_days.update(data)
                break

    result: Dict[str, Dict[str, str]] = {}
    if (e := _extreme_min_of_max(tmax_days)):
        result["Mínima de máximas"] = e
    max_of_min = _extreme_max_of_min(tmin_days)
    if max_of_min:
        result["Máxima de mínimas"] = max_of_min
        s = pd.to_numeric(pd.Series(tmin_days, dtype=float), errors="coerce").dropna()
        # Umbral INCLUSIVO (≥): noche tropical = la mínima no baja de 20 °C.
        result["Noches tropicales (mín > 20 °C)"] = {"Valor": f"{int((s >= 20.0).sum())} noches", "Fecha": "—"}
        result["Noches tórridas (mín > 25 °C)"] = {"Valor": f"{int((s >= 25.0).sum())} noches", "Fecha": "—"}
    if (e := _extreme_windiest(wind_days)):
        result["Día más ventoso (viento medio)"] = e
    return result


# =====================================================================
# Orquestación del dataset canónico
# =====================================================================

async def fetch_climo_dataset(
    client: httpx.AsyncClient,
    station_code: str,
    api_key: str,
    *,
    summary_mode: str,
    periods: Sequence[Tuple[date, date]],
    selected_years: Sequence[int],
) -> Tuple[pd.DataFrame, Optional[Dict[str, Dict[str, str]]]]:
    """Selección de modo idéntica a ``_fetch_meteocat_historical_dataset``."""
    _require_api_key(api_key)
    years = [int(y) for y in selected_years]

    if summary_mode == "annual" and len(years) > 1:
        df = await fetch_annual_history_for_years(client, station_code, api_key, years)
        return df, None

    if summary_mode == "annual" and len(years) == 1:
        df = await fetch_monthly_history_for_year(client, station_code, api_key, years[0])
        extremes = await fetch_daily_extremes_for_year(client, station_code, api_key, years[0])
        return df, (extremes or None)

    if summary_mode == "monthly":
        if len(periods) == 1:
            df = await fetch_daily_history_for_periods(client, station_code, api_key, periods)
        else:
            df = await fetch_monthly_history_for_periods(client, station_code, api_key, periods)
        extremes = await fetch_daily_extremes_for_periods(client, station_code, api_key, periods)
        return df, (extremes or None)

    df = await fetch_daily_history_for_periods(client, station_code, api_key, periods)
    return df, None

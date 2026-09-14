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
import calendar
import logging
from datetime import date
from typing import Any, Dict, Optional, Sequence, Tuple

import httpx
import pandas as pd

from server.schemas.errors import ProviderError
from server.services.meteocat import BASE_URL, _get_json, _require_api_key
from server.services.climo_cache import get_or_fetch_climo_block
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
            year = int(params.get("any")) if str(params.get("any", "")).isdigit() else None
            month = int(params.get("mes")) if str(params.get("mes", "")).isdigit() else None
            end_date = None
            if year is not None and month is not None:
                end_date = date(year, month, calendar.monthrange(year, month)[1])
            elif year is not None:
                end_date = date(year, 12, 31)
            kind = f"{url.rsplit('/', 1)[-1]}:{sorted(params.items())}"
            return await get_or_fetch_climo_block(
                provider=PROVIDER,
                kind=kind,
                station_id=self.code,
                credential=self.api_key,
                client=self.client,
                end_date=end_date,
                fetcher=lambda: _get_json(self.client, url, self.api_key, params=params),
            )
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
    rows_by_day: Dict[str, Dict[str, Any]] = {}
    # Los periodos pueden ser discontinuos (p. ej. agosto de cinco años).
    # Recorrer desde el primero hasta el último descargaría también los 55
    # meses intermedios y, peor aún, acabaría incluyéndolos en el histograma.
    months = sorted({
        month
        for period_start, period_end in periods
        for month in P.iter_months(period_start, period_end)
    })

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
                    "gust_max": float("nan"), "gust_dir_max": float("nan"),
                    "precip_total": float("nan"),
                    "precip_rate_max": float("nan"),
                    "solar_mean": float("nan"),
                })
                value = float(raw_value)
                if metric_name in P.CLIMO_WIND_METRICS and not P._is_nan(value):
                    value = P.ms_to_kmh(value)
                elif metric_name == "precip_rate_max" and not P._is_nan(value):
                    value *= 60.0
                row[metric_name] = value
                row["epoch"] = P.climo_epoch_from_label(day_txt)

    return _daily_frame_from_rows(rows_by_day, periods)


def _empty_daily_row(day_txt: str) -> Dict[str, Any]:
    return {
        "date": day_txt, "epoch": P.climo_epoch_from_label(day_txt),
        "temp_mean": float("nan"), "temp_max": float("nan"), "temp_min": float("nan"),
        "wind_mean": float("nan"), "wind_dir_mean": float("nan"),
        "gust_max": float("nan"), "gust_dir_max": float("nan"),
        "precip_total": float("nan"),
        "precip_rate_max": float("nan"),
        "solar_mean": float("nan"),
    }


def _daily_frame_from_rows(
    rows_by_day: Dict[str, Dict[str, Any]], periods: Sequence[Tuple[date, date]],
) -> pd.DataFrame:
    """Filas diarias ya en unidades de la app → DataFrame recortado a los periodos."""
    if not rows_by_day:
        return P.empty_daily_df()

    frame = pd.DataFrame(rows_by_day.values())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in P.CLIMO_DAILY_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean",
        "wind_dir_mean", "gust_max", "gust_dir_max", "precip_total", "precip_rate_max",
        "solar_mean",
    ]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
    if missing_mean.any():
        frame.loc[missing_mean, "temp_mean"] = (frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]) / 2.0
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_rate_max"] = frame["precip_rate_max"].clip(lower=0)
    frame = frame.sort_values("date").reset_index(drop=True)
    mask = pd.Series(False, index=frame.index)
    for period_start, period_end in periods:
        mask |= frame["date"].between(pd.to_datetime(period_start), pd.to_datetime(period_end))
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


def _daily_extremes_from_frame(frame: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    """Calcula hitos desde una serie diaria ya descargada, sin repetir API."""
    if frame.empty or "date" not in frame.columns:
        return {}
    dates = pd.to_datetime(frame["date"], errors="coerce")

    def values(column: str) -> Dict[str, float]:
        if column not in frame.columns:
            return {}
        numeric = pd.to_numeric(frame[column], errors="coerce")
        return {
            day.strftime("%Y-%m-%d"): float(value)
            for day, value in zip(dates, numeric)
            if not pd.isna(day) and not pd.isna(value)
        }

    tmax_days = values("temp_max")
    tmin_days = values("temp_min")
    # _extreme_windiest recibe los valores nativos en m/s; el frame común ya
    # está en km/h, por lo que se divide antes de reutilizar el helper.
    wind_days = {day: value / 3.6 for day, value in values("wind_mean").items()}
    result: Dict[str, Dict[str, str]] = {}
    if (extreme := _extreme_min_of_max(tmax_days)):
        result["Mínima de máximas"] = extreme
    if (extreme := _extreme_max_of_min(tmin_days)):
        result["Máxima de mínimas"] = extreme
    if tmin_days:
        minima = pd.Series(tmin_days, dtype=float)
        result["Noches tropicales (mín > 20 °C)"] = {
            "Valor": f"{int((minima >= 20.0).sum())} noches", "Fecha": "—",
        }
        result["Noches tórridas (mín > 25 °C)"] = {
            "Valor": f"{int((minima >= 25.0).sum())} noches", "Fecha": "—",
        }
    if (extreme := _extreme_windiest(wind_days)):
        result["Día más ventoso (viento medio)"] = extreme
    return result


def _add_characteristic_counts(
    frame: pd.DataFrame, extremes: Dict[str, Dict[str, str]],
) -> pd.DataFrame:
    """Conserva en las filas agregadas los recuentos derivados de mínimas diarias."""
    if frame.empty:
        return frame
    out = frame.copy()
    metric_columns = {
        "Noches tropicales (mín > 20 °C)": "tropical_nights",
        "Noches tórridas (mín > 25 °C)": "torrid_nights",
    }
    for metric_name, column in metric_columns.items():
        raw_value = str((extremes.get(metric_name) or {}).get("Valor", "")).split(" ", 1)[0]
        try:
            value = float(raw_value.replace(",", "."))
        except (TypeError, ValueError):
            continue
        if column not in out.columns:
            out[column] = float("nan")
        out[column] = pd.to_numeric(out[column], errors="coerce")
        out.loc[:, column] = float("nan")
        out.loc[out.index[0], column] = value
    return out


async def fetch_daily_extremes_for_year(
    client: httpx.AsyncClient, station_code: str, api_key: str, year: int,
) -> Dict[str, Dict[str, str]]:
    code = str(station_code).strip().upper()
    if not code:
        return {}
    yy = int(year)
    stats = _StatsClient(client, code, api_key)

    tmax_days = await _daily_metric_for_months(stats, [P.STAT_TEMP_MAX], yy, [11, 12, 1, 2, 3, 4])
    tmin_days = await _daily_metric_for_months(stats, [P.STAT_TEMP_MIN], yy, list(range(1, 13)))
    wind_days = await _daily_metric_for_months(stats, P.WIND_MEAN_DAILY_CANDIDATES, yy, list(range(1, 13)))

    result: Dict[str, Dict[str, str]] = {}
    if (e := _extreme_min_of_max(tmax_days)):
        result["Mínima de máximas"] = e
    if (e := _extreme_max_of_min(tmin_days)):
        result["Máxima de mínimas"] = e
    if tmin_days:
        s = pd.to_numeric(pd.Series(tmin_days, dtype=float), errors="coerce").dropna()
        result["Noches tropicales (mín > 20 °C)"] = {"Valor": f"{int((s >= 20.0).sum())} noches", "Fecha": "—"}
        result["Noches tórridas (mín > 25 °C)"] = {"Valor": f"{int((s >= 25.0).sum())} noches", "Fecha": "—"}
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
        if chosen_wind is not None and wind_days:
            wind_values = pd.to_numeric(pd.Series(wind_days, dtype=float), errors="coerce").dropna()
            if not wind_values.empty:
                winner_day = str(wind_values.idxmax())
                try:
                    winner_date = date.fromisoformat(winner_day)
                except ValueError:
                    winner_date = None
                direction_code_by_wind_code = {
                    P.STAT_WIND_MEAN_2: P.STAT_WIND_DIR_MEAN_2,
                    P.STAT_WIND_MEAN_6: P.STAT_WIND_DIR_MEAN_6,
                    P.STAT_WIND_MEAN_10: P.STAT_WIND_DIR_MEAN_10,
                }
                direction_code = direction_code_by_wind_code.get(int(chosen_wind))
                if winner_date is not None and direction_code is not None:
                    # Sólo se pide la dirección del mes que contiene el día
                    # ganador: una llamada mensual adicional como máximo.
                    direction_days = await stats.daily(
                        direction_code, winner_date.year, winner_date.month
                    )
                    direction = P._safe_float(direction_days.get(winner_day))
                    if not P._is_nan(direction):
                        e["Dirección"] = f"{float(direction):.1f}"
        result["Día más ventoso (viento medio)"] = e
    return result


# =====================================================================
# Histórico desde Dades Obertes (sin cuota)
# =====================================================================

# Viento y dirección, y racha y su dirección, van por parejas de la misma
# altura; se prefiere 2 m como en ``CLIMO_STAT_CODES``.
_OPEN_WIND_PAIRS = (
    (P.STAT_WIND_MEAN_2, P.STAT_WIND_DIR_MEAN_2),
    (P.STAT_WIND_MEAN_6, P.STAT_WIND_DIR_MEAN_6),
    (P.STAT_WIND_MEAN_10, P.STAT_WIND_DIR_MEAN_10),
)
_OPEN_GUST_PAIRS = (
    (P.STAT_GUST_MAX_2, P.STAT_GUST_DIR_2),
    (P.STAT_GUST_MAX_6, P.STAT_GUST_DIR_6),
    (P.STAT_GUST_MAX_10, P.STAT_GUST_DIR_10),
)
_OPEN_DAILY_CODES = sorted({
    P.STAT_TEMP_MEAN, P.STAT_TEMP_MAX, P.STAT_TEMP_MIN,
    P.STAT_PRECIP, P.STAT_PRECIP_MAX_1MIN, P.STAT_SOLAR_GLOBAL,
    *(code for pair in _OPEN_WIND_PAIRS + _OPEN_GUST_PAIRS for code in pair),
})
# El dataset diario va dos días por detrás. Solo se rellena desde las
# semihorarias un hueco reciente de ese tamaño; más atrás no es retraso de
# publicación sino una estación sin datos, y no hay nada que inventar.
_OPEN_GAP_FILL_MAX_DAYS = 10
# Un día semihorario se da por completo con 43 de sus 48 lecturas (90 %).
_HALF_HOURLY_MIN_SAMPLES = 43


def _nan() -> float:
    return float("nan")


def _rows_from_open_daily(values: Dict[int, Dict[str, float]]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    days = sorted({day for by_day in values.values() for day in by_day})
    for day in days:
        row = _empty_daily_row(day)

        def pick(code: int) -> float:
            return float(values.get(code, {}).get(day, _nan()))

        row["temp_mean"] = pick(P.STAT_TEMP_MEAN)
        row["temp_max"] = pick(P.STAT_TEMP_MAX)
        row["temp_min"] = pick(P.STAT_TEMP_MIN)
        row["precip_total"] = pick(P.STAT_PRECIP)
        rate = pick(P.STAT_PRECIP_MAX_1MIN)
        # Milímetros en un minuto → intensidad en mm/h, como el camino XEMA.
        row["precip_rate_max"] = rate * 60.0 if not P._is_nan(rate) else _nan()
        row["solar_mean"] = pick(P.STAT_SOLAR_GLOBAL)
        for speed_code, direction_code in _OPEN_WIND_PAIRS:
            speed = pick(speed_code)
            if not P._is_nan(speed):
                row["wind_mean"] = P.ms_to_kmh(speed)
                row["wind_dir_mean"] = pick(direction_code)
                break
        for gust_code, direction_code in _OPEN_GUST_PAIRS:
            gust = pick(gust_code)
            if not P._is_nan(gust):
                row["gust_max"] = P.ms_to_kmh(gust)
                row["gust_dir_max"] = pick(direction_code)
                break
        rows[day] = row
    return rows


def _circular_mean_deg(directions: Sequence[float]) -> float:
    import math

    if not directions:
        return _nan()
    sin_sum = sum(math.sin(math.radians(value)) for value in directions)
    cos_sum = sum(math.cos(math.radians(value)) for value in directions)
    if abs(sin_sum) < 1e-9 and abs(cos_sum) < 1e-9:
        return _nan()
    return math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0


def _rows_from_half_hourly(
    maps: Dict[int, list], days: Sequence[date],
) -> Dict[str, Dict[str, Any]]:
    """Reconstruye días completos desde las lecturas semihorarias (días UTC).

    Solo para el tramo que el dataset diario aún no ha publicado. Una
    variable sin cobertura suficiente queda en blanco: una máxima de medio
    día no es la máxima del día.
    """
    from datetime import datetime as _dt, timedelta, timezone as _tz

    from server.services import meteocat as mc

    rows: Dict[str, Dict[str, Any]] = {}
    for day in days:
        start = int(_dt(day.year, day.month, day.day, tzinfo=_tz.utc).timestamp())
        end = start + 86400

        def samples(code: int) -> list:
            return [value for epoch, value in maps.get(code, []) if start <= int(epoch) < end]

        def complete(code: int) -> list:
            values = samples(code)
            return values if len(values) >= _HALF_HOURLY_MIN_SAMPLES else []

        row = _empty_daily_row(day.isoformat())
        temp = complete(mc.V_TEMP)
        tmax = complete(mc.V_TEMP_MAX) or temp
        tmin = complete(mc.V_TEMP_MIN) or temp
        if temp:
            row["temp_mean"] = sum(temp) / len(temp)
        if tmax:
            row["temp_max"] = max(tmax)
        if tmin:
            row["temp_min"] = min(tmin)
        rain = complete(mc.V_PRECIP)
        if rain:
            row["precip_total"] = sum(max(0.0, value) for value in rain)
        solar = complete(mc.V_SOLAR)
        if solar:
            # W/m² medios del día → MJ/m², la unidad de la irradiación diaria.
            row["solar_mean"] = sum(solar) / len(solar) * 86400.0 / 1e6
        for speed_code, direction_code in (
            (mc.V_WIND_2M, mc.V_WIND_DIR_2M),
            (mc.V_WIND_6M, mc.V_WIND_DIR_6M),
            (mc.V_WIND, mc.V_WIND_DIR),
        ):
            speeds = complete(speed_code)
            if speeds:
                row["wind_mean"] = P.ms_to_kmh(sum(speeds) / len(speeds))
                row["wind_dir_mean"] = _circular_mean_deg(samples(direction_code))
                break
        for gust_code, direction_code in (
            (mc.V_GUST_2M, mc.V_GUST_DIR_2M),
            (mc.V_GUST_6M, mc.V_GUST_DIR_6M),
            (mc.V_GUST, mc.V_GUST_DIR),
        ):
            gusts = [
                (epoch, value) for epoch, value in maps.get(gust_code, [])
                if start <= int(epoch) < end
            ]
            if len(gusts) >= _HALF_HOURLY_MIN_SAMPLES:
                epoch, value = max(gusts, key=lambda item: item[1])
                row["gust_max"] = P.ms_to_kmh(value)
                direction = dict(maps.get(direction_code, [])).get(epoch)
                row["gust_dir_max"] = float(direction) if direction is not None else _nan()
                break
        if any(
            not P._is_nan(row[column])
            for column in ("temp_mean", "temp_max", "temp_min", "precip_total")
        ):
            rows[day.isoformat()] = row
    return rows


async def fetch_open_data_daily_for_periods(
    client: httpx.AsyncClient,
    station_code: str,
    periods: Sequence[Tuple[date, date]],
    *,
    today: Optional[date] = None,
) -> pd.DataFrame:
    """Serie diaria de los periodos pedidos desde Dades Obertes.

    Da igual el modo: un mes, un año o veinte llegan como días, y los
    resúmenes mensuales y anuales los agrega después el pipeline común, como
    con IEM o LHMT. Se baja un bloque por año natural —una sola petición de
    menos de un segundo, cacheable entero— y solo de los años que tocan los
    periodos, para no descargar los intermedios de una selección discontinua.

    Los días que el dataset diario aún no publica (va dos días por detrás) se
    reconstruyen desde las lecturas semihorarias. El de hoy no: está a medias.
    """
    from datetime import datetime as _dt, timedelta, timezone as _tz

    from server.services import meteocat as mc
    from server.services import meteocat_open_data as open_data

    code = str(station_code).strip().upper()
    if not code or not periods:
        return P.empty_daily_df()
    today = today or _dt.now(_tz.utc).date()
    last_complete = today - timedelta(days=1)
    years = sorted({
        year
        for period_start, period_end in periods
        for year in range(period_start.year, min(period_end, last_complete).year + 1)
        if period_start <= last_complete
    })
    if not years:
        return P.empty_daily_df()

    semaphore = asyncio.Semaphore(open_data.DAILY_CONCURRENCY)

    async def _year(year: int) -> Dict[int, Dict[str, float]]:
        async with semaphore:
            return await get_or_fetch_climo_block(
                provider=PROVIDER,
                kind=f"open-daily:{year}:{','.join(map(str, _OPEN_DAILY_CODES))}",
                station_id=code,
                credential="",
                client=client,
                end_date=date(year, 12, 31),
                fetcher=lambda: open_data.fetch_daily_values(
                    code, date(year, 1, 1), min(date(year, 12, 31), last_complete),
                    _OPEN_DAILY_CODES, client=client,
                ),
            ) or {}

    blocks = await asyncio.gather(*(_year(year) for year in years))
    values: Dict[int, Dict[str, float]] = {}
    for block in blocks:
        for var_code, by_day in block.items():
            values.setdefault(var_code, {}).update(by_day)
    rows_by_day = _rows_from_open_daily(values)

    requested_end = min(max(period_end for _start, period_end in periods), last_complete)
    published = max((date.fromisoformat(day) for day in rows_by_day), default=None)
    if published is not None and published < requested_end:
        gap_start = published + timedelta(days=1)
        if (requested_end - gap_start).days < _OPEN_GAP_FILL_MAX_DAYS:
            gap_days = [
                gap_start + timedelta(days=offset)
                for offset in range((requested_end - gap_start).days + 1)
            ]
            try:
                maps = await open_data.fetch_variable_maps(
                    _dt(gap_start.year, gap_start.month, gap_start.day, tzinfo=_tz.utc),
                    _dt(requested_end.year, requested_end.month, requested_end.day, tzinfo=_tz.utc)
                    + timedelta(days=1) - timedelta(seconds=1),
                    sorted({
                        mc.V_TEMP, mc.V_TEMP_MAX, mc.V_TEMP_MIN, mc.V_PRECIP, mc.V_SOLAR,
                        mc.V_WIND, mc.V_WIND_DIR, mc.V_WIND_6M, mc.V_WIND_DIR_6M,
                        mc.V_WIND_2M, mc.V_WIND_DIR_2M,
                        mc.V_GUST, mc.V_GUST_DIR, mc.V_GUST_6M, mc.V_GUST_DIR_6M,
                        mc.V_GUST_2M, mc.V_GUST_DIR_2M,
                    }),
                    station_id=code,
                    client=client,
                )
            except ProviderError as exc:
                # Sin los últimos días el histórico sigue siendo válido.
                logger.warning("Climo Meteocat: no se pudieron completar %s-%s: %s",
                               gap_start, requested_end, exc.detail)
            else:
                rows_by_day.update(_rows_from_half_hourly(maps.get(code, {}), gap_days))

    return _daily_frame_from_rows(rows_by_day, periods)


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
    """Dataset canónico de Meteocat.

    Primero Dades Obertes, que devuelve días en cualquier modo y sin cuota:
    así también el resumen anual tiene mínima de máximas, máxima de mínimas,
    histograma y distribución, que con ``mensuals``/``anuals`` no se podían
    calcular. La API de Meteocat queda como respaldo si Dades Obertes falla.
    """
    years = [int(y) for y in selected_years]
    try:
        df = await fetch_open_data_daily_for_periods(client, station_code, periods)
    except ProviderError as exc:
        logger.warning(
            "Climo Meteocat: Dades Obertes falló (%s); se recurre a XEMA", exc.detail,
        )
    else:
        # En la comparación de varios años los hitos diarios los calcula el
        # pipeline común con sus ventanas estacionales; aquí sobrarían.
        if summary_mode == "annual" and len(years) > 1:
            return df, None
        extremes = _daily_extremes_from_frame(df)
        return df, (extremes or None)

    _require_api_key(api_key)

    if summary_mode == "annual" and len(years) > 1:
        df = await fetch_annual_history_for_years(client, station_code, api_key, years)
        return df, None

    if summary_mode == "annual" and len(years) == 1:
        # El resumen mensual nativo ya contiene las métricas disponibles para
        # el año. No se fuerzan decenas de consultas diarias únicamente para
        # rellenar hitos ausentes del resumen anual de Meteocat.
        df = await fetch_monthly_history_for_year(client, station_code, api_key, years[0])
        return df, None

    if summary_mode == "monthly":
        # Meteocat publica estadísticas diarias para cada mes solicitado. Las
        # filas mensuales siguen construyéndose después en ``climograms``, de
        # modo que conservar aquí los días alimenta tanto el histograma real
        # como el mismo climograma/resumen mensual de antes.
        df = await fetch_daily_history_for_periods(client, station_code, api_key, periods)
        extremes = _daily_extremes_from_frame(df)
        return df, (extremes or None)

    df = await fetch_daily_history_for_periods(client, station_code, api_key, periods)
    return df, None

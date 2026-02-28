"""
Servicios de climogramas (capa común para todos los proveedores).

Actualmente el fetch de histórico diario está implementado para WU.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import time
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from api import fetch_wu_history_daily
from api.weather_underground import quantize_rain_mm_wu


MONTH_NAMES_ES: Dict[int, str] = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}

MONTH_SHORT_ES: Dict[int, str] = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}

DAILY_SCHEMA = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "gust_max",
    "precip_total",
]


@dataclass(frozen=True)
class ClimogramPeriod:
    label: str
    start: date
    end: date


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _first_valid_float(*values: Any) -> float:
    for value in values:
        number = _safe_float(value)
        if pd.notna(number):
            return float(number)
    return float("nan")


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _parse_obs_date(observation: Dict[str, Any]) -> Optional[pd.Timestamp]:
    local_time = observation.get("obsTimeLocal")
    if isinstance(local_time, str) and len(local_time) >= 10:
        try:
            return pd.to_datetime(local_time[:10], format="%Y-%m-%d", errors="raise")
        except Exception:
            pass

    epoch = observation.get("epoch")
    try:
        epoch_i = int(epoch)
    except Exception:
        epoch_i = 0
    if epoch_i > 0:
        return pd.to_datetime(epoch_i, unit="s")
    return None


def _empty_daily_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_SCHEMA)


def _normalize_wu_daily_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    observations = payload.get("observations", [])
    if not isinstance(observations, list) or not observations:
        return _empty_daily_dataframe()

    rows: List[Dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        timestamp = _parse_obs_date(observation)
        if timestamp is None:
            continue

        metric = observation.get("metric", {})
        if not isinstance(metric, dict):
            metric = {}

        rows.append(
            {
                "date": pd.to_datetime(timestamp).normalize(),
                "epoch": _safe_float(observation.get("epoch")),
                "temp_mean": _first_valid_float(metric.get("tempAvg"), metric.get("temp")),
                "temp_max": _first_valid_float(metric.get("tempHigh"), metric.get("tempMax")),
                "temp_min": _first_valid_float(metric.get("tempLow"), metric.get("tempMin")),
                "wind_mean": _first_valid_float(
                    metric.get("windspeedAvg"),
                    metric.get("windSpeedAvg"),
                    metric.get("windspeed"),
                    metric.get("windSpeed"),
                ),
                "gust_max": _first_valid_float(
                    metric.get("windgustHigh"),
                    metric.get("windGust"),
                    metric.get("windgust"),
                ),
                "precip_total": quantize_rain_mm_wu(
                    _first_valid_float(metric.get("precipTotal"), observation.get("precipTotal"))
                ),
            }
        )

    if not rows:
        return _empty_daily_dataframe()

    frame = pd.DataFrame(rows)
    for column in DAILY_SCHEMA:
        if column not in frame.columns:
            frame[column] = pd.NA

    numeric_columns = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
    if missing_mean.any():
        frame.loc[missing_mean, "temp_mean"] = (frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]) / 2.0

    frame["precip_total"] = frame["precip_total"].clip(lower=0)

    frame["quality"] = frame[numeric_columns[1:]].notna().sum(axis=1)
    frame = (
        frame.sort_values(["date", "quality", "epoch"], ascending=[True, True, True])
        .drop_duplicates(subset=["date"], keep="last")
        .drop(columns=["quality"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[DAILY_SCHEMA]


def _iter_chunks(start_date: date, end_date: date, max_days: int = 31) -> Iterable[Tuple[date, date]]:
    cursor = start_date
    delta_days = int(max_days) - 1
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=delta_days), end_date)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def build_period_specs(
    summary_mode: Literal["Mensual", "Anual"],
    years: Sequence[int],
    months: Optional[Sequence[int]] = None,
) -> List[ClimogramPeriod]:
    valid_years = sorted({int(year) for year in years})
    if not valid_years:
        return []

    periods: List[ClimogramPeriod] = []
    if summary_mode == "Mensual":
        valid_months = sorted({int(month) for month in (months or []) if 1 <= int(month) <= 12})
        for year in valid_years:
            for month in valid_months:
                start = date(year, month, 1)
                end = _last_day_of_month(year, month)
                periods.append(ClimogramPeriod(label=f"{MONTH_NAMES_ES[month]} {year}", start=start, end=end))
    else:
        for year in valid_years:
            periods.append(
                ClimogramPeriod(
                    label=str(year),
                    start=date(year, 1, 1),
                    end=date(year, 12, 31),
                )
            )

    periods.sort(key=lambda period: period.start)
    return periods


def describe_period_range(periods: Sequence[ClimogramPeriod]) -> str:
    if not periods:
        return "—"
    start = min(period.start for period in periods)
    end = max(period.end for period in periods)
    return f"{start.strftime('%d/%m/%Y')} → {end.strftime('%d/%m/%Y')}"


def fetch_wu_daily_history_for_periods(
    station_id: str,
    api_key: str,
    periods: Sequence[ClimogramPeriod],
    ttl_seconds: int = 3 * 3600,
) -> pd.DataFrame:
    if not periods:
        return _empty_daily_dataframe()

    cache = st.session_state.setdefault("wu_cache_history_daily", {})
    now = time.time()
    chunks_data: List[pd.DataFrame] = []

    for period in periods:
        for chunk_start, chunk_end in _iter_chunks(period.start, period.end, max_days=31):
            start_txt = chunk_start.strftime("%Y%m%d")
            end_txt = chunk_end.strftime("%Y%m%d")
            cache_key = (station_id, api_key, start_txt, end_txt)

            payload: Dict[str, Any]
            cached = cache.get(cache_key)
            if isinstance(cached, dict) and (now - float(cached.get("t", 0.0)) < float(ttl_seconds)):
                payload = cached.get("payload", {"observations": []})
            else:
                payload = fetch_wu_history_daily(
                    station_id=station_id,
                    api_key=api_key,
                    start_date=start_txt,
                    end_date=end_txt,
                )
                cache[cache_key] = {"t": now, "payload": payload}

            chunk_df = _normalize_wu_daily_payload(payload)
            if not chunk_df.empty:
                chunks_data.append(chunk_df)

    if not chunks_data:
        return _empty_daily_dataframe()

    all_days = pd.concat(chunks_data, ignore_index=True)
    all_days["date"] = pd.to_datetime(all_days["date"]).dt.normalize()
    all_days = (
        all_days.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return all_days[DAILY_SCHEMA]


def _format_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        dt = pd.to_datetime(value)
    except Exception:
        return "—"
    return dt.strftime("%d/%m/%Y")


def _format_year(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    try:
        dt = pd.to_datetime(value)
        return str(int(dt.year))
    except Exception:
        raw = str(value).strip()
        if len(raw) >= 4 and raw[:4].isdigit():
            return raw[:4]
        return "—"


def _format_value(value: float, unit: str, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{int(decimals)}f} {unit}".strip()


def _extract_extreme(
    df: pd.DataFrame,
    metric: str,
    mode: Literal["max", "min"],
    date_override_col: Optional[str] = None,
) -> Tuple[float, Any, bool]:
    if metric not in df.columns:
        return float("nan"), pd.NaT, False
    series = pd.to_numeric(df[metric], errors="coerce")
    valid = series.dropna()
    if valid.empty:
        return float("nan"), pd.NaT, False
    index = valid.idxmax() if mode == "max" else valid.idxmin()
    day = df.loc[index, "date"]
    override_used = False
    if date_override_col and date_override_col in df.columns:
        override_value = _format_date(df.loc[index, date_override_col])
        if override_value != "—":
            day = df.loc[index, date_override_col]
            override_used = True
    return float(series.loc[index]), day, override_used


def build_extremes_table(
    daily_df: pd.DataFrame,
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["Métrica", "Valor", "Fecha"])

    frame = daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()

    annual_marker_cols = ("temp_abs_max", "temp_abs_min", "rain_days", "solar_mean", "precip_max_24h")
    has_annual_markers = any(
        col in frame.columns and pd.to_numeric(frame[col], errors="coerce").notna().any()
        for col in annual_marker_cols
    )
    looks_yearly_index = (
        len(frame) > 0
        and bool((frame["date"].dt.month == 1).all())
        and bool((frame["date"].dt.day == 1).all())
        and frame["date"].dt.year.nunique(dropna=True) == len(frame)
    )
    is_annual_series = has_annual_markers and looks_yearly_index

    rows: List[Dict[str, str]] = []

    if is_annual_series:
        # ── Serie plurianual (una fila por año) ─────────────────────────────
        extreme_definitions = [
            ("Máxima absoluta", "temp_abs_max", "max", "°C", "temp_abs_max_date"),
            ("Mínima absoluta", "temp_abs_min", "min", "°C", "temp_abs_min_date"),
            ("Año más cálido (temperatura media)", "temp_mean", "max", "°C"),
            ("Año más frío (temperatura media)", "temp_mean", "min", "°C"),
            ("Año más ventoso (viento medio)", "wind_mean", "max", "km/h"),
            ("Racha máxima", "gust_max", "max", "km/h", "gust_abs_max_date"),
            ("Año más lluvioso", "precip_total", "max", "mm"),
            ("Año más seco", "precip_total", "min", "mm"),
            ("Año con más días de lluvia", "rain_days", "max", "días"),
            ("Precipitación máxima en 24 horas", "precip_max_24h", "max", "mm", "precip_max_24h_date"),
            ("Año más soleado", "solar_mean", "max", "h"),
            ("Año con menos sol", "solar_mean", "min", "h"),
        ]
        for row_def in extreme_definitions:
            if len(row_def) == 4:
                title, metric, mode, unit = row_def
                date_override_col = None
            else:
                title, metric, mode, unit, date_override_col = row_def
            value, day, override_used = _extract_extreme(frame, metric, mode, date_override_col=date_override_col)  # type: ignore[arg-type]
            date_txt = _format_year(day) if not override_used else _format_date(day)
            rows.append({"Métrica": title, "Valor": _format_value(value, unit), "Fecha": date_txt})

    else:
        # ── Datos no-anuales: mensuales agregados o diarios ─────────────────
        # Detectar si los datos son mensuales (ej. AEMET "Anual 1 año"):
        # todas las fechas son día 1, una fila por mes.
        is_monthly_indexed = (
            len(frame) > 1
            and bool((frame["date"].dt.day == 1).all())
            and not looks_yearly_index
            and frame["date"].nunique(dropna=True) == len(frame)
        )

        # Ventanas estacionales para métricas calculadas (mín-de-máx, máx-de-mín).
        date_months = frame["date"].dt.month
        warm_min_mask = date_months.isin([5, 6, 7, 8, 9])
        cold_max_mask = date_months.isin([11, 12, 1, 2, 3, 4])
        frame_warm_min = frame.loc[warm_min_mask].copy() if warm_min_mask.any() else frame.copy()
        frame_cold_max = frame.loc[cold_max_mask].copy() if cold_max_mask.any() else frame.copy()

        min_of_max_value, min_of_max_day, _ = _extract_extreme(frame_cold_max, "temp_max", "min")
        max_of_min_value, max_of_min_day, _ = _extract_extreme(frame_warm_min, "temp_min", "max")

        if is_monthly_indexed:
            # ── Datos mensuales agregados (una fila por mes) ─────────────────
            # temp_abs_max/min provienen de ta_max/ta_min de AEMET (valor real del mes).
            # precip_total = total mensual, no diario → usar precip_max_24h para la lluvia extrema.

            # 1. Máxima absoluta
            val, day, _ = _extract_extreme(frame, "temp_abs_max", "max", date_override_col="temp_abs_max_date")
            rows.append({"Métrica": "Máxima absoluta", "Valor": _format_value(val, "°C"), "Fecha": _format_date(day)})

            # 2. Mínima absoluta
            val, day, _ = _extract_extreme(frame, "temp_abs_min", "min", date_override_col="temp_abs_min_date")
            rows.append({"Métrica": "Mínima absoluta", "Valor": _format_value(val, "°C"), "Fecha": _format_date(day)})

            # 3. Mínima de máximas (calculada: mes con menor media de máximas en meses fríos)
            rows.append({
                "Métrica": "Mínima de máximas",
                "Valor": _format_value(min_of_max_value, "°C"),
                "Fecha": _format_date(min_of_max_day),
            })

            # 4. Máxima de mínimas (calculada: mes con mayor media de mínimas en meses cálidos)
            rows.append({
                "Métrica": "Máxima de mínimas",
                "Valor": _format_value(max_of_min_value, "°C"),
                "Fecha": _format_date(max_of_min_day),
            })

            # 5. Mes más ventoso (viento medio calculado)
            val, day, _ = _extract_extreme(frame, "wind_mean", "max")
            rows.append({"Métrica": "Mes más ventoso (viento medio)", "Valor": _format_value(val, "km/h"), "Fecha": _format_date(day)})

            # 6. Racha máxima
            val, day, _ = _extract_extreme(frame, "gust_max", "max", date_override_col="gust_abs_max_date")
            rows.append({"Métrica": "Racha máxima", "Valor": _format_value(val, "km/h"), "Fecha": _format_date(day)})

            # 7. Precipitación máx. en 24h (p_max de AEMET)
            val, day, _ = _extract_extreme(frame, "precip_max_24h", "max", date_override_col="precip_max_24h_date")
            rows.append({"Métrica": "Precipitación máx. en 24h", "Valor": _format_value(val, "mm"), "Fecha": _format_date(day)})

        else:
            # ── Datos diarios (modo Mensual: una fila por día real) ───────────
            # Para AEMET, temp_max/temp_min son los máx/mín diarios (= absolutos del día).
            # temp_abs_max/min solo existe si los datos fueron enriquecidos (WU o cálculo previo).
            abs_max_col = (
                "temp_abs_max"
                if "temp_abs_max" in frame.columns
                and pd.to_numeric(frame["temp_abs_max"], errors="coerce").notna().any()
                else "temp_max"
            )
            abs_min_col = (
                "temp_abs_min"
                if "temp_abs_min" in frame.columns
                and pd.to_numeric(frame["temp_abs_min"], errors="coerce").notna().any()
                else "temp_min"
            )
            abs_max_date_col = "temp_abs_max_date" if abs_max_col == "temp_abs_max" else None
            abs_min_date_col = "temp_abs_min_date" if abs_min_col == "temp_abs_min" else None

            # 1. Máxima absoluta
            val, day, _ = _extract_extreme(frame, abs_max_col, "max", date_override_col=abs_max_date_col)
            rows.append({"Métrica": "Máxima absoluta", "Valor": _format_value(val, "°C"), "Fecha": _format_date(day)})

            # 2. Mínima absoluta
            val, day, _ = _extract_extreme(frame, abs_min_col, "min", date_override_col=abs_min_date_col)
            rows.append({"Métrica": "Mínima absoluta", "Valor": _format_value(val, "°C"), "Fecha": _format_date(day)})

            # 3. Mínima de máximas (calculada)
            rows.append({
                "Métrica": "Mínima de máximas",
                "Valor": _format_value(min_of_max_value, "°C"),
                "Fecha": _format_date(min_of_max_day),
            })

            # 4. Máxima de mínimas (calculada)
            rows.append({
                "Métrica": "Máxima de mínimas",
                "Valor": _format_value(max_of_min_value, "°C"),
                "Fecha": _format_date(max_of_min_day),
            })

            # 5. Día más ventoso (viento medio calculado)
            val, day, _ = _extract_extreme(frame, "wind_mean", "max")
            rows.append({"Métrica": "Día más ventoso (viento medio)", "Valor": _format_value(val, "km/h"), "Fecha": _format_date(day)})

            # 6. Racha máxima
            val, day, _ = _extract_extreme(frame, "gust_max", "max", date_override_col="gust_abs_max_date")
            rows.append({"Métrica": "Racha máxima", "Valor": _format_value(val, "km/h"), "Fecha": _format_date(day)})

            # 7. Día más lluvioso (calculado)
            val, day, _ = _extract_extreme(frame, "precip_total", "max")
            rows.append({"Métrica": "Día más lluvioso", "Valor": _format_value(val, "mm"), "Fecha": _format_date(day)})

    if overrides:
        rows_by_title = {str(row.get("Métrica", "")): idx for idx, row in enumerate(rows)}
        for metric_title, override in overrides.items():
            if not isinstance(override, dict):
                continue
            value_txt = str(override.get("Valor", "—"))
            date_txt = str(override.get("Fecha", "—"))
            if metric_title in rows_by_title:
                idx = rows_by_title[metric_title]
                rows[idx]["Valor"] = value_txt
                rows[idx]["Fecha"] = date_txt
            else:
                rows.append({"Métrica": metric_title, "Valor": value_txt, "Fecha": date_txt})

    return pd.DataFrame(rows, columns=["Métrica", "Valor", "Fecha"])


def build_general_metrics_table(daily_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["Métrica", "Valor"])

    frame = daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    temp_mean_series = pd.to_numeric(frame["temp_mean"], errors="coerce")
    temp_max_series = pd.to_numeric(frame["temp_max"], errors="coerce")
    temp_min_series = pd.to_numeric(frame["temp_min"], errors="coerce")
    wind_mean_series = pd.to_numeric(frame["wind_mean"], errors="coerce")
    precip_series = pd.to_numeric(frame["precip_total"], errors="coerce")

    # Datos solares: preferir solar_mean (MJ/m² o horas según fuente),
    # y como alternativa solar_hours (horas de sol del endpoint diario AEMET).
    solar_mean_series = (
        pd.to_numeric(frame["solar_mean"], errors="coerce")
        if "solar_mean" in frame.columns
        else pd.Series(dtype=float)
    )
    solar_hours_series = (
        pd.to_numeric(frame["solar_hours"], errors="coerce")
        if "solar_hours" in frame.columns
        else pd.Series(dtype=float)
    )

    unique_years = frame["date"].dt.year.nunique(dropna=True)

    rows = [
        {"Métrica": "Temperatura media", "Valor": _format_value(temp_mean_series.mean(), "°C", decimals=1)},
        {"Métrica": "Media de máximas", "Valor": _format_value(temp_max_series.mean(), "°C", decimals=1)},
        {"Métrica": "Media de mínimas", "Valor": _format_value(temp_min_series.mean(), "°C", decimals=1)},
        {"Métrica": "Desv. estándar de temperatura", "Valor": _format_value(temp_mean_series.std(ddof=0), "°C", decimals=1)},
        {"Métrica": "Media de viento", "Valor": _format_value(wind_mean_series.mean(), "km/h", decimals=1)},
        {"Métrica": "Precipitación acumulada", "Valor": _format_value(precip_series.sum(min_count=1), "mm", decimals=1)},
    ]
    if int(unique_years) > 1:
        rows.insert(
            5,
            {"Métrica": "Media de precipitación", "Valor": _format_value(precip_series.mean(), "mm", decimals=1)},
        )

    # Solo añadir filas solares si hay datos reales (sin fallback a "—" vacío).
    if solar_mean_series.notna().any():
        rows.append({"Métrica": "Irradiancia solar media", "Valor": _format_value(solar_mean_series.mean(), "h", decimals=1)})
    elif solar_hours_series.notna().any():
        # Endpoint diario AEMET: campo 'sol' = horas de sol del día
        rows.append({"Métrica": "Horas de sol (media)", "Valor": _format_value(solar_hours_series.mean(), "h", decimals=1)})

    # ── Noches tropicales / helada ─────────────────────────────────────────
    # Detectar si los datos son mensuales (día 1 de cada mes) o diarios.
    _looks_yearly = (
        len(frame) > 0
        and bool((frame["date"].dt.month == 1).all())
        and bool((frame["date"].dt.day == 1).all())
        and frame["date"].dt.year.nunique(dropna=True) == len(frame)
    )
    _is_monthly = (
        len(frame) > 1
        and bool((frame["date"].dt.day == 1).all())
        and not _looks_yearly
        and frame["date"].nunique(dropna=True) == len(frame)
    )

    if _is_monthly:
        # Datos mensuales: usar columnas nt_30 / nt_00 del proveedor (suma real de noches).
        if "tropical_nights" in frame.columns:
            tn_s = pd.to_numeric(frame["tropical_nights"], errors="coerce")
            if tn_s.notna().any():
                rows.append({"Métrica": "Noches tropicales (mín > 20 °C)", "Valor": f"{int(tn_s.sum(min_count=1))} noches"})
        if "frost_nights" in frame.columns:
            fn_s = pd.to_numeric(frame["frost_nights"], errors="coerce")
            if fn_s.notna().any():
                rows.append({"Métrica": "Noches de helada (mín ≤ 0 °C)", "Valor": f"{int(fn_s.sum(min_count=1))} noches"})
    elif not _looks_yearly:
        # Datos diarios: calcular directamente desde temp_min.
        if temp_min_series.notna().any():
            n_tropical = int((temp_min_series > 20.0).sum())
            n_torrid = int((temp_min_series > 25.0).sum())
            rows.append({"Métrica": "Noches tropicales (mín > 20 °C)", "Valor": f"{n_tropical} noches"})
            rows.append({"Métrica": "Noches tórridas (mín > 25 °C)", "Valor": f"{n_torrid} noches"})

    return pd.DataFrame(rows, columns=["Métrica", "Valor"])


def resolve_chart_granularity(summary_mode: Literal["Mensual", "Anual"], period_count: int) -> Literal["daily", "monthly", "yearly"]:
    if summary_mode == "Mensual":
        return "daily" if int(period_count) == 1 else "monthly"
    if int(period_count) <= 1:
        return "monthly"
    return "yearly"


def _sum_with_min_count(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").sum(min_count=1))


def build_chart_table(
    daily_df: pd.DataFrame,
    granularity: Literal["daily", "monthly", "yearly"],
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["label", "sort_key", "temp_mean", "temp_max", "temp_min", "precip_total"])

    frame = daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()

    if granularity == "daily":
        grouped = (
            frame.groupby("date", as_index=False)
            .agg(
                temp_mean=("temp_mean", "mean"),
                temp_max=("temp_max", "mean"),
                temp_min=("temp_min", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("date")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = grouped["date"]
        grouped["label"] = grouped["date"].dt.strftime("%d/%m")
    elif granularity == "monthly":
        frame["month_start"] = frame["date"].dt.to_period("M").dt.to_timestamp()
        grouped = (
            frame.groupby("month_start", as_index=False)
            .agg(
                temp_mean=("temp_mean", "mean"),
                temp_max=("temp_max", "mean"),
                temp_min=("temp_min", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("month_start")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = grouped["month_start"]
        grouped["label"] = grouped["month_start"].apply(
            lambda d: f"{MONTH_SHORT_ES[int(pd.Timestamp(d).month)]} {int(pd.Timestamp(d).year)}"
        )
    else:
        frame["year"] = frame["date"].dt.year
        grouped = (
            frame.groupby("year", as_index=False)
            .agg(
                temp_mean=("temp_mean", "mean"),
                temp_max=("temp_max", "mean"),
                temp_min=("temp_min", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("year")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = pd.to_datetime(grouped["year"].astype(str) + "-01-01")
        grouped["label"] = grouped["year"].astype(str)

    grouped = grouped.fillna(value={"temp_mean": float("nan"), "temp_max": float("nan"), "temp_min": float("nan"), "precip_total": float("nan")})
    return grouped[["label", "sort_key", "temp_mean", "temp_max", "temp_min", "precip_total"]]


def build_units_table(
    daily_df: pd.DataFrame,
    granularity: Literal["daily", "monthly", "yearly"],
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["label", "sort_key", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"])

    frame = daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["label", "sort_key", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"])

    abs_max_source = "temp_abs_max" if ("temp_abs_max" in frame.columns and pd.to_numeric(frame["temp_abs_max"], errors="coerce").notna().any()) else "temp_max"
    abs_min_source = "temp_abs_min" if ("temp_abs_min" in frame.columns and pd.to_numeric(frame["temp_abs_min"], errors="coerce").notna().any()) else "temp_min"

    if granularity == "daily":
        grouped = (
            frame.groupby("date", as_index=False)
            .agg(
                temp_abs_max=(abs_max_source, "max"),
                temp_abs_min=(abs_min_source, "min"),
                temp_mean=("temp_mean", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("date")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = grouped["date"]
        grouped["label"] = grouped["date"].dt.strftime("%d/%m")
    elif granularity == "monthly":
        frame["month_start"] = frame["date"].dt.to_period("M").dt.to_timestamp()
        grouped = (
            frame.groupby("month_start", as_index=False)
            .agg(
                temp_abs_max=(abs_max_source, "max"),
                temp_abs_min=(abs_min_source, "min"),
                temp_mean=("temp_mean", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("month_start")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = grouped["month_start"]
        grouped["label"] = grouped["month_start"].apply(
            lambda d: f"{MONTH_SHORT_ES[int(pd.Timestamp(d).month)]} {int(pd.Timestamp(d).year)}"
        )
    else:
        frame["year"] = frame["date"].dt.year
        grouped = (
            frame.groupby("year", as_index=False)
            .agg(
                temp_abs_max=(abs_max_source, "max"),
                temp_abs_min=(abs_min_source, "min"),
                temp_mean=("temp_mean", "mean"),
                precip_total=("precip_total", _sum_with_min_count),
            )
            .sort_values("year")
            .reset_index(drop=True)
        )
        grouped["sort_key"] = pd.to_datetime(grouped["year"].astype(str) + "-01-01")
        grouped["label"] = grouped["year"].astype(str)

    grouped = grouped.fillna(
        value={
            "temp_abs_max": float("nan"),
            "temp_abs_min": float("nan"),
            "temp_mean": float("nan"),
            "precip_total": float("nan"),
        }
    )
    return grouped[["label", "sort_key", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"]]

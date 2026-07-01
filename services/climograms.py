"""
Servicios de climogramas (capa común para todos los proveedores).

La obtención de datos vive en FastAPI; este módulo solo prepara tablas para UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import pandas as pd
from utils.i18n import month_name, t
from utils.units import (
    convert_precip,
    convert_temperature,
    convert_wind,
    format_precip,
    format_temperature,
    format_wind,
    normalize_unit_preferences,
)


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

METRIC_LABEL_KEYS = {
    "Máxima absoluta": "historical.metrics.absolute_max",
    "Mínima absoluta": "historical.metrics.absolute_min",
    "Año más cálido (temperatura media)": "historical.metrics.warmest_year",
    "Año más frío (temperatura media)": "historical.metrics.coldest_year",
    "Año más ventoso (viento medio)": "historical.metrics.windiest_year",
    "Racha máxima": "historical.metrics.max_gust",
    "Año más lluvioso": "historical.metrics.wettest_year",
    "Año más seco": "historical.metrics.driest_year",
    "Año con más días de lluvia": "historical.metrics.most_rain_days_year",
    "Precipitación máxima en 24 horas": "historical.metrics.max_precip_24h",
    "Año más soleado": "historical.metrics.sunniest_year",
    "Año con menos sol": "historical.metrics.least_sunny_year",
    "Mínima de máximas": "historical.metrics.lowest_maximum",
    "Máxima de mínimas": "historical.metrics.highest_minimum",
    "Mes más ventoso (viento medio)": "historical.metrics.windiest_month",
    "Precipitación máx. en 24h": "historical.metrics.max_precip_24h_short",
    "Día más ventoso (viento medio)": "historical.metrics.windiest_day",
    "Día más lluvioso": "historical.metrics.rainiest_day",
    "Temperatura media": "historical.metrics.mean_temperature",
    "Media de máximas": "historical.metrics.mean_maximums",
    "Media de mínimas": "historical.metrics.mean_minimums",
    "Desv. estándar de temperatura": "historical.metrics.temperature_stddev",
    "Media de viento": "historical.metrics.mean_wind",
    "Precipitación acumulada": "historical.metrics.accumulated_precipitation",
    "Media de precipitación": "historical.metrics.mean_precipitation",
    "Irradiancia solar media": "historical.metrics.mean_solar_irradiance",
    "Horas de sol (media)": "historical.metrics.mean_sunshine_hours",
    "Noches tropicales (mín > 20 °C)": "historical.metrics.tropical_nights",
    "Noches de helada (mín ≤ 0 °C)": "historical.metrics.frost_nights",
    "Noches tórridas (mín > 25 °C)": "historical.metrics.torrid_nights",
}


def _table_columns() -> list[str]:
    return [_table_column_name("metric"), _table_column_name("value"), _table_column_name("date")]


def _table_column_name(name: str) -> str:
    return t(f"historical.table.headers.{name}")


def _metric_label(label: str) -> str:
    return t(METRIC_LABEL_KEYS.get(str(label).strip(), ""), default=str(label))


def _table_row(metric: str, value: str, date_txt: str | None = None) -> dict[str, str]:
    row = {
        _table_column_name("metric"): _metric_label(metric),
        _table_column_name("value"): value,
    }
    if date_txt is not None:
        row[_table_column_name("date")] = date_txt
    return row


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


def clip_periods_to_today(
    periods: Sequence[ClimogramPeriod],
    *,
    today_date: Optional[date] = None,
) -> List[ClimogramPeriod]:
    today = today_date or date.today()
    clipped: List[ClimogramPeriod] = []
    for period in periods:
        if period.start > today:
            continue
        clipped.append(
            ClimogramPeriod(
                label=period.label,
                start=period.start,
                end=min(period.end, today),
            )
        )
    return clipped


def build_period_specs(
    summary_mode: Literal["monthly", "annual"],
    years: Sequence[int],
    months: Optional[Sequence[int]] = None,
) -> List[ClimogramPeriod]:
    valid_years = sorted({int(year) for year in years})
    if not valid_years:
        return []

    periods: List[ClimogramPeriod] = []
    if summary_mode == "monthly":
        valid_months = sorted({int(month) for month in (months or []) if 1 <= int(month) <= 12})
        for year in valid_years:
            for month in valid_months:
                start = date(year, month, 1)
                end = _last_day_of_month(year, month)
                periods.append(ClimogramPeriod(label=f"{month_name(month)} {year}", start=start, end=end))
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


def _format_temperature_value(value: float, unit_preferences: Optional[Dict[str, str]], decimals: int = 1) -> str:
    prefs = normalize_unit_preferences(unit_preferences)
    return f"{format_temperature(value, prefs['temperature'], decimals=decimals)} {prefs['temperature'] == 'k' and 'K' or ('°F' if prefs['temperature'] == 'f' else '°C')}".strip()


def _format_wind_value(value: float, unit_preferences: Optional[Dict[str, str]], decimals: int = 1) -> str:
    prefs = normalize_unit_preferences(unit_preferences)
    unit = {"kmh": "km/h", "ms": "m/s", "mph": "mph", "kt": "kt"}[prefs["wind"]]
    return f"{format_wind(value, prefs['wind'], decimals=decimals)} {unit}".strip()


def _format_precip_value(value: float, unit_preferences: Optional[Dict[str, str]], decimals: int = 1) -> str:
    prefs = normalize_unit_preferences(unit_preferences)
    unit = {"mm": "mm", "in": "in"}[prefs["precip"]]
    return f"{format_precip(value, prefs['precip'], decimals=decimals)} {unit}".strip()


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
    unit_preferences: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=_table_columns())

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
            ("Año con más días de lluvia", "rain_days", "max", t("historical.units.days")),
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
            if unit == "°C":
                value_txt = _format_temperature_value(value, unit_preferences)
            elif unit == "km/h":
                value_txt = _format_wind_value(value, unit_preferences)
            elif unit == "mm":
                value_txt = _format_precip_value(value, unit_preferences)
            else:
                value_txt = _format_value(value, unit)
            rows.append(_table_row(title, value_txt, date_txt))

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
            rows.append(_table_row("Máxima absoluta", _format_temperature_value(val, unit_preferences), _format_date(day)))

            # 2. Mínima absoluta
            val, day, _ = _extract_extreme(frame, "temp_abs_min", "min", date_override_col="temp_abs_min_date")
            rows.append(_table_row("Mínima absoluta", _format_temperature_value(val, unit_preferences), _format_date(day)))

            # 3. Mínima de máximas (calculada: mes con menor media de máximas en meses fríos)
            rows.append(_table_row("Mínima de máximas", _format_temperature_value(min_of_max_value, unit_preferences), _format_date(min_of_max_day)))

            # 4. Máxima de mínimas (calculada: mes con mayor media de mínimas en meses cálidos)
            rows.append(_table_row("Máxima de mínimas", _format_temperature_value(max_of_min_value, unit_preferences), _format_date(max_of_min_day)))

            # 5. Mes más ventoso (viento medio calculado)
            val, day, _ = _extract_extreme(frame, "wind_mean", "max")
            rows.append(_table_row("Mes más ventoso (viento medio)", _format_wind_value(val, unit_preferences), _format_date(day)))

            # 6. Racha máxima
            val, day, _ = _extract_extreme(frame, "gust_max", "max", date_override_col="gust_abs_max_date")
            rows.append(_table_row("Racha máxima", _format_wind_value(val, unit_preferences), _format_date(day)))

            # 7. Precipitación máx. en 24h (p_max de AEMET)
            val, day, _ = _extract_extreme(frame, "precip_max_24h", "max", date_override_col="precip_max_24h_date")
            rows.append(_table_row("Precipitación máx. en 24h", _format_precip_value(val, unit_preferences), _format_date(day)))

        else:
            # ── Datos diarios (modo Mensual: una fila por día real) ───────────
            # Para AEMET, temp_max/temp_min son los máx/mín diarios (= absolutos del día).
            # temp_abs_max/min solo existe si el dataset fue enriquecido previamente.
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
            rows.append(_table_row("Máxima absoluta", _format_temperature_value(val, unit_preferences), _format_date(day)))

            # 2. Mínima absoluta
            val, day, _ = _extract_extreme(frame, abs_min_col, "min", date_override_col=abs_min_date_col)
            rows.append(_table_row("Mínima absoluta", _format_temperature_value(val, unit_preferences), _format_date(day)))

            # 3. Mínima de máximas (calculada)
            rows.append(_table_row("Mínima de máximas", _format_temperature_value(min_of_max_value, unit_preferences), _format_date(min_of_max_day)))

            # 4. Máxima de mínimas (calculada)
            rows.append(_table_row("Máxima de mínimas", _format_temperature_value(max_of_min_value, unit_preferences), _format_date(max_of_min_day)))

            # 5. Día más ventoso (viento medio calculado)
            val, day, _ = _extract_extreme(frame, "wind_mean", "max")
            rows.append(_table_row("Día más ventoso (viento medio)", _format_wind_value(val, unit_preferences), _format_date(day)))

            # 6. Racha máxima
            val, day, _ = _extract_extreme(frame, "gust_max", "max", date_override_col="gust_abs_max_date")
            rows.append(_table_row("Racha máxima", _format_wind_value(val, unit_preferences), _format_date(day)))

            # 7. Día más lluvioso (calculado)
            val, day, _ = _extract_extreme(frame, "precip_total", "max")
            rows.append(_table_row("Día más lluvioso", _format_precip_value(val, unit_preferences), _format_date(day)))

    if overrides:
        rows_by_title = {str(row.get(_table_column_name("metric"), "")): idx for idx, row in enumerate(rows)}
        for metric_title, override in overrides.items():
            if not isinstance(override, dict):
                continue
            value_txt = str(override.get("Valor", "—"))
            date_txt = str(override.get("Fecha", "—"))
            localized_metric_title = _metric_label(metric_title)
            try:
                if value_txt.endswith(" °C"):
                    value_txt = _format_temperature_value(float(value_txt.replace(" °C", "").strip()), unit_preferences)
                elif value_txt.endswith(" km/h"):
                    value_txt = _format_wind_value(float(value_txt.replace(" km/h", "").strip()), unit_preferences)
                elif value_txt.endswith(" mm"):
                    value_txt = _format_precip_value(float(value_txt.replace(" mm", "").strip()), unit_preferences)
            except Exception:
                pass
            if localized_metric_title in rows_by_title:
                idx = rows_by_title[localized_metric_title]
                rows[idx][_table_column_name("value")] = value_txt
                rows[idx][_table_column_name("date")] = date_txt
            else:
                rows.append(_table_row(metric_title, value_txt, date_txt))

    return pd.DataFrame(rows, columns=_table_columns())


def build_general_metrics_table(
    daily_df: pd.DataFrame,
    unit_preferences: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=[_table_column_name("metric"), _table_column_name("value")])

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

    # Para "Media de precipitación" queremos la precipitación MENSUAL media
    # (cuánto llueve, en promedio, en uno de los meses seleccionados), NO la
    # diaria. Antes se hacía ``precip_series.mean()`` sobre el dataframe
    # diario y eso devolvía mm/día (~2 mm), lo que parecía absurdamente bajo
    # cuando se acumulaban muchos abriles. Ahora agrupamos por (año, mes),
    # sumamos la precipitación de cada (año, mes) y promediamos esas sumas.
    monthly_precip_totals = precip_series.groupby(
        [frame["date"].dt.year, frame["date"].dt.month]
    ).sum(min_count=1)
    mean_monthly_precip = monthly_precip_totals.mean()

    rows = [
        _table_row("Temperatura media", _format_temperature_value(temp_mean_series.mean(), unit_preferences, decimals=1), None),
        _table_row("Media de máximas", _format_temperature_value(temp_max_series.mean(), unit_preferences, decimals=1), None),
        _table_row("Media de mínimas", _format_temperature_value(temp_min_series.mean(), unit_preferences, decimals=1), None),
        _table_row("Desv. estándar de temperatura", _format_temperature_value(temp_mean_series.std(ddof=0), unit_preferences, decimals=1), None),
        _table_row("Media de viento", _format_wind_value(wind_mean_series.mean(), unit_preferences, decimals=1), None),
        _table_row("Precipitación acumulada", _format_precip_value(precip_series.sum(min_count=1), unit_preferences, decimals=1), None),
    ]
    if int(unique_years) > 1:
        rows.insert(
            5,
            _table_row(
                "Media de precipitación",
                _format_precip_value(mean_monthly_precip, unit_preferences, decimals=1),
                None,
            ),
        )

    # Solo añadir filas solares si hay datos reales (sin fallback a "—" vacío).
    if solar_mean_series.notna().any():
        rows.append(_table_row("Irradiancia solar media", _format_value(solar_mean_series.mean(), "h", decimals=1), None))
    elif solar_hours_series.notna().any():
        # Endpoint diario AEMET: campo 'sol' = horas de sol del día
        rows.append(_table_row("Horas de sol (media)", _format_value(solar_hours_series.mean(), "h", decimals=1), None))

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
                rows.append(_table_row("Noches tropicales (mín > 20 °C)", f"{int(tn_s.sum(min_count=1))} {t('historical.units.nights')}", None))
        if "frost_nights" in frame.columns:
            fn_s = pd.to_numeric(frame["frost_nights"], errors="coerce")
            if fn_s.notna().any():
                rows.append(_table_row("Noches de helada (mín ≤ 0 °C)", f"{int(fn_s.sum(min_count=1))} {t('historical.units.nights')}", None))
    elif not _looks_yearly:
        # Datos diarios: calcular directamente desde temp_min.
        if temp_min_series.notna().any():
            # Noche tropical = la mínima NO baja de 20 °C (≥ 20); tórrida ≥ 25 °C.
            # Inclusivo en el umbral (definición AEMET/OMM): 20,0 cuenta.
            n_tropical = int((temp_min_series >= 20.0).sum())
            n_torrid = int((temp_min_series >= 25.0).sum())
            rows.append(_table_row("Noches tropicales (mín > 20 °C)", f"{n_tropical} {t('historical.units.nights')}", None))
            rows.append(_table_row("Noches tórridas (mín > 25 °C)", f"{n_torrid} {t('historical.units.nights')}", None))

    return pd.DataFrame(rows, columns=[_table_column_name("metric"), _table_column_name("value")])


def resolve_chart_granularity(summary_mode: Literal["monthly", "annual"], period_count: int) -> Literal["daily", "monthly", "yearly"]:
    if summary_mode == "monthly":
        return "daily" if int(period_count) == 1 else "monthly"
    if int(period_count) <= 1:
        return "monthly"
    return "yearly"


def _sum_with_min_count(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").sum(min_count=1))


def build_chart_table(
    daily_df: pd.DataFrame,
    granularity: Literal["daily", "monthly", "yearly"],
    unit_preferences: Optional[Dict[str, str]] = None,
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
            lambda d: f"{month_name(int(pd.Timestamp(d).month), short=True)} {int(pd.Timestamp(d).year)}"
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

    prefs = normalize_unit_preferences(unit_preferences)
    for column in ("temp_mean", "temp_max", "temp_min"):
        grouped[column] = grouped[column].apply(
            lambda value: convert_temperature(value, prefs["temperature"]) if not pd.isna(value) else float("nan")
        )
    grouped["precip_total"] = grouped["precip_total"].apply(
        lambda value: convert_precip(value, prefs["precip"]) if not pd.isna(value) else float("nan")
    )
    grouped = grouped.fillna(value={"temp_mean": float("nan"), "temp_max": float("nan"), "temp_min": float("nan"), "precip_total": float("nan")})
    return grouped[["label", "sort_key", "temp_mean", "temp_max", "temp_min", "precip_total"]]


def build_units_table(
    daily_df: pd.DataFrame,
    granularity: Literal["daily", "monthly", "yearly"],
    unit_preferences: Optional[Dict[str, str]] = None,
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
            lambda d: f"{month_name(int(pd.Timestamp(d).month), short=True)} {int(pd.Timestamp(d).year)}"
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

    prefs = normalize_unit_preferences(unit_preferences)
    for column in ("temp_abs_max", "temp_abs_min", "temp_mean"):
        grouped[column] = grouped[column].apply(
            lambda value: convert_temperature(value, prefs["temperature"]) if not pd.isna(value) else float("nan")
        )
    grouped["precip_total"] = grouped["precip_total"].apply(
        lambda value: convert_precip(value, prefs["precip"]) if not pd.isna(value) else float("nan")
    )

    grouped = grouped.fillna(
        value={
            "temp_abs_max": float("nan"),
            "temp_abs_min": float("nan"),
            "temp_mean": float("nan"),
            "precip_total": float("nan"),
        }
    )
    return grouped[["label", "sort_key", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total"]]

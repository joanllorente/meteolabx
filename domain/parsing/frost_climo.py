"""
Parsing puro de las normales climáticas de Frost (frost.met.no).

Módulo de dominio sin ``streamlit`` ni transporte:
transforma los payloads de ``/climatenormals/available/v0.jsonld`` y
``/climatenormals/v0.jsonld`` al esquema de columnas común de
climogramas consumido por ``server/services/frost_climo.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

# Campo del esquema común → elementId de normales mensuales.
CLIMO_MONTHLY_ELEMENT_MAP: Dict[str, str] = {
    "temp_mean": "mean(air_temperature P1M)",
    "temp_max": "mean(max(air_temperature P1D) P1M)",
    "temp_min": "mean(min(air_temperature P1D) P1M)",
    "precip_total": "sum(precipitation_amount P1M)",
    "rain_days": "number_of_days_gte(sum(precipitation_amount P1D) P1M 1.0)",
    "solar_hours": "sum(duration_of_sunshine P1M)",
}

# Campo del esquema común → elementId de normales anuales.
CLIMO_YEARLY_ELEMENT_MAP: Dict[str, str] = {
    "temp_mean": "mean(air_temperature P1Y)",
    "temp_max": "mean(max(air_temperature P1D) P1Y)",
    "temp_min": "mean(min(air_temperature P1D) P1Y)",
    "precip_total": "sum(precipitation_amount P1Y)",
    "rain_days": "number_of_days_gte(sum(precipitation_amount P1D) P1Y 1.0)",
    "solar_hours": "sum(duration_of_sunshine P1Y)",
}

_CLIMO_COLS = [
    "date", "epoch", "temp_mean", "temp_max", "temp_min",
    "wind_mean", "wind_dir_mean", "gust_max", "precip_total",
    "solar_hours", "rain_days", "period_label",
]


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def empty_climo_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_CLIMO_COLS)


def parse_available_climo_elements(payload: Any) -> Dict[str, List[str]]:
    """``/climatenormals/available`` → {periodo: [elementIds disponibles]}."""
    out: Dict[str, List[str]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period", "")).strip()
        element_id = str(item.get("elementId", "")).strip()
        if not period or not element_id:
            continue
        bucket = out.setdefault(period, [])
        if element_id not in bucket:
            bucket.append(element_id)
    return out


def climo_value_map(payload: Any) -> Dict[Tuple[str, Optional[int]], float]:
    """``/climatenormals`` → {(elementId, mes|None): valor normal}."""
    values: Dict[Tuple[str, Optional[int]], float] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("elementId", "")).strip()
        month_raw = item.get("month")
        try:
            month = int(month_raw) if month_raw is not None else None
        except Exception:
            month = None
        normal = _safe_float(item.get("normal"))
        if _is_nan(normal):
            continue
        values[(element_id, month)] = float(normal)
    return values


def period_anchor_year(period_label: str) -> int:
    """Año de anclaje del periodo ("1991/2020" → 2020) para el eje fecha."""
    parts = str(period_label or "").split("/")
    for part in reversed(parts):
        try:
            return int(part)
        except Exception:
            continue
    return 2000


def _empty_row(date_ts: pd.Timestamp, period: str) -> Dict[str, Any]:
    return {
        "date": date_ts,
        "epoch": 0.0,
        "temp_mean": float("nan"),
        "temp_max": float("nan"),
        "temp_min": float("nan"),
        "wind_mean": float("nan"),
        "wind_dir_mean": float("nan"),
        "gust_max": float("nan"),
        "precip_total": float("nan"),
        "solar_hours": float("nan"),
        "rain_days": float("nan"),
        "period_label": period,
    }


def build_monthly_rows(
    period: str,
    months: Sequence[int],
    available_set: set,
    value_map: Dict[Tuple[str, Optional[int]], float],
) -> List[Dict[str, Any]]:
    """Filas mensuales del esquema común para un periodo de normales."""
    anchor_year = period_anchor_year(period)
    rows: List[Dict[str, Any]] = []
    for month in months:
        row = _empty_row(pd.Timestamp(year=anchor_year, month=int(month), day=1), period)
        for field, element_id in CLIMO_MONTHLY_ELEMENT_MAP.items():
            if element_id not in available_set:
                continue
            value = value_map.get((element_id, int(month)))
            if value is None:
                continue
            row[field] = float(value)
        rows.append(row)
    return rows


def build_yearly_row(
    period: str,
    available_set: set,
    value_map: Dict[Tuple[str, Optional[int]], float],
) -> Dict[str, Any]:
    """Fila anual del esquema común para un periodo de normales."""
    anchor_year = period_anchor_year(period)
    row = _empty_row(pd.Timestamp(year=anchor_year, month=1, day=1), period)
    for field, element_id in CLIMO_YEARLY_ELEMENT_MAP.items():
        if element_id not in available_set:
            continue
        value = value_map.get((element_id, None))
        if value is None:
            continue
        row[field] = float(value)
    return row


def rows_to_climo_df(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return empty_climo_df()
    return pd.DataFrame(list(rows)).sort_values("date").reset_index(drop=True)


def _period_sort_key(period_text: str) -> Tuple[int, int]:
    parts = str(period_text).split("/")
    try:
        start_year = int(parts[0])
    except Exception:
        start_year = -1
    try:
        end_year = int(parts[1])
    except Exception:
        end_year = -1
    return (start_year, end_year)


def build_period_options(available: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Desde ``{periodo: [elementIds disponibles]}`` decide qué periodos de
    normales tienen datos para los climogramas mensual / anual y los
    devuelve ordenados: ``{"monthly": [...], "annual": [...]}``.

    Un periodo entra en ``monthly``/``annual`` si publica AL MENOS un
    elemento del mapa mensual/anual respectivo (misma regla que el
    selector de la UI).
    """
    monthly_required = set(CLIMO_MONTHLY_ELEMENT_MAP.values())
    yearly_required = set(CLIMO_YEARLY_ELEMENT_MAP.values())
    monthly_periods: List[str] = []
    yearly_periods: List[str] = []
    for period, elements in (available or {}).items():
        element_set = set(elements)
        if element_set & monthly_required:
            monthly_periods.append(period)
        if element_set & yearly_required:
            yearly_periods.append(period)
    monthly_periods.sort(key=_period_sort_key)
    yearly_periods.sort(key=_period_sort_key)
    return {"monthly": monthly_periods, "annual": yearly_periods}

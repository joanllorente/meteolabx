"""
Parsing puro del archivo climatológico de GeoSphere Austria (klima-v2).

Módulo de dominio sin ``streamlit`` ni ``httpx``: transforma respuestas
del Data Hub (``timestamps`` + ``parameters.<nombre>.data``) al esquema
de columnas común de climogramas. El ensamblado a DataFrame reutiliza
los assemblers genéricos de ``meteogalicia_climo`` (mismo contrato).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from domain.parsing.meteogalicia_climo import (  # assemblers genéricos
    aggregate_monthly_rows_to_year,
    empty_climo_df,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)

__all__ = [
    "DAILY_PARAM_MAP",
    "MONTHLY_PARAM_MAP",
    "aggregate_monthly_rows_to_year",
    "empty_climo_df",
    "extract_climo_rows",
    "rows_to_climo_df",
    "yearly_rows_to_climo_df",
]

# Parámetro klima-v2-1d → (campo del esquema, factor de unidades).
DAILY_PARAM_MAP: Dict[str, Tuple[str, float]] = {
    "tl_mittel": ("temp_mean", 1.0),
    "tlmax": ("temp_max", 1.0),
    "tlmin": ("temp_min", 1.0),
    "rr": ("precip_total", 1.0),
    "vv_mittel": ("wind_mean", 3.6),   # m/s → km/h
    "ffx": ("gust_max", 3.6),
    "so_h": ("solar_hours", 1.0),
}

# Parámetro klima-v2-1m → (campo del esquema, factor). Misma convención
# que MeteoGalicia: temp_max/min mensuales = media de máximas/mínimas;
# las absolutas del mes van a temp_abs_max/min. Sin ráfaga mensual.
MONTHLY_PARAM_MAP: Dict[str, Tuple[str, float]] = {
    "tl_mittel": ("temp_mean", 1.0),
    "tlmax_mittel": ("temp_max", 1.0),
    "tlmin_mittel": ("temp_min", 1.0),
    "tlmax": ("temp_abs_max", 1.0),
    "tlmin": ("temp_abs_min", 1.0),
    "rr": ("precip_total", 1.0),
    "rr_max": ("precip_max_24h", 1.0),
    "vv_mittel": ("wind_mean", 3.6),
    "so_h": ("solar_hours", 1.0),
    "tage_rr_01": ("rain_days", 1.0),
}


def _safe_float(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def extract_climo_rows(
    payload: Any,
    param_map: Dict[str, Tuple[str, float]],
) -> List[Dict[str, Any]]:
    """Respuesta del Data Hub → filas {date, <campos del esquema>}.

    El archivo usa ``-1.0`` en la precipitación como "sin precipitación
    medible" (traza): se recorta a 0. El resto de nulos quedan NaN.
    """
    if not isinstance(payload, dict):
        return []
    timestamps = payload.get("timestamps")
    features = payload.get("features")
    if not isinstance(timestamps, list) or not isinstance(features, list) or not features:
        return []
    properties = features[0].get("properties") if isinstance(features[0], dict) else None
    parameters = properties.get("parameters") if isinstance(properties, dict) else None
    if not isinstance(parameters, dict):
        return []

    columns: Dict[str, Tuple[List[Any], float]] = {}
    for name, (field, factor) in param_map.items():
        block = parameters.get(name)
        values = block.get("data") if isinstance(block, dict) else None
        if isinstance(values, list):
            columns[field] = (values, factor)

    rows: List[Dict[str, Any]] = []
    for idx, timestamp in enumerate(timestamps):
        date_text = str(timestamp or "")[:10]
        if not date_text:
            continue
        try:
            epoch = int(
                datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc).timestamp()
            )
        except ValueError:
            continue
        row: Dict[str, Any] = {"date": date_text, "epoch": epoch}
        has_value = False
        for field, (values, factor) in columns.items():
            value = _safe_float(values[idx]) if idx < len(values) else float("nan")
            if value == value:
                if field.startswith("precip") and value < 0.0:
                    value = 0.0
                row[field] = value * factor
                has_value = True
            else:
                row[field] = float("nan")
        if has_value:
            rows.append(row)
    return rows

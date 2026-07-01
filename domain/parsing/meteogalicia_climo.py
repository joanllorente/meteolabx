"""
Parsing puro de los endpoints climo de MeteoGalicia (diario/mensual).

Módulo de dominio sin ``streamlit`` ni ``requests``/``httpx``:
solo transforma payloads JSON ya descargados al esquema de columnas
común de climogramas consumido por
``server/services/meteogalicia_climo.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

# Esquema de columnas del DataFrame devuelto (compatible con climograms.py).
CLIMO_DAILY_COLS = [
    "date", "epoch", "temp_mean", "temp_max", "temp_min",
    "wind_mean", "wind_dir_mean", "gust_max", "precip_total",
]
CLIMO_EXTRA_COLS = [
    "solar_hours", "precip_max_24h", "rain_days",
    "temp_abs_max", "temp_abs_min",
    "tropical_nights", "frost_nights",
]

# Mapeo código-parámetro → (campo del esquema, factor de unidades) para
# datos *diarios*.
DAILY_PARAM_MAP: Dict[str, Tuple[str, float]] = {
    "TA_AVG_1.5m":   ("temp_mean",    1.0),
    "TA_MAX_1.5m":   ("temp_max",     1.0),
    "TA_MIN_1.5m":   ("temp_min",     1.0),
    "PP_SUM_1.5m":   ("precip_total", 1.0),
    "VV_AVG_10m":    ("wind_mean",    3.6),   # m/s → km/h
    "VV_AVG_2m":     ("wind_mean",    3.6),
    "VV_MAX_10m":    ("gust_max",     3.6),
    "VV_MAX_2m":     ("gust_max",     3.6),
    "HSOL_SUM_1.5m": ("solar_hours",  1.0),
}

# Mapeo código-parámetro → campo del esquema para datos *mensuales*.
MONTHLY_PARAM_MAP: Dict[str, Tuple[str, float]] = {
    "TA_AVG_1.5m":          ("temp_mean",      1.0),
    "TA_AVGMAX_1.5m":       ("temp_max",       1.0),   # media de máximas
    "TA_AVGMIN_1.5m":       ("temp_min",       1.0),   # media de mínimas
    "TA_MAX_1.5m":          ("temp_abs_max",   1.0),   # absoluta del mes
    "TA_MIN_1.5m":          ("temp_abs_min",   1.0),   # absoluta del mes
    "PP_SUM_1.5m":          ("precip_total",   1.0),
    "PP_MAX_1.5m":          ("precip_max_24h", 1.0),
    "VV_AVG_10m":           ("wind_mean",      3.6),
    "VV_AVG_2m":            ("wind_mean",      3.6),
    "VV_MAX_10m":           ("gust_max",       3.6),
    "VV_MAX_2m":            ("gust_max",       3.6),
    "HSOL_SUM_1.5m":        ("solar_hours",    1.0),
    "NDPP_RECUENTO_1.5m":   ("rain_days",      1.0),
    "NDX_RECUENTO_1.5m":    ("frost_nights",   1.0),   # días de helada
}


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def parse_measures(
    medidas: Any,
    param_map: Dict[str, Tuple[str, float]],
) -> Dict[str, float]:
    """Extrae medidas de una listaMedidas según el mapa de parámetros.

    Descarta valores con código de validación 3 (erróneo) o 9 (sin registro).
    Cuando hay duplicados de campo, conserva el de mayor prioridad (10m > 2m).
    """
    out: Dict[str, float] = {}
    priority: Dict[str, int] = {}
    if not isinstance(medidas, list):
        return out

    for m in medidas:
        if not isinstance(m, dict):
            continue
        # Descartar datos erróneos / sin registro.
        try:
            vc = int(m.get("lnCodigoValidacion", 0))
            if vc in (3, 9):
                continue
        except (TypeError, ValueError):
            pass

        code = str(m.get("codigoParametro", "")).strip()
        mapping = param_map.get(code)
        if mapping is None:
            continue

        field, factor = mapping
        val = _safe_float(m.get("valor"))
        if _is_nan(val) or val <= -9999:
            continue

        # Priorizar sensores a mayor altura (10m > 2m).
        score = 10 if "10m" in code.lower() or "10M" in code else 0
        if field not in out or score >= priority.get(field, -1):
            out[field] = val * factor
            priority[field] = score
    return out


def parse_entry_date(data_str: Any) -> Optional[date]:
    """Convierte la cadena 'data' de la respuesta a un objeto date (UTC)."""
    raw = str(data_str or "").strip()
    if not raw:
        return None
    # Formato habitual: "2024-01-15T00:00:00.000+01:00" o similar ISO.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:26].replace("+", "+") if "+" in raw else raw, fmt).date()
        except Exception:
            continue
    # Intento genérico con fromisoformat (Python 3.11+).
    try:
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


def extract_climo_rows(
    payload: Any,
    station_id: str,
    *,
    list_key: str,
    param_map: Dict[str, Tuple[str, float]],
    month_start: bool = False,
) -> List[Dict[str, Any]]:
    """
    Filas del esquema común desde el payload de datosDiarios/datosMensuais.

    ``list_key``: "listDatosDiarios" o "listDatosMensuais".
    ``month_start``: canonicaliza la fecha al día 1 del mes (mensuales).
    """
    entries = payload.get(list_key, []) if isinstance(payload, dict) else []
    sid = str(station_id).strip()

    rows: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_date = parse_entry_date(entry.get("data"))
        if entry_date is None:
            continue

        stations = entry.get("listaEstacions", [])
        if not isinstance(stations, list):
            continue

        # Buscar nuestra estación en la lista.
        station_block = None
        for s in stations:
            if not isinstance(s, dict):
                continue
            if str(s.get("idEstacion", "")).strip() == sid:
                station_block = s
                break
        if station_block is None and stations:
            station_block = stations[0]
        if station_block is None:
            continue

        measures = parse_measures(station_block.get("listaMedidas", []), param_map)
        if not measures:
            continue

        if month_start:
            entry_date = date(entry_date.year, entry_date.month, 1)
        row: Dict[str, Any] = {"date": pd.Timestamp(entry_date), "epoch": 0.0}
        for col in CLIMO_DAILY_COLS[2:] + CLIMO_EXTRA_COLS:
            row[col] = measures.get(col, float("nan"))
        rows.append(row)

    return rows


def empty_climo_df() -> pd.DataFrame:
    return pd.DataFrame(columns=CLIMO_DAILY_COLS + CLIMO_EXTRA_COLS)


def rows_to_climo_df(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """Ensambla filas al DataFrame común: tipado, dedupe por fecha, esquema."""
    if not rows:
        return empty_climo_df()

    df = pd.DataFrame(list(rows))
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for col in CLIMO_DAILY_COLS[2:] + CLIMO_EXTRA_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "precip_total" in df.columns:
        df["precip_total"] = df["precip_total"].clip(lower=0)

    df = (
        df.sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )
    # Asegurar que todas las columnas del esquema existen.
    for col in CLIMO_DAILY_COLS + CLIMO_EXTRA_COLS:
        if col not in df.columns:
            df[col] = float("nan") if col != "date" else pd.NaT
    return df


def aggregate_monthly_rows_to_year(year: int, monthly_rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Agrega las filas mensuales de un año a una única fila anual."""
    if not monthly_rows:
        return None

    mdf = pd.DataFrame(list(monthly_rows))
    for col in CLIMO_DAILY_COLS[2:] + CLIMO_EXTRA_COLS:
        if col in mdf.columns:
            mdf[col] = pd.to_numeric(mdf[col], errors="coerce")

    row: Dict[str, Any] = {
        "date": pd.Timestamp(date(year, 1, 1)),
        "epoch": 0.0,
    }
    # Medias: temp_mean, temp_max, temp_min, wind_mean
    for col in ("temp_mean", "temp_max", "temp_min", "wind_mean"):
        if col in mdf.columns:
            row[col] = float(mdf[col].mean(skipna=True))
        else:
            row[col] = float("nan")
    # Sumas: precip_total, solar_hours, rain_days, frost_nights
    for col in ("precip_total", "solar_hours", "rain_days", "frost_nights"):
        if col in mdf.columns:
            s = mdf[col].dropna()
            row[col] = float(s.sum()) if len(s) > 0 else float("nan")
        else:
            row[col] = float("nan")
    # Máximos: temp_abs_max, gust_max, precip_max_24h
    for col in ("temp_abs_max", "gust_max", "precip_max_24h"):
        if col in mdf.columns:
            s = mdf[col].dropna()
            row[col] = float(s.max()) if len(s) > 0 else float("nan")
        else:
            row[col] = float("nan")
    # Mínimo: temp_abs_min
    if "temp_abs_min" in mdf.columns:
        s = mdf["temp_abs_min"].dropna()
        row["temp_abs_min"] = float(s.min()) if len(s) > 0 else float("nan")
    else:
        row["temp_abs_min"] = float("nan")

    return row


def yearly_rows_to_climo_df(yearly_rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """DataFrame anual: como ``rows_to_climo_df`` pero sin dedupe ni clip."""
    if not yearly_rows:
        return empty_climo_df()

    df = pd.DataFrame(list(yearly_rows))
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for col in CLIMO_DAILY_COLS[2:] + CLIMO_EXTRA_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("date").reset_index(drop=True)
    for col in CLIMO_DAILY_COLS + CLIMO_EXTRA_COLS:
        if col not in df.columns:
            df[col] = float("nan") if col != "date" else pd.NaT
    return df

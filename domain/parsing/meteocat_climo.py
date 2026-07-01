"""
Parsing puro de la climatología de Meteocat (endpoints
``/variables/estadistics/{diaris,mensuals,anuals}``).

Módulo de dominio sin ``streamlit`` ni transporte. Reúne los códigos
de variable estadística de Meteocat (distintos de los de medición en
tiempo real), el esquema de columnas común de climogramas y los helpers
de selección de candidatos y ensamblado de filas para
``server/services/meteocat_climo.py``.

Particularidades de Meteocat que viven aquí:
- Viento/racha tienen variable distinta por altura del anemómetro
  (10/6/2 m); se prueba en ese orden y gana la primera con datos.
- Las velocidades vienen en m/s → km/h.
- Las máximas/mínimas absolutas y las rachas traen fecha de ocurrencia.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

# =====================================================================
# Códigos de variable estadística (no confundir con los de medición)
# =====================================================================

# Diarios — /variables/estadistics/diaris/{codiVariable}
STAT_TEMP_MEAN     = 1000
STAT_TEMP_MAX      = 1001
STAT_TEMP_MIN      = 1002
STAT_PRECIP        = 1300
STAT_WIND_MEAN_10  = 1503
STAT_WIND_MEAN_6   = 1504
STAT_WIND_MEAN_2   = 1505
STAT_GUST_MAX_10   = 1512
STAT_GUST_MAX_6    = 1513
STAT_GUST_MAX_2    = 1514

CLIMO_STAT_CODES = {
    "temp_mean": [STAT_TEMP_MEAN],
    "temp_max": [STAT_TEMP_MAX],
    "temp_min": [STAT_TEMP_MIN],
    "wind_mean": [STAT_WIND_MEAN_2, STAT_WIND_MEAN_6, STAT_WIND_MEAN_10],
    "gust_max": [STAT_GUST_MAX_2, STAT_GUST_MAX_6, STAT_GUST_MAX_10],
    "precip_total": [STAT_PRECIP],
}

# Anuales — /variables/estadistics/anuals/{codiVariable}
STAT_AN_TEMP_MEAN      = 3000
STAT_AN_TEMP_ABS_MAX   = 3001
STAT_AN_TEMP_ABS_MIN   = 3002
STAT_AN_TEMP_MAX_MEAN  = 3003
STAT_AN_TEMP_MIN_MEAN  = 3004
STAT_AN_PRECIP_TOTAL   = 3300
STAT_AN_PRECIP_MAX_24H = 3303
STAT_AN_RAIN_DAYS      = 3305
STAT_AN_SOLAR_MEAN     = 3400
STAT_AN_WIND_MEAN_10   = 3503
STAT_AN_WIND_MEAN_6    = 3504
STAT_AN_WIND_MEAN_2    = 3505
STAT_AN_GUST_MAX_10    = 3512
STAT_AN_GUST_MAX_6     = 3513
STAT_AN_GUST_MAX_2     = 3514

ANNUAL_CLIMO_CODES = {
    "temp_mean": [STAT_AN_TEMP_MEAN],
    "temp_max": [STAT_AN_TEMP_MAX_MEAN],
    "temp_min": [STAT_AN_TEMP_MIN_MEAN],
    "wind_mean": [STAT_AN_WIND_MEAN_2, STAT_AN_WIND_MEAN_6, STAT_AN_WIND_MEAN_10],
    "gust_max": [STAT_AN_GUST_MAX_2, STAT_AN_GUST_MAX_6, STAT_AN_GUST_MAX_10],
    "precip_total": [STAT_AN_PRECIP_TOTAL],
    "solar_mean": [STAT_AN_SOLAR_MEAN],
    "precip_max_24h": [STAT_AN_PRECIP_MAX_24H],
    "rain_days": [STAT_AN_RAIN_DAYS],
    "temp_abs_max": [STAT_AN_TEMP_ABS_MAX],
    "temp_abs_min": [STAT_AN_TEMP_ABS_MIN],
}

# Mensuales — /variables/estadistics/mensuals/{codiVariable}
STAT_MO_TEMP_MEAN      = 2000
STAT_MO_TEMP_ABS_MAX   = 2001
STAT_MO_TEMP_ABS_MIN   = 2002
STAT_MO_TEMP_MAX_MEAN  = 2003
STAT_MO_TEMP_MIN_MEAN  = 2004
STAT_MO_PRECIP_TOTAL   = 2300
STAT_MO_PRECIP_MAX_24H = 2303
STAT_MO_RAIN_DAYS      = 2305
STAT_MO_SOLAR_MEAN     = 2400
STAT_MO_WIND_MEAN_10   = 2503
STAT_MO_WIND_MEAN_6    = 2504
STAT_MO_WIND_MEAN_2    = 2505
STAT_MO_GUST_MAX_10    = 2512
STAT_MO_GUST_MAX_6     = 2513
STAT_MO_GUST_MAX_2     = 2514

MONTHLY_CLIMO_CODES = {
    "temp_mean": [STAT_MO_TEMP_MEAN],
    "temp_max": [STAT_MO_TEMP_MAX_MEAN],
    "temp_min": [STAT_MO_TEMP_MIN_MEAN],
    "wind_mean": [STAT_MO_WIND_MEAN_2, STAT_MO_WIND_MEAN_6, STAT_MO_WIND_MEAN_10],
    "gust_max": [STAT_MO_GUST_MAX_2, STAT_MO_GUST_MAX_6, STAT_MO_GUST_MAX_10],
    "precip_total": [STAT_MO_PRECIP_TOTAL],
    "solar_mean": [STAT_MO_SOLAR_MEAN],
    "precip_max_24h": [STAT_MO_PRECIP_MAX_24H],
    "rain_days": [STAT_MO_RAIN_DAYS],
    "temp_abs_max": [STAT_MO_TEMP_ABS_MAX],
    "temp_abs_min": [STAT_MO_TEMP_ABS_MIN],
}

# Códigos diarios para los extremos derivados (mín de máximas, etc.)
WIND_MEAN_DAILY_CANDIDATES = [STAT_WIND_MEAN_2, STAT_WIND_MEAN_6, STAT_WIND_MEAN_10]

CLIMO_DAILY_SCHEMA = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "wind_dir_mean",
    "gust_max",
    "precip_total",
]

CLIMO_ANNUAL_EXTRA_SCHEMA = [
    "solar_mean",
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_max_date",
    "temp_abs_min",
    "temp_abs_min_date",
    "gust_abs_max_date",
    "precip_max_24h_date",
]

CLIMO_WIND_METRICS = {"wind_mean", "gust_max"}
CLIMO_DATE_COLUMN_BY_METRIC = {
    "temp_abs_max": "temp_abs_max_date",
    "temp_abs_min": "temp_abs_min_date",
    "gust_max": "gust_abs_max_date",
    "precip_max_24h": "precip_max_24h_date",
}
CLIMO_NUMERIC_COLUMNS = [
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "wind_dir_mean",
    "gust_max",
    "precip_total",
    "solar_mean",
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_min",
]


# =====================================================================
# Helpers numéricos / fecha (autocontenidos: el módulo no debe arrastrar
# el adaptador HTTP de Meteocat)
# =====================================================================

def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def ms_to_kmh(value: float) -> float:
    return float("nan") if _is_nan(value) else value * 3.6


def parse_stats_date(raw_value: Any) -> Optional[str]:
    if isinstance(raw_value, dict):
        for key in ("data", "date", "valorData", "dataValor"):
            if key in raw_value:
                nested = parse_stats_date(raw_value.get(key))
                if nested:
                    return nested
        return None
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if len(raw) == 4 and raw.isdigit():
        return f"{raw}-01-01"
    if raw.endswith("Z"):
        raw = raw[:-1]
    base = raw.split("T", 1)[0]
    try:
        return datetime.fromisoformat(base).strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_stats_year(raw_value: Any) -> Optional[int]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    if raw.endswith("Z"):
        raw = raw.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(raw).year)
    except Exception:
        return None


def parse_stats_month(raw_value: Any) -> Optional[str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    if len(raw) >= 7 and raw[4] == "-" and raw[:4].isdigit() and raw[5:7].isdigit():
        return f"{raw[:4]}-{raw[5:7]}-01"
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-01")
    except Exception:
        return None


def format_date_for_ui(raw_day: str) -> str:
    parsed = parse_stats_date(raw_day)
    if not parsed:
        return "—"
    try:
        return datetime.fromisoformat(parsed).strftime("%d/%m/%Y")
    except Exception:
        return "—"


def iter_months(start_date: date, end_date: date):
    cursor = date(int(start_date.year), int(start_date.month), 1)
    limit = date(int(end_date.year), int(end_date.month), 1)
    while cursor <= limit:
        yield cursor.year, cursor.month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def climo_epoch_from_label(date_label: str) -> float:
    try:
        return float(datetime.fromisoformat(str(date_label)).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return float("nan")


# =====================================================================
# Ensamblado de filas del esquema común
# =====================================================================

def empty_climo_row(date_label: str, epoch: float) -> Dict[str, Any]:
    return {
        "date": date_label,
        "epoch": epoch,
        "temp_mean": float("nan"),
        "temp_max": float("nan"),
        "temp_min": float("nan"),
        "wind_mean": float("nan"),
        "wind_dir_mean": float("nan"),
        "gust_max": float("nan"),
        "precip_total": float("nan"),
        "solar_mean": float("nan"),
        "precip_max_24h": float("nan"),
        "rain_days": float("nan"),
        "temp_abs_max": float("nan"),
        "temp_abs_max_date": None,
        "temp_abs_min": float("nan"),
        "temp_abs_min_date": None,
        "gust_abs_max_date": None,
        "precip_max_24h_date": None,
    }


def build_climo_rows(date_labels: List[str]) -> Dict[Any, Dict[str, Any]]:
    return {
        str(date_label): empty_climo_row(str(date_label), climo_epoch_from_label(str(date_label)))
        for date_label in date_labels
    }


def metric_value_available(item: Any) -> bool:
    return isinstance(item, dict) and (not _is_nan(_safe_float(item.get("value"))))


def select_metric_candidate_code(candidate_codes: List[int], has_data_for_code) -> Optional[int]:
    normalized = [int(code) for code in candidate_codes]
    if not normalized:
        return None
    chosen_code = int(normalized[0])
    for candidate_code in normalized:
        try:
            if has_data_for_code(int(candidate_code)):
                chosen_code = int(candidate_code)
                break
        except Exception:
            continue
    return chosen_code


def apply_climo_metric_value(row: Dict[str, Any], metric_name: str, payload: Dict[str, Any]) -> None:
    value = _safe_float(payload.get("value"))
    if _is_nan(value):
        return
    if metric_name in CLIMO_WIND_METRICS:
        value = ms_to_kmh(value)
    row[metric_name] = float(value)
    date_col = CLIMO_DATE_COLUMN_BY_METRIC.get(metric_name)
    if date_col:
        row[date_col] = parse_stats_date(payload.get("date"))


def finalize_climo_frame(frame: pd.DataFrame, *, fill_temp_mean: bool = True) -> pd.DataFrame:
    for col in CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")

    for col in CLIMO_NUMERIC_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    if fill_temp_mean:
        missing_mean = frame["temp_mean"].isna() & frame["temp_max"].notna() & frame["temp_min"].notna()
        if missing_mean.any():
            frame.loc[missing_mean, "temp_mean"] = (
                frame.loc[missing_mean, "temp_max"] + frame.loc[missing_mean, "temp_min"]
            ) / 2.0

    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA]


def finalize_climo_rows(rows_by_key: Dict[Any, Dict[str, Any]], *, fill_temp_mean: bool = True) -> pd.DataFrame:
    if not rows_by_key:
        return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)
    frame = pd.DataFrame(rows_by_key.values())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    return finalize_climo_frame(frame, fill_temp_mean=fill_temp_mean)


def empty_daily_df() -> pd.DataFrame:
    return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA)


def empty_annual_df() -> pd.DataFrame:
    return pd.DataFrame(columns=CLIMO_DAILY_SCHEMA + CLIMO_ANNUAL_EXTRA_SCHEMA)


def parse_daily_stats_values(payload: Any) -> Dict[str, float]:
    """``valors`` de /estadistics/diaris → {YYYY-MM-DD: valor}."""
    valors: List[Any] = []
    if isinstance(payload, dict):
        v = payload.get("valors", [])
        if isinstance(v, list):
            valors = v
    out: Dict[str, float] = {}
    for item in valors:
        if not isinstance(item, dict):
            continue
        day_txt = parse_stats_date(item.get("data"))
        if not day_txt:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        out[day_txt] = float(value)
    return out


def collect_stat_entries(payload: Any) -> List[Dict[str, Any]]:
    """Aplana ``valors``/``estadistics``/``variables[].estadistics`` a una lista de dicts."""
    entries: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            entries.extend(collect_stat_entries(item))
        return entries
    if not isinstance(payload, dict):
        return entries

    direct_vals = payload.get("valors")
    if isinstance(direct_vals, list):
        entries.extend(item for item in direct_vals if isinstance(item, dict))

    direct_stats = payload.get("estadistics")
    if isinstance(direct_stats, list):
        entries.extend(item for item in direct_stats if isinstance(item, dict))

    variables = payload.get("variables")
    if isinstance(variables, list):
        for var in variables:
            if not isinstance(var, dict):
                continue
            stats = var.get("estadistics", [])
            if isinstance(stats, list):
                entries.extend(item for item in stats if isinstance(item, dict))

    return entries


def _extract_annual_item_date(item: Dict[str, Any]) -> Optional[str]:
    for key in ("data", "date", "dataExtrem", "data_extrem", "dataMax", "dataMin", "dataValor", "valorData"):
        if key in item:
            parsed = parse_stats_date(item.get(key))
            if parsed:
                return parsed
    return None


def parse_annual_stats_by_year(payload: Any) -> Dict[int, Dict[str, Any]]:
    """Serie anual → {año: {"value", "date"}}."""
    per_year: Dict[int, Dict[str, Any]] = {}
    for item in collect_stat_entries(payload):
        candidate_year = parse_stats_year(item.get("any"))
        if candidate_year is None:
            candidate_year = parse_stats_year(item.get("data"))
        if candidate_year is None:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        per_year[int(candidate_year)] = {
            "value": float(value),
            "date": _extract_annual_item_date(item),
        }
    return per_year


def parse_monthly_stats_by_month(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Serie mensual de un año → {YYYY-MM-01: {"value", "date"}}."""
    per_month: Dict[str, Dict[str, Any]] = {}
    for item in collect_stat_entries(payload):
        month_key = parse_stats_month(item.get("data"))
        if not month_key:
            continue
        value = _safe_float(item.get("valor"))
        if _is_nan(value):
            continue
        per_month[month_key] = {
            "value": float(value),
            "date": _extract_annual_item_date(item),
        }
    return per_month

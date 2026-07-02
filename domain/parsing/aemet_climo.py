"""
Parsing puro de la climatología de AEMET OpenData (diaria y
mensual/anual).

Módulo de dominio sin ``streamlit`` ni transporte: transforma los payloads de
``valores/climatologicos/diarios`` y
``valores/climatologicos/mensualesanuales`` al esquema de columnas
común de climogramas consumido por ``server/services/aemet_climo.py``.

Particularidades de AEMET que viven aquí (y SOLO aquí):
- Números con coma decimal, 'Ip' (inapreciable → 0 en precip),
  paréntesis con la fecha de ocurrencia ("37.4(27)", "-2.0(27/dic)") y
  rachas "dir/velocidad" ("99/21.1(07)").
- Mes 13 = resumen anual del año (se separa de los mensuales).
- Vientos en m/s → km/h; sentinelas de dirección (99/990/999) → NaN.
- El resumen anual suele traer ta_max/ta_min del año; los mensuales
  también: se cruzan y gana el más extremo, arrastrando su fecha.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

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

CLIMO_EXTRA_SCHEMA = [
    "solar_mean",
    "solar_hours",       # horas de sol diarias (endpoint diario AEMET, campo 'sol')
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_max_date",
    "temp_abs_min",
    "temp_abs_min_date",
    "gust_abs_max_date",
    "precip_max_24h_date",
    "tropical_nights",   # noches tropicales del mes (nt_30 de AEMET mensual)
    "frost_nights",      # noches de helada del mes (nt_00 de AEMET mensual)
]


def _parse_num(value):
    """Parseo robusto de números AEMET (coma decimal, vacíos, 'Ip', paréntesis…).

    AEMET devuelve campos como ta_max='37.4(27)', p_max='29.0(09)',
    q_min='984.2(09/nov)', w_racha='99/21.1(07)' donde el paréntesis
    indica el día/mes de ocurrencia. Este parser extrae solo la parte numérica.
    """
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return float("nan")
    try:
        s = str(value).strip()
        if not s:
            return float("nan")
        # Quitar parte entre paréntesis: "37.4(27)" → "37.4", "984.2(09/nov)" → "984.2"
        paren_idx = s.find("(")
        if paren_idx > 0:
            s = s[:paren_idx].strip()
        # w_racha usa "dir/velocidad": "99/21.1" → tomar la última parte
        if "/" in s:
            s = s.rsplit("/", 1)[-1].strip()
        s = s.replace(",", ".")
        if s.lower() in {"ip", "nan", "none", "--", "-"}:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


_MONTH_ABBR_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _extract_paren_date(raw_value: Any, period_str: Any) -> Optional[str]:
    """Extrae fecha desde valores AEMET con paréntesis.

    Ejemplos:
        _extract_paren_date("37.4(27)", "2010-8")       → "2010-08-27"
        _extract_paren_date("37.4(27/ago)", "2010-13")   → "2010-08-27"
        _extract_paren_date("-2.0(27/dic)", "2010-13")   → "2010-12-27"
    """
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    p_start = s.find("(")
    p_end = s.find(")")
    if p_start < 0 or p_end <= p_start + 1:
        return None
    paren = s[p_start + 1:p_end].strip()

    # Año desde la cadena de periodo ("2010-8", "2010-13", etc.)
    period = str(period_str or "").strip()
    if len(period) < 4 or not period[:4].isdigit():
        return None
    year = int(period[:4])

    # Mes: puede venir del periodo o del paréntesis
    month: Optional[int] = None
    if "-" in period:
        mp = period.split("-", 1)[1]
        if mp.isdigit():
            mm = int(mp)
            if 1 <= mm <= 12:
                month = mm

    # Parsear paréntesis: "27" (solo día) o "27/ago" (día/mes)
    if "/" in paren:
        parts = paren.split("/", 1)
        day_str = parts[0].strip()
        month_str = parts[1].strip().lower()
        month = _MONTH_ABBR_ES.get(month_str, month)
    else:
        day_str = paren

    try:
        day = int(day_str)
    except ValueError:
        return None

    if month is None or day < 1 or day > 31:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def _aemet_first_non_empty(record: Dict[str, Any], keys: List[str]):
    """Devuelve el primer campo no vacío ignorando mayúsculas/minúsculas."""
    record_ci = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = record.get(key)
        if value is None:
            value = record_ci.get(str(key).lower())
        if value is not None and value != "":
            return value
    return None


def _aemet_first_by_patterns(record: Dict[str, Any], keys: List[str], patterns: List[str]):
    """Busca primero por claves conocidas y luego por patrón de nombre de campo."""
    value = _aemet_first_non_empty(record, keys)
    if value is not None and value != "":
        return value

    for k, v in record.items():
        if v is None or v == "":
            continue
        lk = str(k).lower()
        # Ignorar banderas de calidad y estadísticas auxiliares (STDVV/STDDV),
        # que no son la magnitud principal y sesgan la serie si se capturan
        # por coincidencia parcial del nombre.
        if lk.startswith("q") or lk.startswith("std"):
            continue
        if any(p in lk for p in patterns):
            return v
    return None


def _parse_wind_dir_deg(value) -> float:
    """Parsea dirección de viento a grados, aceptando numérico y cardinal (ES/EN)."""
    if value is None:
        return float("nan")

    s = str(value).strip().upper()
    if not s:
        return float("nan")
    if s in {"CALMA", "CALM", "VARIABLE", "VRB"}:
        return float("nan")

    num = _parse_num(value)
    if num == num:
        # AEMET puede mezclar grados reales con códigos/sentinelas de ausencia o
        # viento variable. No aplicar módulo aquí evita que 990/999 acaben como
        # "N" falso en la gráfica y la rosa.
        if num in {99.0, 990.0, 999.0}:
            return float("nan")
        if num < 0.0 or num > 360.0:
            return float("nan")
        if abs(num - 360.0) < 1e-6:
            return 0.0
        return float(num)

    # En español se usa O para Oeste.
    s_norm = s.replace("O", "W")

    cardinal_16 = {
        "N": 0.0,
        "NNE": 22.5,
        "NE": 45.0,
        "ENE": 67.5,
        "E": 90.0,
        "ESE": 112.5,
        "SE": 135.0,
        "SSE": 157.5,
        "S": 180.0,
        "SSW": 202.5,
        "SW": 225.0,
        "WSW": 247.5,
        "W": 270.0,
        "WNW": 292.5,
        "NW": 315.0,
        "NNW": 337.5,
    }

    return cardinal_16.get(s_norm, float("nan"))


def _empty_climo_dataframe(include_extras: bool = True) -> pd.DataFrame:
    columns = CLIMO_DAILY_SCHEMA + (CLIMO_EXTRA_SCHEMA if include_extras else [])
    return pd.DataFrame(columns=columns)


def _parse_aemet_climo_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) == 4 and raw.isdigit():
        return f"{raw}-01-01"
    try:
        dt = pd.to_datetime(raw, errors="raise")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_precip_aemet(value: Any) -> float:
    if value is None:
        return float("nan")
    raw = str(value).strip().lower()
    if raw == "ip":
        return 0.0
    return _parse_num(value)


def _parse_month_key(value: Any) -> Optional[str]:
    """Parsea 'YYYY-M' o 'YYYY-MM' a 'YYYY-MM'. Descarta mes 13 (resumen anual AEMET)."""
    raw = str(value or "").strip()
    if len(raw) < 6 or not raw[:4].isdigit() or raw[4] != "-":
        return None
    month_part = raw[5:]
    if not month_part.isdigit():
        return None
    mm = int(month_part)
    if mm < 1 or mm > 12:
        return None
    return f"{raw[:4]}-{mm:02d}"


def _parse_year_key(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    return None


def _aemet_climo_num(record: Dict[str, Any], keys: List[str], patterns: List[str]) -> float:
    value = _aemet_first_by_patterns(record, keys, patterns)
    return _parse_num(value)


def _aemet_daily_record_to_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None

    day_txt = _parse_aemet_climo_date(_aemet_first_non_empty(record, ["fecha", "Fecha", "fint", "FINT"]))
    if not day_txt:
        return None

    temp_mean = _aemet_climo_num(record, ["tmed", "TMED", "tm", "TM"], ["tmed", "tmedia", "temp_media"])
    temp_max = _aemet_climo_num(record, ["tmax", "TMAX", "tamax", "TAMAX"], ["tmax", "tamax"])
    temp_min = _aemet_climo_num(record, ["tmin", "TMIN", "tamin", "TAMIN"], ["tmin", "tamin"])
    wind_mean = _aemet_climo_num(record, ["velmedia", "VELMEDIA", "vv", "VV"], ["velmedia", "vv", "viento"])
    wind_dir_mean = _parse_wind_dir_deg(
        _aemet_first_by_patterns(
            record,
            ["dir", "DIR", "dv", "DV", "dd", "DD", "dir_viento", "direccion_viento", "winddir"],
            ["dir", "direccion", "dv", "dd", "winddir"],
        )
    )
    gust_max = _aemet_climo_num(record, ["racha", "RACHA", "vmax", "VMAX"], ["racha", "vmax", "gust"])
    precip_total = _parse_precip_aemet(_aemet_first_by_patterns(record, ["prec", "PREC", "pp", "PP"], ["prec", "pp"]))
    # 'sol': horas de sol del día (disponible en estaciones con piranómetro/heliógrafo)
    solar_hours = _aemet_climo_num(record, ["sol", "SOL", "insolacion", "INSOLACION"], ["sol", "insol"])

    if pd.isna(temp_mean) and not pd.isna(temp_max) and not pd.isna(temp_min):
        temp_mean = (float(temp_max) + float(temp_min)) / 2.0

    try:
        epoch = float(pd.Timestamp(day_txt).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        epoch = float("nan")

    if not pd.isna(wind_mean):
        wind_mean = float(wind_mean) * 3.6
    if not pd.isna(gust_max):
        gust_max = float(gust_max) * 3.6
    if not pd.isna(precip_total):
        precip_total = max(0.0, float(precip_total))

    return {
        "date": day_txt,
        "epoch": epoch,
        "temp_mean": float(temp_mean) if not pd.isna(temp_mean) else float("nan"),
        "temp_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "wind_mean": float(wind_mean) if not pd.isna(wind_mean) else float("nan"),
        "wind_dir_mean": float(wind_dir_mean) if not pd.isna(wind_dir_mean) else float("nan"),
        "gust_max": float(gust_max) if not pd.isna(gust_max) else float("nan"),
        "precip_total": float(precip_total) if not pd.isna(precip_total) else float("nan"),
        "solar_hours": float(solar_hours) if not pd.isna(solar_hours) else float("nan"),
    }


def _normalize_climo_daily_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_climo_dataframe(include_extras=False)
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    if frame.empty:
        return _empty_climo_dataframe(include_extras=False)

    output_cols = CLIMO_DAILY_SCHEMA + ["solar_hours"]
    for col in output_cols:
        if col not in frame.columns:
            frame[col] = float("nan")

    numeric_cols = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "wind_dir_mean", "gust_max", "precip_total", "solar_hours"]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)

    frame = (
        frame.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[output_cols]


def _iter_date_chunks(start_date: date, end_date: date, max_days: int = 150) -> Iterable[Tuple[date, date]]:
    """Divide un rango de fechas en trozos de max_days días (AEMET limita a ~6 meses)."""
    cursor = start_date
    delta = timedelta(days=max_days - 1)
    while cursor <= end_date:
        chunk_end = min(cursor + delta, end_date)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _merge_daily_chunks(chunks: List[pd.DataFrame]) -> pd.DataFrame:
    """Concatena chunks diarios normalizados, deduplica por fecha (último gana)."""
    non_empty = [c for c in chunks if isinstance(c, pd.DataFrame) and not c.empty]
    if not non_empty:
        return _empty_climo_dataframe(include_extras=False)

    all_days = pd.concat(non_empty, ignore_index=True)
    all_days["date"] = pd.to_datetime(all_days["date"], errors="coerce").dt.normalize()
    all_days = (
        all_days.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    # Incluir solar_hours si está presente (estaciones con piranómetro/heliógrafo)
    output_cols = CLIMO_DAILY_SCHEMA + (["solar_hours"] if "solar_hours" in all_days.columns else [])
    return all_days[output_cols]


def _aemet_monthlyannual_to_metrics(record: Dict[str, Any]) -> Dict[str, Any]:
    # Contexto de periodo para extracción de fechas de los paréntesis
    period_str = str(_aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"]) or "")

    temp_mean = _aemet_climo_num(record, ["tm_mes", "TM_MES", "tm", "TM", "tmed", "TMED"], ["tm_mes", "tmed", "temp_media"])
    temp_max = _aemet_climo_num(record, ["tm_max", "TM_MAX", "txm", "TXM"], ["tm_max", "txm"])
    temp_min = _aemet_climo_num(record, ["tm_min", "TM_MIN", "tnm", "TNM"], ["tm_min", "tnm"])
    wind_mean = _aemet_climo_num(record, ["w_med", "W_MED", "velmedia", "VELMEDIA", "vmedia", "VMEDIA"], ["w_med", "velmedia", "vmedia"])
    gust_max = _aemet_climo_num(record, ["w_racha", "W_RACHA", "racha", "RACHA", "vmax", "VMAX"], ["w_racha", "racha", "vmax"])
    precip_total = _parse_precip_aemet(_aemet_first_by_patterns(record, ["p_mes", "P_MES", "prec", "PREC", "pp", "PP"], ["p_mes", "prec", "pp"]))
    rain_days = _aemet_climo_num(record, ["n_llu", "N_LLU", "diaslluvia", "DIASLLUVIA", "n_dias_lluvia"], ["n_llu", "dias", "lluv"])
    precip_max_24h = _aemet_climo_num(record, ["p_max", "P_MAX", "prec_max_24h", "PREC_MAX_24H", "pmax24", "PMAX24"], ["p_max", "max24", "pmax24"])

    # Horas de sol (campo 'e' en el endpoint mensual = décimas de hora; 'sol' en algunas variantes)
    solar_mean = _aemet_climo_num(record, ["sol", "SOL", "insolacion", "INSOLACION"], ["sol", "insol"])
    if pd.isna(solar_mean):
        # Campo 'e' = horas de sol en décimas → convertir a horas
        e_val = _aemet_climo_num(record, ["e", "E"], [])
        if not pd.isna(e_val) and e_val >= 0:
            solar_mean = e_val / 10.0

    # Noches tropicales (nt_30): número de noches del mes con mínima ≥ 20 °C
    tropical_nights = _aemet_climo_num(record, ["nt_30", "NT_30", "noches_tropicales"], ["nt_30"])
    # Noches de helada (nt_00): número de noches del mes con mínima ≤ 0 °C
    frost_nights = _aemet_climo_num(record, ["nt_00", "NT_00", "noches_helada", "noches_frost"], ["nt_00"])

    # ta_max/ta_min: valor numérico + fecha dentro del paréntesis
    raw_ta_max = _aemet_first_by_patterns(record, ["ta_max", "TA_MAX", "tmax_abs", "TMAX_ABS"], ["ta_max", "tmax_abs"])
    raw_ta_min = _aemet_first_by_patterns(record, ["ta_min", "TA_MIN", "tmin_abs", "TMIN_ABS"], ["ta_min", "tmin_abs"])
    raw_gust = _aemet_first_by_patterns(record, ["w_racha", "W_RACHA", "racha", "RACHA"], ["w_racha", "racha"])
    raw_p_max = _aemet_first_by_patterns(record, ["p_max", "P_MAX"], ["p_max"])

    temp_abs_max = _parse_num(raw_ta_max)
    temp_abs_min = _parse_num(raw_ta_min)

    if pd.isna(temp_mean) and not pd.isna(temp_max) and not pd.isna(temp_min):
        temp_mean = (float(temp_max) + float(temp_min)) / 2.0
    if not pd.isna(wind_mean):
        wind_mean = float(wind_mean) * 3.6
    if not pd.isna(gust_max):
        gust_max = float(gust_max) * 3.6
    if not pd.isna(precip_total):
        precip_total = max(0.0, float(precip_total))
    # NO fallback: si AEMET no proporciona ta_max/ta_min el valor queda como NaN
    # y se mostrará "—" en la tabla. Mejor no mostrar nada que mostrar un valor
    # incorrecto (p.ej. la media de máximas en lugar de la máxima absoluta real).

    # Extraer fechas desde los paréntesis: "37.4(27)" → 27 del mes, "37.4(27/ago)" → 27/ago
    ta_max_date = _extract_paren_date(raw_ta_max, period_str)
    ta_min_date = _extract_paren_date(raw_ta_min, period_str)
    gust_date = _extract_paren_date(raw_gust, period_str)
    p_max_date = _extract_paren_date(raw_p_max, period_str)

    return {
        "temp_mean": float(temp_mean) if not pd.isna(temp_mean) else float("nan"),
        "temp_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "wind_mean": float(wind_mean) if not pd.isna(wind_mean) else float("nan"),
        "gust_max": float(gust_max) if not pd.isna(gust_max) else float("nan"),
        "precip_total": float(precip_total) if not pd.isna(precip_total) else float("nan"),
        "solar_mean": float(solar_mean) if not pd.isna(solar_mean) else float("nan"),
        "precip_max_24h": float(precip_max_24h) if not pd.isna(precip_max_24h) else float("nan"),
        "rain_days": float(rain_days) if not pd.isna(rain_days) else float("nan"),
        "temp_abs_max": float(temp_abs_max) if not pd.isna(temp_abs_max) else float("nan"),
        "temp_abs_min": float(temp_abs_min) if not pd.isna(temp_abs_min) else float("nan"),
        "temp_abs_max_date": ta_max_date,
        "temp_abs_min_date": ta_min_date,
        "gust_abs_max_date": gust_date,
        "precip_max_24h_date": p_max_date,
        "tropical_nights": float(tropical_nights) if not pd.isna(tropical_nights) else float("nan"),
        "frost_nights": float(frost_nights) if not pd.isna(frost_nights) else float("nan"),
    }


def _monthly_epoch(day_txt: str) -> float:
    try:
        return float(pd.Timestamp(day_txt).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return float("nan")


def _monthly_year_df(payload: Sequence[Any], year: int) -> pd.DataFrame:
    """Payload mensualesanuales de UN año → DataFrame mensual del esquema común.

    El registro de mes 13 (resumen anual) se usa para corregir los
    extremos absolutos cuando es más extremo que los mensuales.
    """
    yy = int(year)
    rows: List[Dict[str, Any]] = []
    annual_record: Optional[Dict[str, Any]] = None  # resumen anual AEMET (periodo YYYY-13)

    for record in payload or []:
        if not isinstance(record, dict):
            continue
        raw_date = _aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"])
        month_key = _parse_month_key(raw_date)
        if not month_key:
            # _parse_month_key devuelve None para mes 13 (resumen anual) → capturarlo
            year_key = _parse_year_key(raw_date)
            if year_key == yy:
                annual_record = record
            continue
        if not month_key.startswith(f"{yy:04d}-"):
            continue

        day_txt = f"{month_key}-01"
        metrics = _aemet_monthlyannual_to_metrics(record)
        rows.append({"date": day_txt, "epoch": _monthly_epoch(day_txt), **metrics})

    if not rows:
        return _empty_climo_dataframe(include_extras=True)

    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total",
        "solar_mean", "precip_max_24h", "rain_days", "temp_abs_max", "temp_abs_min",
        "tropical_nights", "frost_nights",
    ]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    frame["tropical_nights"] = frame["tropical_nights"].clip(lower=0)
    frame["frost_nights"] = frame["frost_nights"].clip(lower=0)

    # Usar el resumen anual de AEMET (ta_max/ta_min del año completo) para corregir
    # los extremos absolutos cuando los registros mensuales no tienen ta_max/ta_min
    # o cuando el valor anual es más extremo que el máximo de los mensuales.
    if annual_record is not None and not frame.empty:
        ann = _aemet_monthlyannual_to_metrics(annual_record)
        ann_abs_max = ann.get("temp_abs_max", float("nan"))
        ann_abs_min = ann.get("temp_abs_min", float("nan"))
        ann_abs_max_date = ann.get("temp_abs_max_date")
        ann_abs_min_date = ann.get("temp_abs_min_date")
        ann_gust = ann.get("gust_max", float("nan"))
        ann_gust_date = ann.get("gust_abs_max_date")

        abs_max_s = pd.to_numeric(frame["temp_abs_max"], errors="coerce")
        if not pd.isna(ann_abs_max) and (
            abs_max_s.isna().all() or float(ann_abs_max) >= float(abs_max_s.max(skipna=True))
        ):
            proxy = abs_max_s if abs_max_s.notna().any() else pd.to_numeric(frame["temp_max"], errors="coerce")
            idx = proxy.idxmax() if proxy.notna().any() else frame.index[0]
            frame.loc[idx, "temp_abs_max"] = float(ann_abs_max)
            if ann_abs_max_date:
                frame.loc[idx, "temp_abs_max_date"] = ann_abs_max_date

        abs_min_s = pd.to_numeric(frame["temp_abs_min"], errors="coerce")
        if not pd.isna(ann_abs_min) and (
            abs_min_s.isna().all() or float(ann_abs_min) <= float(abs_min_s.min(skipna=True))
        ):
            proxy = abs_min_s if abs_min_s.notna().any() else pd.to_numeric(frame["temp_min"], errors="coerce")
            idx = proxy.idxmin() if proxy.notna().any() else frame.index[0]
            frame.loc[idx, "temp_abs_min"] = float(ann_abs_min)
            if ann_abs_min_date:
                frame.loc[idx, "temp_abs_min_date"] = ann_abs_min_date

        gust_s = pd.to_numeric(frame["gust_max"], errors="coerce")
        if not pd.isna(ann_gust) and (
            gust_s.isna().all() or float(ann_gust) >= float(gust_s.max(skipna=True))
        ):
            idx = gust_s.idxmax() if gust_s.notna().any() else frame.index[0]
            frame.loc[idx, "gust_max"] = float(ann_gust)
            if ann_gust_date:
                frame.loc[idx, "gust_abs_max_date"] = ann_gust_date

    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA]


def _yearly_df(
    valid_years: Sequence[int],
    monthly_metrics: Dict[Tuple[int, int], Dict[str, Any]],
    annual_metrics: Dict[int, Dict[str, Any]],
) -> pd.DataFrame:
    """Métricas mensuales/anuales bucketizadas → DataFrame anual del esquema común."""
    rows: List[Dict[str, Any]] = []
    for yy in valid_years:
        day_txt = f"{yy:04d}-01-01"
        epoch = _monthly_epoch(day_txt)

        metrics = annual_metrics.get(int(yy))
        if metrics is None:
            month_rows = [monthly_metrics[(yy, mm)] for mm in range(1, 13) if (yy, mm) in monthly_metrics]
            if month_rows:
                month_df = pd.DataFrame(month_rows)
                metrics = {
                    "temp_mean": float(pd.to_numeric(month_df["temp_mean"], errors="coerce").mean()),
                    "temp_max": float(pd.to_numeric(month_df["temp_max"], errors="coerce").mean()),
                    "temp_min": float(pd.to_numeric(month_df["temp_min"], errors="coerce").mean()),
                    "wind_mean": float(pd.to_numeric(month_df["wind_mean"], errors="coerce").mean()),
                    "gust_max": float(pd.to_numeric(month_df["gust_max"], errors="coerce").max()),
                    "precip_total": float(pd.to_numeric(month_df["precip_total"], errors="coerce").sum(min_count=1)),
                    "solar_mean": float(pd.to_numeric(month_df["solar_mean"], errors="coerce").mean()),
                    "precip_max_24h": float(pd.to_numeric(month_df["precip_max_24h"], errors="coerce").max()),
                    "rain_days": float(pd.to_numeric(month_df["rain_days"], errors="coerce").sum(min_count=1)),
                    "temp_abs_max": float(pd.to_numeric(month_df["temp_abs_max"], errors="coerce").max()),
                    "temp_abs_min": float(pd.to_numeric(month_df["temp_abs_min"], errors="coerce").min()),
                    "temp_abs_max_date": None,
                    "temp_abs_min_date": None,
                    "gust_abs_max_date": None,
                    "precip_max_24h_date": None,
                    # Sumar noches tropicales/helada de los 12 meses para obtener el total anual
                    "tropical_nights": float(pd.to_numeric(month_df.get("tropical_nights", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)),
                    "frost_nights": float(pd.to_numeric(month_df.get("frost_nights", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)),
                }
            else:
                metrics = {}

        # El registro anual AEMET (YYYY-13) suele tener solo tm_max/tm_min
        # (medias anuales) pero NO ta_max/ta_min (absolutos reales).
        # Los registros mensuales SÍ los tienen → cruzar y corregir.
        if metrics:
            avail_months = [monthly_metrics[(yy, mm)] for mm in range(1, 13) if (yy, mm) in monthly_metrics]
            if avail_months:
                mdf = pd.DataFrame(avail_months)

                # temp_abs_max: mejor valor entre registro anual y máx. de mensuales
                m_abs_max_s = pd.to_numeric(mdf["temp_abs_max"], errors="coerce")
                if m_abs_max_s.notna().any():
                    idx_best = int(m_abs_max_s.idxmax())
                    m_best_max = float(m_abs_max_s.iloc[idx_best])
                    cur_max = metrics.get("temp_abs_max", float("nan"))
                    if pd.isna(cur_max) or m_best_max > float(cur_max):
                        metrics["temp_abs_max"] = m_best_max
                        m_date = avail_months[idx_best].get("temp_abs_max_date")
                        if m_date:
                            metrics["temp_abs_max_date"] = m_date

                # temp_abs_min: mejor valor entre registro anual y mín. de mensuales
                m_abs_min_s = pd.to_numeric(mdf["temp_abs_min"], errors="coerce")
                if m_abs_min_s.notna().any():
                    idx_best = int(m_abs_min_s.idxmin())
                    m_best_min = float(m_abs_min_s.iloc[idx_best])
                    cur_min = metrics.get("temp_abs_min", float("nan"))
                    if pd.isna(cur_min) or m_best_min < float(cur_min):
                        metrics["temp_abs_min"] = m_best_min
                        m_date = avail_months[idx_best].get("temp_abs_min_date")
                        if m_date:
                            metrics["temp_abs_min_date"] = m_date

                # gust_max: mejor valor
                m_gust_s = pd.to_numeric(mdf["gust_max"], errors="coerce")
                if m_gust_s.notna().any():
                    idx_best = int(m_gust_s.idxmax())
                    m_best_gust = float(m_gust_s.iloc[idx_best])
                    cur_gust = metrics.get("gust_max", float("nan"))
                    if pd.isna(cur_gust) or m_best_gust > float(cur_gust):
                        metrics["gust_max"] = m_best_gust
                        m_date = avail_months[idx_best].get("gust_abs_max_date")
                        if m_date:
                            metrics["gust_abs_max_date"] = m_date

                # precip_max_24h: mejor valor
                m_prec24_s = pd.to_numeric(mdf["precip_max_24h"], errors="coerce")
                if m_prec24_s.notna().any():
                    idx_best = int(m_prec24_s.idxmax())
                    m_best_prec = float(m_prec24_s.iloc[idx_best])
                    cur_prec = metrics.get("precip_max_24h", float("nan"))
                    if pd.isna(cur_prec) or m_best_prec > float(cur_prec):
                        metrics["precip_max_24h"] = m_best_prec
                        m_date = avail_months[idx_best].get("precip_max_24h_date")
                        if m_date:
                            metrics["precip_max_24h_date"] = m_date

                # tropical_nights / frost_nights: sumar de mensuales si el anual no las tiene
                for night_col in ("tropical_nights", "frost_nights"):
                    if night_col in mdf.columns:
                        m_night_s = pd.to_numeric(mdf[night_col], errors="coerce")
                        if m_night_s.notna().any():
                            cur_val = metrics.get(night_col, float("nan"))
                            if pd.isna(cur_val):
                                metrics[night_col] = float(m_night_s.sum(min_count=1))

        rows.append({"date": day_txt, "epoch": epoch, **metrics})

    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total",
        "solar_mean", "precip_max_24h", "rain_days", "temp_abs_max", "temp_abs_min",
        "tropical_nights", "frost_nights",
    ]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    frame["tropical_nights"] = frame["tropical_nights"].clip(lower=0)
    frame["frost_nights"] = frame["frost_nights"].clip(lower=0)
    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA]


def _bucket_monthlyannual_records(
    payload: Sequence[Any],
) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Separa records mensuales (YYYY-MM) del resumen anual (YYYY-13)."""
    monthly: Dict[Tuple[int, int], Dict[str, Any]] = {}
    annual: Dict[int, Dict[str, Any]] = {}
    for record in payload or []:
        if not isinstance(record, dict):
            continue
        raw_date = _aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"])
        month_key = _parse_month_key(raw_date)
        if month_key:
            yy = int(month_key[:4])
            mm = int(month_key[5:7])
            monthly[(yy, mm)] = _aemet_monthlyannual_to_metrics(record)
            continue
        year_key = _parse_year_key(raw_date)
        if year_key is not None:
            annual[int(year_key)] = _aemet_monthlyannual_to_metrics(record)
    return monthly, annual

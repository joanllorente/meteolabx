"""
Parsing puro del archivo climatológico de SMHI (corrected-archive CSV +
latest-months JSON).

El ``corrected-archive`` es un CSV con bloques de metadatos y una tabla
``Från;Till;Representativ(t) dygn/månad;<valor>;Kvalitet[;;notas]``, con
calidad controlada pero ~3 meses de decalaje; los meses recientes se
completan con el JSON de ``latest-months`` (campo ``ref`` = día/mes).
El ensamblado a DataFrame reutiliza los assemblers genéricos de
``meteogalicia_climo`` (mismo contrato de columnas).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from domain.parsing.meteogalicia_climo import (  # assemblers genéricos
    aggregate_monthly_rows_to_year,
    empty_climo_df,
    rows_to_climo_df,
    yearly_rows_to_climo_df,
)

__all__ = [
    "DAILY_PARAM_FIELDS",
    "MONTHLY_PARAM_FIELDS",
    "aggregate_monthly_rows_to_year",
    "empty_climo_df",
    "merge_field_rows",
    "parse_archive_csv",
    "parse_recent_json",
    "rows_to_climo_df",
    "yearly_rows_to_climo_df",
]

# Parámetro SMHI diario → campo del esquema común.
DAILY_PARAM_FIELDS: Dict[str, str] = {
    "2": "temp_mean",     # media diaria
    "20": "temp_max",     # máxima diaria
    "19": "temp_min",     # mínima diaria
    "5": "precip_total",  # precipitación 24 h
}
# Parámetro SMHI mensual → campo. SMHI no publica medias mensuales de
# máx/mín ni racha: el climograma anual lleva media y precipitación.
MONTHLY_PARAM_FIELDS: Dict[str, str] = {
    "22": "temp_mean",
    "23": "precip_total",
}

_ACCEPTED_QUALITIES = ("G", "Y")


def _safe_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return float("nan")


def _normalize_date(ref: str) -> str:
    """``2026-05`` (mensual) → ``2026-05-01``; los diarios ya vienen día."""
    ref = str(ref or "").strip()
    if len(ref) == 7:
        return f"{ref}-01"
    return ref[:10]


def _epoch_for(date_text: str) -> int | None:
    try:
        return int(
            datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc).timestamp()
        )
    except ValueError:
        return None


def parse_archive_csv(text: str, field: str) -> List[Dict[str, Any]]:
    """CSV del corrected-archive → filas {date, epoch, <field>}.

    La tabla de datos empieza en la línea cuyo encabezado arranca con
    ``Från Datum``; las filas llevan notas en columnas extra (tras un
    ``;;``) que se ignoran. Solo calidades G/Y.
    """
    rows: List[Dict[str, Any]] = []
    in_data = False
    for line in str(text or "").splitlines():
        if not in_data:
            if line.startswith("Från Datum"):
                in_data = True
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        quality = parts[4].strip().upper()
        if quality and quality not in _ACCEPTED_QUALITIES:
            continue
        value = _safe_float(parts[3])
        if value != value:
            continue
        date_text = _normalize_date(parts[2])
        epoch = _epoch_for(date_text)
        if epoch is None:
            continue
        if field.startswith("precip") and value < 0.0:
            value = 0.0
        rows.append({"date": date_text, "epoch": epoch, field: value})
    return rows


def parse_recent_json(payload: Any, field: str) -> List[Dict[str, Any]]:
    """JSON de latest-months → filas {date, epoch, <field>} (campo ``ref``)."""
    if not isinstance(payload, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for item in payload.get("value") or []:
        if not isinstance(item, dict):
            continue
        quality = str(item.get("quality") or "").strip().upper()
        if quality and quality not in _ACCEPTED_QUALITIES:
            continue
        value = _safe_float(item.get("value"))
        if value != value:
            continue
        date_text = _normalize_date(str(item.get("ref") or ""))
        epoch = _epoch_for(date_text)
        if epoch is None:
            continue
        if field.startswith("precip") and value < 0.0:
            value = 0.0
        rows.append({"date": date_text, "epoch": epoch, field: value})
    return rows


def merge_field_rows(batches: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Combina filas de varios campos/fuentes en una fila por fecha.

    Las fuentes llegan en orden de prioridad ascendente: para una misma
    fecha y campo, la última fuente (latest-months, más fresca) gana.
    """
    by_date: Dict[str, Dict[str, Any]] = {}
    for batch in batches:
        for row in batch:
            merged = by_date.setdefault(
                row["date"], {"date": row["date"], "epoch": row["epoch"]}
            )
            merged.update({k: v for k, v in row.items() if k not in ("date", "epoch")})
    return [by_date[key] for key in sorted(by_date)]

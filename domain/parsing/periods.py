"""Operaciones puras sobre intervalos de fechas de históricos."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, List, Tuple


def merge_date_periods(periods: Iterable[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Une intervalos solapados o consecutivos y elimina duplicados."""
    normalized = sorted(
        (start, end) if start <= end else (end, start)
        for start, end in periods
    )
    merged: List[Tuple[date, date]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged

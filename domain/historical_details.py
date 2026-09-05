"""Detalles del histórico que no caben en la tabla de métricas.

La tabla canónica de hitos da un valor y una fecha por métrica —«Racha
máxima, 46,8 km/h, 15/08/2026»—, pero las tarjetas enseñan además de dónde
soplaba ese viento y con qué intensidad cayó la lluvia. Eso sale del dataset
diario, no de la tabla, y hasta ahora se calculaba dentro de la vista
Streamlit: el frontend nuevo no tiene el DataFrame, solo la respuesta del
endpoint, así que estos cálculos viven aquí y viajan ya resueltos.

Es un porte literal de ``tabs/historical.py``: mismos umbrales, mismos
redondeos y el mismo criterio de «no atribuir una dirección a un día cuando
lo que hay son totales mensuales».
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

# Viento por debajo de este umbral se considera calma y no vota rumbo: con
# 0,5 km/h la veleta gira sola y ensuciaría la dirección predominante.
WIND_ROSE_CALM_THRESHOLD_KMH = 2.0
WIND_ROSE_SECTORS16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

NO_DIRECTION = ("-", "")


def _as_float(value: Any) -> float:
    """Número o NaN. Nunca lanza: los proveedores mandan de todo."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def direction_text(value: Any) -> str:
    """Dirección en 16 rumbos; ausencia explícita como ``-``."""
    direction = _as_float(value)
    if direction != direction:
        return "-"
    sector = int((((direction % 360.0) + 11.25) // 22.5)) % 16
    return WIND_ROSE_SECTORS16[sector]


def direction_parts(value: Any) -> Tuple[str, str]:
    """Rumbo cardinal y grados legibles: ``("NNE", "15°")``."""
    direction = _as_float(value)
    if direction != direction:
        return NO_DIRECTION
    normalized = direction % 360.0
    rounded = round(normalized, 1)
    degrees = f"{rounded:.0f}°" if math.isclose(rounded, round(rounded)) else f"{rounded:.1f}°"
    return direction_text(normalized), degrees


def _dominant_sector(daily) -> Optional[str]:
    """Rumbo más frecuente del periodo, saltándose las calmas."""
    if daily is None or "wind_dir_mean" not in getattr(daily, "columns", []):
        return None
    winds = (
        daily["wind_mean"].tolist()
        if "wind_mean" in daily.columns
        else [float("nan")] * len(daily)
    )
    counts = {sector: 0 for sector in WIND_ROSE_SECTORS16}
    for wind, direction in zip(winds, daily["wind_dir_mean"].tolist()):
        speed = _as_float(wind)
        degrees = _as_float(direction)
        if degrees != degrees:
            continue
        if speed == speed and speed < WIND_ROSE_CALM_THRESHOLD_KMH:
            continue
        counts[WIND_ROSE_SECTORS16[int((((degrees % 360.0) + 11.25) // 22.5)) % 16]] += 1
    if not sum(counts.values()):
        return None
    return max(WIND_ROSE_SECTORS16, key=lambda sector: counts[sector])


def gust_direction_parts(daily) -> Tuple[str, str]:
    """Dirección de la mayor racha, si el proveedor la conserva."""
    columns = getattr(daily, "columns", [])
    if daily is None or getattr(daily, "empty", True):
        return NO_DIRECTION
    if "gust_max" not in columns or "gust_dir_max" not in columns:
        return NO_DIRECTION

    import pandas as pd

    gusts = pd.to_numeric(daily["gust_max"], errors="coerce")
    directions = pd.to_numeric(daily["gust_dir_max"], errors="coerce")
    valid = gusts.dropna()
    if valid.empty:
        return NO_DIRECTION
    index = valid.idxmax()
    if pd.isna(directions.loc[index]):
        return NO_DIRECTION
    return direction_parts(directions.loc[index])


def predominant_direction_parts(daily) -> Tuple[str, str]:
    """Rumbo predominante y su dirección media dentro de ese sector.

    Los grados son la media circular de las muestras del sector ganador, no
    el centro del sector: decir «WSW · 245,2°» es más honesto que «247,5°».
    """
    dominant = _dominant_sector(daily)
    if not dominant or daily is None or "wind_dir_mean" not in daily.columns:
        return NO_DIRECTION

    import pandas as pd

    directions = pd.to_numeric(daily["wind_dir_mean"], errors="coerce")
    speeds = (
        pd.to_numeric(daily["wind_mean"], errors="coerce")
        if "wind_mean" in daily.columns
        else pd.Series(float("nan"), index=daily.index, dtype=float)
    )
    sector_index = WIND_ROSE_SECTORS16.index(dominant)
    samples = []
    for direction, speed in zip(directions, speeds):
        if pd.isna(direction):
            continue
        if not pd.isna(speed) and float(speed) < WIND_ROSE_CALM_THRESHOLD_KMH:
            continue
        if int((((float(direction) % 360.0) + 11.25) // 22.5)) % 16 == sector_index:
            samples.append(float(direction) % 360.0)
    if not samples:
        return dominant, ""
    radians = [math.radians(value) for value in samples]
    mean_direction = math.degrees(
        math.atan2(
            sum(math.sin(value) for value in radians),
            sum(math.cos(value) for value in radians),
        )
    ) % 360.0
    return dominant, direction_parts(mean_direction)[1]


def windiest_day_direction_parts(daily, date_txt: str = "") -> Tuple[str, str]:
    """Dirección del propio día más ventoso, no la predominante del periodo."""
    columns = getattr(daily, "columns", [])
    if daily is None or getattr(daily, "empty", True):
        return NO_DIRECTION
    if "wind_mean" not in columns or "wind_dir_mean" not in columns:
        return NO_DIRECTION

    import pandas as pd

    frame = daily.copy()
    has_dates = "date" in frame.columns
    if has_dates:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["wind_mean"] = pd.to_numeric(frame["wind_mean"], errors="coerce")
    frame["wind_dir_mean"] = pd.to_numeric(frame["wind_dir_mean"], errors="coerce")

    target = pd.to_datetime(str(date_txt), dayfirst=True, errors="coerce")
    if has_dates and not pd.isna(target):
        matching = frame.loc[frame["date"].dt.normalize() == target.normalize()]
        if not matching.empty:
            valid = matching.dropna(subset=["wind_mean"])
            row = (valid if not valid.empty else matching).iloc[0]
            return direction_parts(row["wind_dir_mean"])

    # Si lo que hay son totales mensuales no es lícito atribuir su dirección
    # a un día concreto.
    if has_dates and len(frame) > 1 and bool((frame["date"].dt.day == 1).all()):
        return NO_DIRECTION
    valid = frame.dropna(subset=["wind_mean"])
    if valid.empty:
        return NO_DIRECTION
    return direction_parts(valid.loc[valid["wind_mean"].idxmax(), "wind_dir_mean"])


def windiest_month_direction_parts(daily) -> Tuple[str, str]:
    """Dirección predominante dentro del mes de mayor viento medio."""
    columns = getattr(daily, "columns", [])
    if daily is None or getattr(daily, "empty", True):
        return NO_DIRECTION
    if not {"date", "wind_mean", "wind_dir_mean"}.issubset(set(columns)):
        return NO_DIRECTION

    import pandas as pd

    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["wind_mean"] = pd.to_numeric(frame["wind_mean"], errors="coerce")
    frame["wind_dir_mean"] = pd.to_numeric(frame["wind_dir_mean"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return NO_DIRECTION
    months = frame["date"].dt.to_period("M")
    means = frame.groupby(months)["wind_mean"].mean().dropna()
    if means.empty:
        return NO_DIRECTION
    return predominant_direction_parts(frame.loc[months == means.idxmax()])


def max_precip_rate(daily, unit_preferences=None) -> Optional[Tuple[str, str]]:
    """Mayor intensidad de lluvia del periodo y su fecha, ya formateadas."""
    if daily is None or getattr(daily, "empty", True):
        return None
    if "precip_rate_max" not in getattr(daily, "columns", []):
        return None

    import pandas as pd

    from utils.units import format_precip, normalize_unit_preferences

    rates = pd.to_numeric(daily["precip_rate_max"], errors="coerce")
    if not rates.notna().any():
        return None
    index = rates.idxmax()
    preferences = normalize_unit_preferences(unit_preferences)
    unit = preferences["precip"]
    decimals = 2 if unit == "in" else 1
    value = f"{format_precip(float(rates.loc[index]), unit, decimals=decimals)} {unit}/h"

    date_txt = ""
    column = "precip_rate_max_date" if "precip_rate_max_date" in daily.columns else "date"
    if column in daily.columns:
        stamp = pd.to_datetime(daily.loc[index, column], errors="coerce")
        if not pd.isna(stamp):
            date_txt = stamp.strftime("%d/%m/%Y")
    return value, date_txt


def month_year_label(date_txt: str) -> str:
    """«01/08/2026» → «Agosto 2026». El agregado mensual es de un mes, no de un día."""
    import pandas as pd

    from utils.i18n import month_name

    value = str(date_txt or "").strip()
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return value
    return f"{month_name(int(parsed.month))} {int(parsed.year)}"


def override_direction_parts(overrides: Any, metric_name: str) -> Tuple[str, str]:
    """Dirección que el proveedor adjunta aparte (Meteocat manda extremos propios)."""
    override = (overrides or {}).get(metric_name) if isinstance(overrides, dict) else None
    if not isinstance(override, dict):
        return NO_DIRECTION
    return direction_parts(override.get("Dirección"))


def build_details(
    daily,
    *,
    overrides: Any = None,
    unit_preferences: Optional[Dict[str, str]] = None,
    windiest_day_date: str = "",
    windiest_month_date: str = "",
) -> Dict[str, Any]:
    """Todo lo que las tarjetas necesitan del dataset diario, ya resuelto."""
    day_direction = windiest_day_direction_parts(daily, windiest_day_date)
    # Meteocat manda sus propios extremos: si trae dirección, manda la suya.
    override = override_direction_parts(overrides, "Día más ventoso (viento medio)")
    if override[0] != "-":
        day_direction = override

    rate = max_precip_rate(daily, unit_preferences)
    return {
        "gust_direction": dict(zip(("cardinal", "degrees"), gust_direction_parts(daily))),
        "predominant_direction": dict(
            zip(("cardinal", "degrees"), predominant_direction_parts(daily))
        ),
        "windiest_day_direction": dict(zip(("cardinal", "degrees"), day_direction)),
        "windiest_month_direction": dict(
            zip(("cardinal", "degrees"), windiest_month_direction_parts(daily))
        ),
        "max_precip_rate": rate[0] if rate else "",
        "max_precip_rate_date": rate[1] if rate else "",
        "windiest_month_label": month_year_label(windiest_month_date),
    }

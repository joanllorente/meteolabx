"""Derived meteorological series used by the Trends tab."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# pandas/numpy se importan lazy dentro de las funciones que los usan para
# no cargar ~0,7s de import al arrancar el backend (este módulo se importa
# a nivel de módulo desde server/routers/observations.py).
from models.thermodynamics import dewpoint_from_vapor_pressure, mixing_ratio
from models.trends import (
    calculate_trend,
    equivalent_potential_temperature,
    vapor_pressure,
)


DERIVED_ARRAY_FIELDS = (
    "pressures_abs",
    "theta_e",
    "mixing_ratios",
    "theta_e_trends",
    "mixing_ratio_trends",
    "pressure_trends",
    "vapor_pressures",
    "saturation_pressures",
    "theoretical_solar_radiations",
    "wind_u",
    "wind_v",
)


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _parallel_values(data: dict[str, Any], key: str, count: int) -> list[float]:
    values = data.get(key, [])
    if not isinstance(values, (list, tuple)):
        values = []
    return [_number(values[index]) if index < len(values) else float("nan") for index in range(count)]


def _canonical_daily_precip(data: dict[str, Any], count: int) -> list[float]:
    """Normaliza variantes de proveedor a acumulado diario canónico."""
    candidates = []
    for key in ("precips", "precip_accum_mm", "precip_step_mm"):
        values = _parallel_values(data, key, count)
        if any(not math.isnan(value) for value in values):
            candidates.append((key, values))
    if not candidates:
        return []

    key, values = next(
        ((name, vals) for name, vals in candidates if any(value > 0 for value in vals if not math.isnan(value))),
        candidates[0],
    )
    finite = [max(0.0, value) for value in values if not math.isnan(value)]
    if not finite:
        return values

    if key == "precip_accum_mm":
        is_accumulated = True
    elif key == "precip_step_mm":
        is_accumulated = False
    else:
        diffs = [finite[index] - finite[index - 1] for index in range(1, len(finite))]
        is_accumulated = not diffs or sum(diff >= -0.05 for diff in diffs) / len(diffs) >= 0.8

    running: list[float] = []
    total = 0.0
    previous: float | None = None
    for value in values:
        if math.isnan(value):
            running.append(float("nan"))
            continue
        value = max(0.0, value)
        if is_accumulated:
            if previous is None:
                total = 0.0
            else:
                total += value - previous if value >= previous else value
            previous = value
        else:
            total += value
        running.append(total)
    return running


def _typical_step_minutes(epochs: list[int], valid_mask: list[bool] | None = None) -> int:
    import numpy as np

    selected = [epoch for index, epoch in enumerate(epochs) if valid_mask is None or valid_mask[index]]
    if len(selected) < 2:
        return 0
    diffs = np.diff(np.asarray(selected, dtype=np.int64)) / 60.0
    diffs = diffs[diffs > 0]
    return max(1, int(round(float(np.median(diffs))))) if diffs.size else 0


def derive_trend_series(
    data: dict[str, Any],
    *,
    period: str,
    station_elevation: float = 0.0,
    station_lat: float = float("nan"),
    station_lon: float = float("nan"),
    station_tz: str = "",
    fallback_pressure_abs: float = float("nan"),
) -> dict[str, Any]:
    """Return a copy enriched with thermodynamic values and time derivatives.

    All derived arrays stay aligned with ``epochs``. Temperatures are Celsius,
    pressure is station-level hPa, theta-e is Kelvin, mixing ratio is g/kg,
    and derivatives are expressed per hour.
    """
    result = dict(data)
    result.pop("precip_accum_mm", None)
    result.pop("precip_step_mm", None)
    epochs: list[int] = []
    for value in data.get("epochs", []) or []:
        try:
            epochs.append(int(value))
        except (TypeError, ValueError):
            epochs.append(0)
    count = len(epochs)
    result["precips"] = _canonical_daily_precip(data, count)
    if not count:
        for field in DERIVED_ARRAY_FIELDS:
            result[field] = []
        result.update(theta_e_interval_minutes=0, mixing_ratio_interval_minutes=0, pressure_interval_minutes=180)
        return result

    temps = _parallel_values(data, "temps", count)
    humidities = _parallel_values(data, "humidities", count)
    # El punto de rocío es una derivada canónica: nunca se conserva el valor
    # del proveedor. Así tarjeta y series usan exactamente la misma fórmula.
    dewpoints = [
        dewpoint_from_vapor_pressure(vapor_pressure(temp, humidity))
        if not math.isnan(temp) and not math.isnan(humidity)
        else float("nan")
        for temp, humidity in zip(temps, humidities)
    ]

    pressures_abs = _parallel_values(data, "pressures_abs", count)
    if all(math.isnan(value) for value in pressures_abs):
        pressures_msl = _parallel_values(data, "pressures", count)
        elevation = _number(station_elevation)
        if math.isnan(elevation):
            elevation = 0.0
        pressure_factor = math.exp(-elevation / 8000.0)
        pressures_abs = [value * pressure_factor if not math.isnan(value) else float("nan") for value in pressures_msl]

    fallback = _number(fallback_pressure_abs)
    if not math.isnan(fallback):
        pressures_abs = [fallback if math.isnan(value) else value for value in pressures_abs]

    theta_e = []
    mixing_ratios = []
    vapor_pressures = []
    saturation_pressures = []
    for temp, humidity, pressure in zip(temps, humidities, pressures_abs):
        if any(math.isnan(value) for value in (temp, humidity, pressure)):
            theta_e.append(float("nan"))
            mixing_ratios.append(float("nan"))
        else:
            theta_e.append(equivalent_potential_temperature(temp, humidity, pressure))
            mixing_ratios.append(mixing_ratio(vapor_pressure(temp, humidity), pressure) * 1000.0)
        if math.isnan(temp):
            vapor_pressures.append(float("nan"))
            saturation_pressures.append(float("nan"))
        else:
            saturation_pressures.append(vapor_pressure(temp, 100.0))
            vapor_pressures.append(
                vapor_pressure(temp, humidity) if not math.isnan(humidity) else float("nan")
            )

    winds = _parallel_values(data, "winds", count)
    wind_dirs = _parallel_values(data, "wind_dirs", count)
    wind_u = []
    wind_v = []
    for speed, direction in zip(winds, wind_dirs):
        if math.isnan(speed) or math.isnan(direction):
            wind_u.append(float("nan"))
            wind_v.append(float("nan"))
            continue
        direction_rad = math.radians(direction)
        wind_u.append(-speed * math.sin(direction_rad))
        wind_v.append(-speed * math.cos(direction_rad))

    lat = _number(data.get("lat"))
    lon = _number(data.get("lon"))
    if math.isnan(lat):
        lat = _number(station_lat)
    if math.isnan(lon):
        lon = _number(station_lon)
    theoretical_solar = [float("nan")] * count
    result.update(
        sunrise_epoch=None,
        sunset_epoch=None,
        solar_altitude=None,
        solar_altitude_max=None,
        is_nighttime=None,
    )
    if str(period).lower() == "today" and not math.isnan(lat) and not math.isnan(lon):
        from models.radiation import (
            is_nighttime,
            max_solar_altitude_day_deg,
            solar_altitude_deg,
            solar_radiation_max_wm2,
            sunrise_sunset_datetimes,
        )

        elevation = _number(station_elevation)
        if math.isnan(elevation):
            elevation = 0.0
        theoretical_solar = [
            solar_radiation_max_wm2(
                lat, elevation, float(epoch), longitude_deg=lon, period_minutes=1.0,
            ) if epoch > 0 else float("nan")
            for epoch in epochs
        ]
        reference_epoch = next((epoch for epoch in reversed(epochs) if epoch > 0), 0)
        if reference_epoch:
            sunrise, sunset = sunrise_sunset_datetimes(
                lat, lon, float(reference_epoch), tz_name=station_tz,
            )
            result["sunrise_epoch"] = int(sunrise.timestamp()) if sunrise is not None else None
            result["sunset_epoch"] = int(sunset.timestamp()) if sunset is not None else None
            result["solar_altitude"] = solar_altitude_deg(lat, float(reference_epoch), lon)
            result["solar_altitude_max"] = max_solar_altitude_day_deg(lat, float(reference_epoch), lon)
            result["is_nighttime"] = is_nighttime(
                lat, float(reference_epoch), lon, tz_name=station_tz,
            )

    humidity_mask = [not math.isnan(value) for value in humidities]
    source_step = _typical_step_minutes(epochs)
    humidity_step = _typical_step_minutes(epochs, humidity_mask) or source_step
    minimum_interval = 180 if str(period).lower() == "synoptic" else 20
    thermo_interval = max(minimum_interval, humidity_step or minimum_interval)
    pressure_interval = 180
    import pandas as pd

    times = pd.DatetimeIndex([datetime.fromtimestamp(epoch, tz=timezone.utc) for epoch in epochs])

    result["pressures_abs"] = pressures_abs
    result["humidities"] = humidities
    result["dewpts"] = dewpoints
    result["theta_e"] = theta_e
    result["mixing_ratios"] = mixing_ratios
    result["theta_e_trends"] = calculate_trend(theta_e, times, interval_minutes=thermo_interval).tolist()
    result["mixing_ratio_trends"] = calculate_trend(
        mixing_ratios, times, interval_minutes=thermo_interval,
    ).tolist()
    result["pressure_trends"] = calculate_trend(
        pressures_abs, times, interval_minutes=pressure_interval,
    ).tolist()
    result["vapor_pressures"] = vapor_pressures
    result["saturation_pressures"] = saturation_pressures
    result["theoretical_solar_radiations"] = theoretical_solar
    result["wind_u"] = wind_u
    result["wind_v"] = wind_v
    result["theta_e_interval_minutes"] = thermo_interval
    result["mixing_ratio_interval_minutes"] = thermo_interval
    result["pressure_interval_minutes"] = pressure_interval
    return result

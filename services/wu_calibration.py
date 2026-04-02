"""
Calibración persistente de sensores para estaciones Weather Underground.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import pandas as pd

from models import vapor_pressure, dewpoint_from_vapor_pressure


WU_CALIBRATION_SPECS: Dict[str, Dict[str, Any]] = {
    "barometer": {"min": -20.0, "max": 20.0, "unit": "hPa", "decimals": 0},
    "wind_vane": {"min": -180.0, "max": 180.0, "unit": "°", "decimals": 0},
    "thermometer": {"min": -5.0, "max": 5.0, "unit": "°C", "decimals": 1},
    "hygrometer": {"min": -20.0, "max": 20.0, "unit": "%", "decimals": 1},
    "anemometer": {"min": -20.0, "max": 20.0, "unit": "km/h", "decimals": 1},
    "rain_gauge": {"min": -20.0, "max": 20.0, "unit": "mm", "decimals": 1},
    "pyranometer": {"min": -400.0, "max": 400.0, "unit": "W/m²", "decimals": 1},
}

WU_CALIBRATION_ORDER = (
    "barometer",
    "wind_vane",
    "thermometer",
    "hygrometer",
    "anemometer",
    "rain_gauge",
    "pyranometer",
)


def _nan() -> float:
    return float("nan")


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return _nan()
        return float(value)
    except (TypeError, ValueError):
        return _nan()


def _is_nan(value: Any) -> bool:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return True
    return num != num


def _round_value(value: float, decimals: int) -> float:
    factor = 10.0 ** int(decimals)
    return round(float(value) * factor) / factor


def default_wu_calibration() -> Dict[str, float]:
    return {key: 0.0 for key in WU_CALIBRATION_ORDER}


def normalize_wu_calibration(raw: Mapping[str, Any] | None) -> Dict[str, float]:
    normalized = default_wu_calibration()
    if not isinstance(raw, Mapping):
        return normalized

    for key, spec in WU_CALIBRATION_SPECS.items():
        value = _safe_float(raw.get(key, 0.0))
        if _is_nan(value):
            value = 0.0
        value = max(float(spec["min"]), min(float(spec["max"]), float(value)))
        normalized[key] = _round_value(value, int(spec.get("decimals", 1)))
    return normalized


def _apply_scalar(
    payload: Dict[str, Any],
    key: str,
    offset: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    wrap: float | None = None,
) -> None:
    if key not in payload:
        return
    value = _safe_float(payload.get(key))
    if _is_nan(value):
        return
    value = float(value) + float(offset)
    if wrap is not None:
        value = value % float(wrap)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    payload[key] = float(value)


def _apply_array(
    values: Any,
    offset: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    wrap: float | None = None,
) -> Any:
    if not isinstance(values, list):
        return values
    adjusted = []
    for item in values:
        value = _safe_float(item)
        if _is_nan(value):
            adjusted.append(_nan())
            continue
        value = float(value) + float(offset)
        if wrap is not None:
            value = value % float(wrap)
        if minimum is not None:
            value = max(float(minimum), value)
        if maximum is not None:
            value = min(float(maximum), value)
        adjusted.append(float(value))
    return adjusted


def _recompute_dewpoint(temp_c: float, rh_pct: float) -> float:
    temp = _safe_float(temp_c)
    rh = _safe_float(rh_pct)
    if _is_nan(temp) or _is_nan(rh):
        return _nan()
    rh = max(0.0, min(100.0, float(rh)))
    if rh <= 0.0:
        return _nan()
    try:
        return float(dewpoint_from_vapor_pressure(vapor_pressure(float(temp), float(rh))))
    except Exception:
        return _nan()


def _any_valid(values: Any) -> bool:
    if not isinstance(values, list):
        return False
    return any(not _is_nan(_safe_float(item)) for item in values)


def _has_value(value: Any) -> bool:
    return not _is_nan(_safe_float(value))


def detect_wu_sensor_presence(base: Mapping[str, Any] | None, series: Mapping[str, Any] | None = None) -> Dict[str, bool]:
    base = base if isinstance(base, Mapping) else {}
    series = series if isinstance(series, Mapping) else {}

    return {
        "barometer": _has_value(base.get("p_hpa")) or _has_value(base.get("pressure_3h_ago")) or _any_valid(series.get("pressures")) or _any_valid(series.get("pressures_abs")),
        "wind_vane": _has_value(base.get("wind_dir_deg")) or _any_valid(series.get("wind_dirs")),
        "thermometer": _has_value(base.get("Tc")) or _has_value(base.get("temp_max")) or _has_value(base.get("temp_min")) or _any_valid(series.get("temps")),
        "hygrometer": _has_value(base.get("RH")) or _has_value(base.get("rh_max")) or _has_value(base.get("rh_min")) or _any_valid(series.get("humidities")),
        "anemometer": _has_value(base.get("wind")) or _has_value(base.get("gust")) or _has_value(base.get("gust_max")) or _any_valid(series.get("winds")) or _any_valid(series.get("gusts")),
        "rain_gauge": _has_value(base.get("precip_total")) or _has_value(base.get("precip_rate")) or _any_valid(series.get("precips")),
        "pyranometer": _has_value(base.get("solar_radiation")) or _any_valid(series.get("solar_radiations")),
    }


def apply_wu_current_calibration(base: Mapping[str, Any] | None, calibration: Mapping[str, Any] | None) -> Dict[str, Any]:
    payload = dict(base) if isinstance(base, Mapping) else {}
    cal = normalize_wu_calibration(calibration)

    _apply_scalar(payload, "Tc", cal["thermometer"])
    _apply_scalar(payload, "temp_max", cal["thermometer"])
    _apply_scalar(payload, "temp_min", cal["thermometer"])

    _apply_scalar(payload, "RH", cal["hygrometer"], minimum=0.0, maximum=100.0)
    _apply_scalar(payload, "rh_max", cal["hygrometer"], minimum=0.0, maximum=100.0)
    _apply_scalar(payload, "rh_min", cal["hygrometer"], minimum=0.0, maximum=100.0)

    _apply_scalar(payload, "p_hpa", cal["barometer"])
    _apply_scalar(payload, "pressure_3h_ago", cal["barometer"])

    _apply_scalar(payload, "wind", cal["anemometer"], minimum=0.0)
    _apply_scalar(payload, "gust", cal["anemometer"], minimum=0.0)
    _apply_scalar(payload, "gust_max", cal["anemometer"], minimum=0.0)

    _apply_scalar(payload, "wind_dir_deg", cal["wind_vane"], wrap=360.0)
    _apply_scalar(payload, "precip_total", cal["rain_gauge"], minimum=0.0)
    _apply_scalar(payload, "solar_radiation", cal["pyranometer"], minimum=0.0)

    if (_has_value(payload.get("Tc")) and _has_value(payload.get("RH"))):
        payload["Td"] = _recompute_dewpoint(payload.get("Tc"), payload.get("RH"))

    payload["_wu_calibration"] = cal
    return payload


def apply_wu_series_calibration(series: Mapping[str, Any] | None, calibration: Mapping[str, Any] | None) -> Dict[str, Any]:
    payload = dict(series) if isinstance(series, Mapping) else {}
    cal = normalize_wu_calibration(calibration)

    payload["temps"] = _apply_array(payload.get("temps", []), cal["thermometer"])
    payload["humidities"] = _apply_array(payload.get("humidities", []), cal["hygrometer"], minimum=0.0, maximum=100.0)
    payload["pressures"] = _apply_array(payload.get("pressures", []), cal["barometer"])
    payload["pressures_abs"] = _apply_array(payload.get("pressures_abs", []), cal["barometer"])
    payload["winds"] = _apply_array(payload.get("winds", []), cal["anemometer"], minimum=0.0)
    payload["gusts"] = _apply_array(payload.get("gusts", []), cal["anemometer"], minimum=0.0)
    payload["wind_dirs"] = _apply_array(payload.get("wind_dirs", []), cal["wind_vane"], wrap=360.0)
    payload["solar_radiations"] = _apply_array(payload.get("solar_radiations", []), cal["pyranometer"], minimum=0.0)
    payload["precips"] = _apply_array(payload.get("precips", []), cal["rain_gauge"], minimum=0.0)

    temps = payload.get("temps", [])
    humidities = payload.get("humidities", [])
    dewpts = []
    if isinstance(temps, list):
        for idx, temp in enumerate(temps):
            rh = humidities[idx] if isinstance(humidities, list) and idx < len(humidities) else _nan()
            dewpts.append(_recompute_dewpoint(temp, rh))
        payload["dewpts"] = dewpts

    payload["_wu_calibration"] = cal
    return payload


def apply_wu_daily_history_calibration(frame: pd.DataFrame, calibration: Mapping[str, Any] | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return frame

    cal = normalize_wu_calibration(calibration)
    out = frame.copy()

    for column in ("temp_mean", "temp_max", "temp_min", "temp_abs_max", "temp_abs_min"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce") + cal["thermometer"]

    if "wind_mean" in out.columns:
        out["wind_mean"] = (pd.to_numeric(out["wind_mean"], errors="coerce") + cal["anemometer"]).clip(lower=0.0)
    if "gust_max" in out.columns:
        out["gust_max"] = (pd.to_numeric(out["gust_max"], errors="coerce") + cal["anemometer"]).clip(lower=0.0)
    if "precip_total" in out.columns:
        out["precip_total"] = (pd.to_numeric(out["precip_total"], errors="coerce") + cal["rain_gauge"]).clip(lower=0.0)
    return out

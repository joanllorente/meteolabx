"""
Conversión y formateo de unidades de visualización.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


DEFAULT_UNIT_PREFERENCES: Dict[str, str] = {
    "temperature": "c",
    "wind": "kmh",
    "pressure": "hpa",
    "precip": "mm",
    "radiation": "wm2",
}


UNIT_OPTIONS: Dict[str, tuple[str, ...]] = {
    "temperature": ("k", "c", "f"),
    "wind": ("kmh", "ms", "mph", "kt"),
    "pressure": ("hpa", "mmhg", "inhg"),
    "precip": ("mm", "in"),
    "radiation": ("wm2", "mjm2", "kwhm2"),
}


UNIT_LABELS: Dict[str, Dict[str, str]] = {
    "temperature": {"k": "K", "c": "°C", "f": "°F"},
    "wind": {"kmh": "km/h", "ms": "m/s", "mph": "mph", "kt": "kt"},
    "pressure": {"hpa": "hPa", "mmhg": "mmHg", "inhg": "inHg"},
    "precip": {"mm": "mm", "in": "in"},
    "radiation": {"wm2": "W/m²", "mjm2": "MJ/m²", "kwhm2": "kWh/m²"},
}


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def is_nan(value: Any) -> bool:
    num = _safe_float(value)
    return num != num


def normalize_unit_preferences(raw: Mapping[str, Any] | None) -> Dict[str, str]:
    normalized = dict(DEFAULT_UNIT_PREFERENCES)
    if not isinstance(raw, Mapping):
        return normalized

    for category, default_value in DEFAULT_UNIT_PREFERENCES.items():
        candidate = str(raw.get(category, default_value)).strip().lower()
        if candidate in UNIT_OPTIONS[category]:
            normalized[category] = candidate
    return normalized


def _unit_label(category: str, unit: str) -> str:
    normalized = normalize_unit_preferences({category: unit})
    selected = normalized[category]
    return UNIT_LABELS[category][selected]


def temperature_unit_label(unit: str) -> str:
    return _unit_label("temperature", unit)


def wind_unit_label(unit: str) -> str:
    return _unit_label("wind", unit)


def pressure_unit_label(unit: str) -> str:
    return _unit_label("pressure", unit)


def precip_unit_label(unit: str) -> str:
    return _unit_label("precip", unit)


def radiation_unit_label(unit: str) -> str:
    return _unit_label("radiation", unit)


def radiation_energy_unit_label(unit: str) -> str:
    normalized = normalize_unit_preferences({"radiation": unit})["radiation"]
    if normalized == "kwhm2":
        return "kWh/m²"
    return "MJ/m²"


def convert_temperature(value_c: Any, unit: str) -> float:
    value = _safe_float(value_c)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"temperature": unit})["temperature"]
    if normalized == "k":
        return float(value) + 273.15
    if normalized == "f":
        return (float(value) * 9.0 / 5.0) + 32.0
    return float(value)


def convert_temperature_delta(value_c: Any, unit: str) -> float:
    value = _safe_float(value_c)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"temperature": unit})["temperature"]
    if normalized == "f":
        return float(value) * 9.0 / 5.0
    return float(value)


def convert_wind(value_kmh: Any, unit: str) -> float:
    value = _safe_float(value_kmh)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"wind": unit})["wind"]
    if normalized == "ms":
        return float(value) / 3.6
    if normalized == "mph":
        return float(value) * 0.6213711922
    if normalized == "kt":
        return float(value) * 0.5399568035
    return float(value)


def convert_pressure(value_hpa: Any, unit: str) -> float:
    value = _safe_float(value_hpa)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"pressure": unit})["pressure"]
    if normalized == "mmhg":
        return float(value) * 0.750061683
    if normalized == "inhg":
        return float(value) * 0.0295299831
    return float(value)


def convert_precip(value_mm: Any, unit: str) -> float:
    value = _safe_float(value_mm)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"precip": unit})["precip"]
    if normalized == "in":
        return float(value) / 25.4
    return float(value)


def convert_radiation(value_wm2: Any, unit: str) -> float:
    value = _safe_float(value_wm2)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"radiation": unit})["radiation"]
    if normalized == "mjm2":
        return float(value) * 0.0036
    if normalized == "kwhm2":
        return float(value) / 1000.0
    return float(value)


def convert_radiation_energy(value_mj_m2: Any, unit: str) -> float:
    value = _safe_float(value_mj_m2)
    if is_nan(value):
        return float("nan")
    normalized = normalize_unit_preferences({"radiation": unit})["radiation"]
    if normalized == "kwhm2":
        return float(value) / 3.6
    return float(value)


def _format_number(value: Any, decimals: int = 1) -> str:
    number = _safe_float(value)
    if is_nan(number):
        return "—"
    return f"{float(number):.{int(decimals)}f}"


def format_temperature(value_c: Any, unit: str, decimals: int = 1) -> str:
    return _format_number(convert_temperature(value_c, unit), decimals=decimals)


def format_temperature_delta(value_c: Any, unit: str, decimals: int = 1) -> str:
    return _format_number(convert_temperature_delta(value_c, unit), decimals=decimals)


def format_wind(value_kmh: Any, unit: str, decimals: int = 1) -> str:
    return _format_number(convert_wind(value_kmh, unit), decimals=decimals)


def format_pressure(value_hpa: Any, unit: str, decimals: int = 1) -> str:
    return _format_number(convert_pressure(value_hpa, unit), decimals=decimals)


def format_precip(value_mm: Any, unit: str, decimals: int = 1) -> str:
    return _format_number(convert_precip(value_mm, unit), decimals=decimals)


def format_radiation(value_wm2: Any, unit: str, decimals: int = 0) -> str:
    normalized = normalize_unit_preferences({"radiation": unit})["radiation"]
    if normalized != "wm2" and decimals == 0:
        decimals = 2
    return _format_number(convert_radiation(value_wm2, unit), decimals=decimals)


def format_radiation_energy(value_mj_m2: Any, unit: str, decimals: int = 2) -> str:
    return _format_number(convert_radiation_energy(value_mj_m2, unit), decimals=decimals)

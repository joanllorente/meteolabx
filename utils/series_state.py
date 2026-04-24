"""
Helpers comunes para almacenar y reconstruir series temporales en session_state.
"""

from __future__ import annotations

from typing import Any, Optional


SERIES_STATE_FIELD_MAP = {
    "epochs": "epochs",
    "temps": "temps",
    "humidities": "humidities",
    "dewpts": "dewpts",
    "pressures": "pressures_abs",
    "uv_indexes": "uv_indexes",
    "solar_radiations": "solar_radiations",
    "winds": "winds",
    "gusts": "gusts",
    "wind_dirs": "wind_dirs",
    "precips": "precips",
}


def empty_chart_series() -> dict[str, list[Any] | bool]:
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures_abs": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "precips": [],
        "has_data": False,
    }


def normalize_chart_series(payload: Optional[dict], *, pressure_key: str = "pressures_abs") -> dict:
    series = payload if isinstance(payload, dict) else {}
    normalized = empty_chart_series()
    normalized["epochs"] = list(series.get("epochs", []))
    normalized["temps"] = list(series.get("temps", []))
    normalized["humidities"] = list(series.get("humidities", []))
    normalized["dewpts"] = list(series.get("dewpts", []))
    normalized["pressures_abs"] = list(series.get(pressure_key, []))
    normalized["uv_indexes"] = list(series.get("uv_indexes", []))
    normalized["solar_radiations"] = list(series.get("solar_radiations", []))
    normalized["winds"] = list(series.get("winds", []))
    normalized["gusts"] = list(series.get("gusts", []))
    normalized["wind_dirs"] = list(series.get("wind_dirs", []))
    normalized["precips"] = list(
        series.get(
            "precips",
            series.get("precip_accum_mm", series.get("precip_step_mm", [])),
        )
    )
    normalized["has_data"] = bool(series.get("has_data", False))
    return normalized


def store_series_state(state: Any, prefix: str, normalized: dict) -> dict:
    for state_suffix, normalized_key in SERIES_STATE_FIELD_MAP.items():
        state[f"{prefix}_{state_suffix}"] = normalized[normalized_key]
    state[f"has_{prefix}_data"] = normalized["has_data"]
    return normalized


def store_chart_series(state: Any, payload: Optional[dict], *, pressure_key: str = "pressures_abs") -> dict:
    normalized = normalize_chart_series(payload, pressure_key=pressure_key)
    return store_series_state(state, "chart", normalized)


def store_trend_hourly_series(state: Any, payload: Optional[dict], *, pressure_key: str = "pressures_abs") -> dict:
    normalized = normalize_chart_series(payload, pressure_key=pressure_key)
    return store_series_state(state, "trend_hourly", normalized)


def series_from_state(state: Any, prefix: str = "chart", *, pressure_key: str = "pressures_abs") -> dict:
    payload = {
        normalized_key if state_suffix != "pressures" else pressure_key: state.get(f"{prefix}_{state_suffix}", [])
        for state_suffix, normalized_key in SERIES_STATE_FIELD_MAP.items()
    }
    return normalize_chart_series(
        {
            **payload,
            "has_data": state.get(f"has_{prefix}_data", prefix == "chart" and state.get("has_chart_data", False)),
        },
        pressure_key=pressure_key,
    )

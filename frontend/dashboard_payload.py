"""Canonical FastAPI dashboard payload consumed by Streamlit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class DashboardPayload:
    observation: Dict[str, Any]
    derivatives: Dict[str, Any]
    warnings: list[Dict[str, Any]]
    station: Dict[str, Any]
    daily_extremes: Dict[str, Any]
    series: Dict[str, Any]
    recent_series: Optional[Dict[str, Any]] = None


def build_dashboard_payload(
    processed_response: Mapping[str, Any],
    *,
    recent_series: Optional[Mapping[str, Any]] = None,
) -> DashboardPayload:
    """Copy the public FastAPI blocks without flattening or aliases."""
    warnings = processed_response.get("warnings")
    return DashboardPayload(
        observation=_mapping(processed_response.get("observation")),
        derivatives=_mapping(processed_response.get("derivatives")),
        warnings=[dict(item) for item in warnings or [] if isinstance(item, Mapping)],
        station=_mapping(processed_response.get("station")),
        daily_extremes=_mapping(processed_response.get("daily_extremes")),
        series=_mapping(processed_response.get("series")),
        recent_series=(
            dict(recent_series) if isinstance(recent_series, Mapping) else None
        ),
    )

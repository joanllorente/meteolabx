"""Apply observation side effects without coupling calculation to Streamlit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping


@dataclass(frozen=True)
class ObservationEffectHandlers:
    render_warning: Callable[[Any], str]
    emit_warning: Callable[[str], None]
    log_warning: Callable[[str], None]
    store_series: Callable[[MutableMapping[str, Any], str, dict], Any]
    set_chart_owner: Callable[[str, str], None]
    set_trend_hourly_owner: Callable[[str, str], None]
    clear_trend_hourly_owner: Callable[[], None]


def apply_observation_effects(
    result: Any,
    state: MutableMapping[str, Any],
    handlers: ObservationEffectHandlers,
) -> None:
    """Apply the client-owned effects described by a pure processing result."""
    state.update(result.session_updates)

    for warning in result.warnings:
        message = handlers.render_warning(warning)
        if message:
            handlers.emit_warning(message)
            handlers.log_warning(message)

    handlers.store_series(state, "chart", result.chart_series)
    if result.chart_series_owner is not None:
        handlers.set_chart_owner(*result.chart_series_owner)

    if result.trend_hourly_series is not None:
        handlers.store_series(state, "trend_hourly", result.trend_hourly_series)
    if (
        result.trend_hourly_owner_action == "set"
        and result.trend_hourly_owner is not None
    ):
        handlers.set_trend_hourly_owner(*result.trend_hourly_owner)
    elif result.trend_hourly_owner_action == "clear":
        handlers.clear_trend_hourly_owner()

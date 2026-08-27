"""Control de eventos de navegación enviados a estadísticas internas.

Streamlit reejecuta el script por muchos motivos que no son navegación. Este
módulo mantiene en el estado de la sesión la última sección realmente vista
para contar transiciones, no reruns.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any


TRACKED_USAGE_SECTIONS = (
    "observation",
    "trends",
    "historical",
    "map.stations",
    "map.temperature",
    "map.wind",
    "map.precipitation",
    "forecast.streamlit",
    "forecast.direct",
    "ranking",
)
_TRACKED_USAGE_SECTION_SET = frozenset(TRACKED_USAGE_SECTIONS)
_STATION_REQUIRED_TABS = frozenset({"observation", "trends", "historical"})
_MAP_VIEW_MODES = frozenset({"stations", "temperature", "wind", "precipitation"})
_TRACKER_INITIALIZED_KEY = "_usage_section_tracker_initialized"
_LAST_FINGERPRINT_KEY = "_usage_section_last_fingerprint"


def usage_section_state(
    active_tab: str,
    *,
    connected: bool,
    map_view_mode: str = "stations",
) -> tuple[str, bool]:
    """Devuelve ``(sección, elegible)`` para la pantalla visible."""
    tab = str(active_tab or "").strip().lower()
    if tab in _STATION_REQUIRED_TABS:
        return tab, bool(connected)
    if tab == "map":
        mode = str(map_view_mode or "stations").strip().lower()
        if mode not in _MAP_VIEW_MODES:
            mode = "stations"
        return f"map.{mode}", True
    if tab == "forecast":
        # Fallback por si el enlace externo no llega a instalarse y Streamlit
        # termina renderizando su pestaña embebida.
        return "forecast.streamlit", True
    if tab == "ranking":
        return "ranking", True
    return "", False


def track_usage_section_transition(
    state: MutableMapping[str, Any],
    *,
    active_tab: str,
    connected: bool,
    map_view_mode: str = "stations",
    sender: Callable[[str], None],
) -> bool:
    """Registra una entrada si representa una transición contable.

    La primera pantalla de Ranking se marca como vista pero se omite: es la
    apertura automática cuando no hay autoconexión. Las pestañas de estación
    solo son elegibles mientras existe una conexión activa.
    """
    section, eligible = usage_section_state(
        active_tab,
        connected=connected,
        map_view_mode=map_view_mode,
    )
    if not section:
        return False

    fingerprint = f"{section}|{int(eligible)}"
    initialized = bool(state.get(_TRACKER_INITIALIZED_KEY, False))
    previous = str(state.get(_LAST_FINGERPRINT_KEY, "") or "")
    state[_TRACKER_INITIALIZED_KEY] = True
    state[_LAST_FINGERPRINT_KEY] = fingerprint

    if previous == fingerprint:
        return False
    if not initialized and section == "ranking":
        return False
    if not eligible or section not in _TRACKED_USAGE_SECTION_SET:
        return False

    sender(section)
    return True

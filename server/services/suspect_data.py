"""
Cuarentena de variables sospechosas por estación y día local.

Cuando un control de plausibilidad pilla a un sensor dando datos implausibles
(el primero: la intensidad de precipitación, ``domain.precip_quality``), la
variable afectada queda en cuarentena para esa estación y ESE día local. La
cuarentena tiene dos consumidores:

- El ranking, que deja fuera la variable en cuarentena para no clasificar
  estaciones por un dato roto.
- La ficha de la estación, que avisa al visitante de que ese día el sensor
  podría estar dando datos incorrectos.

Es un registro en memoria del proceso, y por diseño: la detección nace de la
serie sub-horaria que se descarga al abrir la estación, así que una estación
que nadie ha abierto todavía no está marcada. Se purga por antigüedad de día
local para que no crezca sin límite.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Variables que pueden entrar en cuarentena. Son los nombres que usa el
# ranking, para que el consumidor no tenga que traducir nada.
PRECIPITATION = "rain"
# La temperatura entra como una sola variable aunque el ranking la publique en
# tres campos (máxima, mínima y actual): un termómetro que miente no lo hace en
# uno solo de ellos.
TEMPERATURE = "temperature"

# Días locales que se conservan. Cuatro es lo mismo que guarda el store del
# ranking (``RankingStore._KEEP_DAYS``): una fecha local vive ~50 h en algún
# punto del planeta.
_KEEP_DAYS = 4

_Key = Tuple[str, str, str]  # (proveedor, station_id, día local)

_lock = threading.Lock()
_flags: Dict[_Key, Dict[str, Dict[str, Any]]] = {}


def _normalise(provider: str, station_id: str, day: str) -> Optional[_Key]:
    provider_token = str(provider or "").strip().upper()
    station_token = str(station_id or "").strip()
    day_token = str(day or "").strip()
    if not provider_token or not station_token or not day_token:
        return None
    return provider_token, station_token, day_token


def flag(
    provider: str,
    station_id: str,
    day: str,
    variable: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """Pone ``variable`` en cuarentena para esa estación y ese día local."""
    key = _normalise(provider, station_id, day)
    if key is None:
        return
    with _lock:
        known = key in _flags and variable in _flags[key]
        _flags.setdefault(key, {})[str(variable)] = dict(params or {})
        _prune_locked()
    if not known:
        logger.info(
            "cuarentena: %s %s (%s) → %s %s",
            key[0], key[1], key[2], variable, params or {},
        )


def flags_for(provider: str, station_id: str, day: str) -> Dict[str, Dict[str, Any]]:
    """Variables en cuarentena de esa estación ese día (``{variable: params}``)."""
    key = _normalise(provider, station_id, day)
    if key is None:
        return {}
    with _lock:
        return {name: dict(params) for name, params in _flags.get(key, {}).items()}


def is_flagged(provider: str, station_id: str, day: str, variable: str) -> bool:
    key = _normalise(provider, station_id, day)
    if key is None:
        return False
    with _lock:
        return str(variable) in _flags.get(key, {})


def _prune_locked() -> None:
    """Conserva solo los ``_KEEP_DAYS`` días locales más recientes."""
    days = sorted({key[2] for key in _flags}, reverse=True)
    if len(days) <= _KEEP_DAYS:
        return
    stale = set(days[_KEEP_DAYS:])
    for key in [key for key in _flags if key[2] in stale]:
        _flags.pop(key, None)


def clear() -> None:
    """Vacía el registro (tests)."""
    with _lock:
        _flags.clear()

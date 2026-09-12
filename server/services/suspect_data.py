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
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Variables que pueden entrar en cuarentena. Son los nombres que usa el
# ranking, para que el consumidor no tenga que traducir nada.
PRECIPITATION = "rain"
# La temperatura entra como una sola variable aunque el ranking la publique en
# tres campos (máxima, mínima y actual): un termómetro que miente no lo hace en
# uno solo de ellos.
TEMPERATURE = "temperature"
# Una racha máxima aislada puede ser un impulso espurio del anemómetro. Se
# guarda aparte para retirar solo la racha, no el viento sostenido actual.
WIND = "wind"

# Días locales que se conservan. Cuatro es lo mismo que guarda el store del
# ranking (``RankingStore._KEEP_DAYS``): una fecha local vive ~50 h en algún
# punto del planeta.
_KEEP_DAYS = 4

_Key = Tuple[str, str, str]  # (proveedor, station_id, día local)

_lock = threading.Lock()
_flags: Dict[_Key, Dict[str, Dict[str, Any]]] = {}

# Historial por (proveedor, estación, variable). Sobrevive a la purga de días
# de ``_flags`` porque responde a otra pregunta: no «¿está marcada hoy?» sino
# «¿cuánto lleva marcada?». Sin él no hay forma de distinguir el sensor que
# falló una tarde del que lleva semanas roto y nadie ha arreglado.
_HistoryKey = Tuple[str, str, str]  # (proveedor, station_id, variable)
_history: Dict[_HistoryKey, Dict[str, Any]] = {}


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
    now: Optional[float] = None,
) -> None:
    """Pone ``variable`` en cuarentena para esa estación y ese día local."""
    key = _normalise(provider, station_id, day)
    if key is None:
        return
    stamp = float(now if now is not None else time.time())
    with _lock:
        known = key in _flags and variable in _flags[key]
        _flags.setdefault(key, {})[str(variable)] = dict(params or {})
        historia = _history.setdefault(
            (key[0], key[1], str(variable)),
            {"days": set(), "first": stamp, "last": stamp, "reasons": {}},
        )
        historia["days"].add(key[2])
        historia["last"] = max(float(historia["last"]), stamp)
        historia["first"] = min(float(historia["first"]), stamp)
        motivo = str((params or {}).get("reason") or "")
        if motivo:
            historia["reasons"][motivo] = int(historia["reasons"].get(motivo, 0)) + 1
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
        _history.clear()


def active(day: str) -> List[Dict[str, Any]]:
    """Lo que está en cuarentena ese día local, con su historial acumulado."""
    day_token = str(day or "").strip()
    filas: List[Dict[str, Any]] = []
    with _lock:
        for (provider, station_id, jornada), variables in _flags.items():
            if jornada != day_token:
                continue
            for variable, params in variables.items():
                historia = _history.get((provider, station_id, variable), {})
                dias = historia.get("days") or set()
                filas.append({
                    "provider": provider,
                    "station_id": station_id,
                    "variable": variable,
                    "day": jornada,
                    "params": dict(params),
                    "days_total": len(dias),
                    "first_seen": historia.get("first"),
                    "last_seen": historia.get("last"),
                    "reasons": dict(historia.get("reasons") or {}),
                })
    filas.sort(key=lambda fila: (-fila["days_total"], fila["station_id"]))
    return filas


def history() -> List[Dict[str, Any]]:
    """Todo lo que ha pasado por cuarentena, se siga marcando o no."""
    with _lock:
        return sorted(
            (
                {
                    "provider": provider,
                    "station_id": station_id,
                    "variable": variable,
                    "days_total": len(datos.get("days") or set()),
                    "first_seen": datos.get("first"),
                    "last_seen": datos.get("last"),
                    "reasons": dict(datos.get("reasons") or {}),
                }
                for (provider, station_id, variable), datos in _history.items()
            ),
            key=lambda fila: (-fila["days_total"], fila["station_id"]),
        )


def export_state() -> Dict[str, Any]:
    """Estado serializable, para viajar en el snapshot del ranking."""
    with _lock:
        return {
            "flags": [
                [provider, station_id, day, variable, params]
                for (provider, station_id, day), variables in _flags.items()
                for variable, params in variables.items()
            ],
            "history": [
                [provider, station_id, variable, sorted(datos.get("days") or set()),
                 datos.get("first"), datos.get("last"), dict(datos.get("reasons") or {})]
                for (provider, station_id, variable), datos in _history.items()
            ],
        }


def import_state(payload: Any) -> int:
    """Restaura un estado exportado; un payload ilegible deja el registro vacío."""
    if not isinstance(payload, dict):
        return 0
    flags: Dict[_Key, Dict[str, Dict[str, Any]]] = {}
    for fila in payload.get("flags") or []:
        try:
            provider, station_id, day, variable, params = fila
            flags.setdefault((str(provider), str(station_id), str(day)), {})[
                str(variable)
            ] = dict(params or {})
        except (TypeError, ValueError):
            continue
    historia: Dict[_HistoryKey, Dict[str, Any]] = {}
    for fila in payload.get("history") or []:
        try:
            provider, station_id, variable, dias, primero, ultimo, motivos = fila
            historia[(str(provider), str(station_id), str(variable))] = {
                "days": set(str(d) for d in dias or []),
                "first": float(primero), "last": float(ultimo),
                "reasons": dict(motivos or {}),
            }
        except (TypeError, ValueError):
            continue
    with _lock:
        _flags.clear()
        _flags.update(flags)
        _history.clear()
        _history.update(historia)
    return len(historia)

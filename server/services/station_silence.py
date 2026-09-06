"""
Estaciones que el ranking dejó de recibir.

El bulk del ranking recorre cada hora las redes que publican de golpe y
devuelve SOLO las estaciones que traen dato. Una que estaba y deja de salir
lleva su sensor apagado: Monte Carpegna (MeteoHub) se pasó dos días así,
publicando una ficha vacía y ocupando su sitio en el mapa mientras sus 119
vecinas de ``dpcn-marche`` seguían reportando.

Pasadas 24 h sin aparecer se oculta de la búsqueda y del mapa, y vuelve sola
en cuanto el bulk la ve de nuevo.

Tres reglas hacen que esto sea seguro:

- **Solo se juzga a quien el bulk observa.** Una estación que nunca hemos
  visto en un bulk no se oculta jamás: puede estar sanísima y ser
  simplemente de una red que el ranking no recorre (NWS entero, el 85 % de
  IEM, las de credencial propia). El silencio se mide contra un avistamiento
  previo, nunca contra la ausencia.

- **El silencio se mide contra el último ciclo BUENO DE SU RED, no contra el
  reloj.** Si una red se cae —o se cae este servidor— sus estaciones dejan de
  aparecer sin tener culpa de nada. Como el reloj de cada estación avanza solo
  cuando su propia red ha contestado, una red caída sencillamente congela a
  las suyas: da igual que esté fuera una hora o una semana. Y va por RED, no
  por proveedor, porque el bulk consulta cada red por separado y devuelve
  lista vacía tanto si falla como si no hay nadie publicando: sin esta
  separación, una sola red italiana con un mal día se llevaría por delante a
  sus 119 estaciones sanas.

- **Los identificadores se comparan en forma canónica.** El catálogo y el
  ranking escriben el mismo id de MeteoHub de dos maneras —``monte-carpegna``
  frente a ``monte carpegna``, cinco decimales frente a los del feed—, y una
  comparación literal daría por muda a una estación viva, ocultándola para
  siempre. Es el mismo desajuste que rompió 21 de los 30 enlaces del ranking
  italiano.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# Sin aparecer en su red durante este tiempo, la estación se oculta.
SILENCE_THRESHOLD_S = 24 * 3600

_Key = Tuple[str, str]  # (proveedor, id canónico)

_lock = threading.Lock()
# identidad → (cuándo se vio por última vez, red a la que pertenece)
_seen: Dict[_Key, Tuple[float, str]] = {}
# red → cuándo contestó por última vez con un censo válido
_sweeps: Dict[str, float] = {}


def scope_id(provider: Any, network: Any = "") -> str:
    """Clave de la red dentro del proveedor. Sin redes, el proveedor entero."""
    provider_token = str(provider or "").strip().upper()
    network_token = str(network or "").strip().lower()
    return f"{provider_token}|{network_token}"


def canonical_key(provider: Any, station_id: Any) -> Optional[_Key]:
    """Identidad comparable entre el catálogo y el bulk."""
    provider_token = str(provider or "").strip().upper()
    station_token = str(station_id or "").strip()
    if not provider_token or not station_token:
        return None
    if provider_token == "METEOHUB_IT":
        # Import perezoso: ``stations`` importa este módulo para filtrar.
        from server.services.stations import _meteohub_canonical_id

        station_token = _meteohub_canonical_id(station_token)
    return provider_token, station_token.lower()


def record_sweep(
    provider: str,
    station_ids: Iterable[Any],
    *,
    network: Any = "",
    now: Optional[float] = None,
) -> int:
    """Una red acaba de contestar: estas son sus estaciones con dato.

    Llamar SOLO cuando la consulta a esa red ha ido bien. Un fallo no se
    registra: así el reloj de sus estaciones se queda quieto en vez de correr
    contra ellas.
    """
    stamp = float(now if now is not None else time.time())
    provider_token = str(provider or "").strip().upper()
    if not provider_token:
        return 0
    scope = scope_id(provider_token, network)
    keys = [key for key in (canonical_key(provider_token, sid) for sid in station_ids) if key]
    with _lock:
        for key in keys:
            _seen[key] = (stamp, scope)
        _sweeps[scope] = stamp
    return len(keys)


def is_silent(provider: Any, station_id: Any) -> bool:
    """Se vio en su red y esa red lleva 24 h contestando sin ella."""
    key = canonical_key(provider, station_id)
    if key is None:
        return False
    with _lock:
        entry = _seen.get(key)
        if entry is None:
            return False  # nunca observada por el bulk: no se juzga
        seen_at, scope = entry
        last_sweep = _sweeps.get(scope)
    if last_sweep is None:
        return False
    return (last_sweep - seen_at) > SILENCE_THRESHOLD_S


def silent_identities() -> FrozenSet[_Key]:
    """Todas las identidades mudas, para filtrar listados de golpe."""
    with _lock:
        sweeps = dict(_sweeps)
        return frozenset(
            key
            for key, (seen_at, scope) in _seen.items()
            if (sweeps.get(scope, seen_at) - seen_at) > SILENCE_THRESHOLD_S
        )


def stats() -> Dict[str, int]:
    """Diagnóstico: cuántas estaciones se observan y cuántas redes responden."""
    with _lock:
        return {"observed": len(_seen), "networks": len(_sweeps)}


def clear() -> None:
    """Vacía el registro (tests)."""
    with _lock:
        _seen.clear()
        _sweeps.clear()


# ---------------------------------------------------------------------------
# Persistencia. El registro viaja dentro del snapshot del ``RankingStore``, que
# ya se guarda tras cada ciclo y se restaura al arrancar. Sin esto, un
# despliegue dejaba el registro en blanco y la ocultación tardaba otras 24 h en
# poder activarse: nunca ocultaba de más, pero un servicio que se redespliega a
# diario no llegaba a ocultar nunca.
# ---------------------------------------------------------------------------

def export_state() -> Dict[str, Any]:
    """Estado serializable a JSON."""
    with _lock:
        return {
            "seen": [
                [provider, station_id, seen_at, scope]
                for (provider, station_id), (seen_at, scope) in _seen.items()
            ],
            "sweeps": dict(_sweeps),
        }


def import_state(payload: Any) -> int:
    """Restaura un estado exportado. Un payload ilegible deja el registro vacío
    —se vuelve a llenar con el primer ciclo— en vez de reventar el arranque."""
    if not isinstance(payload, dict):
        return 0
    seen: Dict[_Key, Tuple[float, str]] = {}
    for row in payload.get("seen") or []:
        try:
            provider, station_id, seen_at, scope = row
            seen[(str(provider), str(station_id))] = (float(seen_at), str(scope))
        except (TypeError, ValueError):
            continue
    sweeps: Dict[str, float] = {}
    for scope, stamp in (payload.get("sweeps") or {}).items():
        try:
            sweeps[str(scope)] = float(stamp)
        except (TypeError, ValueError):
            continue
    with _lock:
        _seen.clear()
        _seen.update(seen)
        _sweeps.clear()
        _sweeps.update(sweeps)
    return len(seen)

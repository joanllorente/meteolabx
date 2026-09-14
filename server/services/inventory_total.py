"""Número total de estaciones del inventario, fijado en cada deploy.

El mapa dice «hay X estaciones disponibles en el inventario». Contarlas
recorre los tres SQLite —catálogo unificado, Windy y Netatmo— y el resultado
solo cambia de verdad cuando se publica un catálogo nuevo, es decir, con un
deploy. Así que se cuenta una vez al arrancar y se deja quieto hasta el
siguiente: un número que baila entre recargas porque unas cuantas estaciones
de Windy han dejado de publicar no aporta nada.

Un reinicio no es un deploy —Railway reinicia el contenedor tras un OOM—, y
volver a contar entonces daría otra cifra dentro del mismo despliegue. Por
eso, con Volume, el recuento se guarda junto al ``RAILWAY_DEPLOYMENT_ID`` y
se reutiliza mientras el identificador sea el mismo. Sin Railway (en local)
cada arranque del proceso cuenta como deploy.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from server.services import stations

logger = logging.getLogger(__name__)

_FILENAME = "inventory_total.json"
_lock = threading.Lock()
_state: Optional[Dict[str, Any]] = None


def _deployment_id() -> str:
    return os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip()


def _state_path() -> Optional[Path]:
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    return Path(volume) / _FILENAME if volume else None


def _read_saved(path: Path, deployment_id: str) -> Optional[Dict[str, Any]]:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("deployment_id") != deployment_id:
        return None
    total = saved.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return None
    return {
        "total": total,
        "computed_at": str(saved.get("computed_at") or ""),
        "deployment_id": deployment_id,
    }


def _save(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        # Sin guardar, el siguiente reinicio vuelve a contar: nada grave.
        logger.warning("inventory_total: no se pudo guardar %s: %s", path, exc)


def _count() -> int:
    return stations.inventory_station_count()


def current() -> Dict[str, Any]:
    """``{total, computed_at, deployment_id}`` del deploy en curso."""
    global _state
    with _lock:
        if _state is not None:
            return dict(_state)
        deployment_id = _deployment_id()
        path = _state_path()
        if path is not None and deployment_id:
            saved = _read_saved(path, deployment_id)
            if saved is not None:
                _state = saved
                return dict(_state)
        state = {
            "total": _count(),
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "deployment_id": deployment_id,
        }
        if path is not None and deployment_id:
            _save(path, state)
        _state = state
        logger.info("inventory_total: %s estaciones en el inventario", state["total"])
        return dict(_state)


def reset_for_tests() -> None:
    global _state
    with _lock:
        _state = None

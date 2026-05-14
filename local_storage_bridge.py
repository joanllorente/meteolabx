"""Componente custom para leer/escribir localStorage de forma estable."""
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import streamlit.components.v1 as components


_COMPONENT_PATH = Path(__file__).resolve().parent / "local_storage_bridge_frontend"
_local_storage_bridge = components.declare_component(
    "local_storage_bridge",
    path=str(_COMPONENT_PATH),
)


def sync_local_storage(
    *,
    keys: Iterable[str] = (),
    writes: Optional[Dict[str, Any]] = None,
    emit: bool = True,
    key: str = "local_storage_bridge",
) -> Optional[Dict[str, Any]]:
    """Sincroniza localStorage y devuelve un snapshot de las keys pedidas."""
    value = _local_storage_bridge(
        keys=list(keys or ()),
        writes=writes or {},
        emit=bool(emit),
        key=key,
        default=None,
    )
    return value if isinstance(value, dict) else None

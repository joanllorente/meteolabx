"""Componente custom para sincronizar la cámara real del mapa pydeck."""
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_PATH = Path(__file__).resolve().parent / "map_viewport_frontend"
_map_viewport = components.declare_component(
    "map_viewport",
    path=str(_COMPONENT_PATH),
)


def get_map_viewport(*, key: str = "map_viewport_sync") -> Optional[Dict[str, Any]]:
    """Devuelve la última cámara real del mapa leída en el navegador."""
    value = _map_viewport(key=key, default=None)
    return value if isinstance(value, dict) else None

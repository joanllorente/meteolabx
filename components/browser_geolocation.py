"""Componente custom para geolocalización del navegador."""
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_PATH = Path(__file__).resolve().parent / "browser_geolocation_frontend"
_browser_geolocation = components.declare_component(
    "browser_geolocation",
    path=str(_COMPONENT_PATH),
)


def get_browser_geolocation(
    request_id: int,
    *,
    timeout_ms: int = 12000,
    high_accuracy: bool = True,
) -> Optional[Dict[str, Any]]:
    """Solicita coordenadas al navegador y devuelve el último resultado disponible."""
    value = _browser_geolocation(
        request_id=int(request_id),
        timeout_ms=int(timeout_ms),
        high_accuracy=bool(high_accuracy),
        key=f"browser_geolocation_{int(request_id)}",
        default=None,
    )
    return value if isinstance(value, dict) else None

"""Componente custom para hidratar contexto del navegador sin recargas duras."""
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_PATH = Path(__file__).resolve().parent / "browser_context_frontend"
_browser_context = components.declare_component(
    "browser_context",
    path=str(_COMPONENT_PATH),
)


def get_browser_context(
    *,
    listen_changes: bool = True,
    listen_viewport_changes: bool = False,
    key: str = "browser_context_sync",
) -> Optional[Dict[str, Any]]:
    """Devuelve timezone, viewport y preferencia de color del navegador."""
    value = _browser_context(
        listen_changes=bool(listen_changes),
        listen_viewport_changes=bool(listen_viewport_changes),
        key=key,
        default=None,
    )
    return value if isinstance(value, dict) else None

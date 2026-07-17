"""Agrupacion client-side de estaciones para la vista de temperatura."""

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import streamlit.components.v1 as components


_COMPONENT_PATH = Path(__file__).resolve().parent / "temperature_clusters_frontend"
_temperature_clusters = components.declare_component(
    "temperature_clusters",
    path=str(_COMPONENT_PATH),
)


def render_temperature_clusters(
    points: Sequence[Dict[str, Any]],
    *,
    tiles: Sequence[Dict[str, Any]],
    dark: bool,
    mode: str = "temperature",
    tooltip_labels: Optional[Dict[str, str]] = None,
    key: str = "temperature_clusters",
) -> Optional[Dict[str, Any]]:
    """Monta el campo y las etiquetas sobre el ultimo mapa pydeck.

    El raster se inserta en MapLibre bajo la capa vectorial de agua del mapa
    base, de modo que el mar recorta el campo por la costa nativa en cada zoom
    y nombres, carreteras y límites quedan por encima. Si un estilo no expone
    una capa de agua identificable, se conserva el fallback bajo los símbolos.
    El agrupado, el pan y el zoom viven enteramente en el navegador. Python
    solo recibe un evento al seleccionar una estación individual.
    """
    value = _temperature_clusters(
        points=list(points),
        tiles=list(tiles),
        dark=bool(dark),
        mode=(
            str(mode).lower()
            if str(mode).lower() in {"temperature", "wind", "precipitation"}
            else "temperature"
        ),
        tooltip_labels=dict(tooltip_labels or {}),
        key=key,
        default=None,
    )
    return value if isinstance(value, dict) else None

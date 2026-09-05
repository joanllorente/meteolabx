"""
El visor instalado tiene que ser el que se acaba de construir.

`npm run build:forecast` deja el artefacto en ``static/forecast_app``, pero
quien lo sirve hoy es el servicio SvelteKit desde ``web/static/forecast``, y
esa copia la hace ``scripts/install_forecast_frontend.py``. Olvidarlo no rompe
nada: la web sigue funcionando y sirve el visor anterior, sin un solo error.
Así estuvo el visor en castellano mientras su código ya estaba traducido.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CONSTRUIDO = REPO / "static" / "forecast_app" / "forecast.html"
INSTALADO = REPO / "web" / "static" / "forecast" / "index.html"

RECETA = (
    "El visor instalado no es el construido. Ejecuta:\n"
    "  cd prototype-svelte && npm run build:forecast\n"
    "  python scripts/install_forecast_frontend.py"
)


def _bundle(html: Path) -> str:
    encontrado = re.search(r'assets/(forecast-[A-Za-z0-9_-]+\.js)', html.read_text(encoding="utf-8"))
    assert encontrado, f"{html} no referencia ningún bundle del visor"
    return encontrado.group(1)


@pytest.mark.skipif(not INSTALADO.is_file(), reason="el frontend SvelteKit no está en este árbol")
def test_the_served_viewer_is_the_one_just_built() -> None:
    assert _bundle(INSTALADO) == _bundle(CONSTRUIDO), RECETA


@pytest.mark.skipif(not INSTALADO.is_file(), reason="el frontend SvelteKit no está en este árbol")
def test_the_bundle_it_points_to_actually_exists() -> None:
    # Un `index.html` nuevo con los assets viejos borrados deja la pestaña en
    # blanco: el navegador pide un fichero que ya no está.
    fichero = INSTALADO.parent / "assets" / _bundle(INSTALADO)
    assert fichero.is_file(), f"Falta {fichero.relative_to(REPO)}. {RECETA}"

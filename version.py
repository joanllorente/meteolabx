"""
Versión de MeteoLabX, en un solo sitio.

Vivía en ``meteolabx.py`` como ``APP_VERSION``, y de ahí la copiaban a mano
``server/__init__.py`` y el exportador de textos. Tres copias que había que
acordarse de mover juntas: al publicar la 2.0.2 se quedaron dos al día y el
test que fijaba el número literal siguió esperando la 2.0.1.

El número vive ahora en el fichero ``VERSION`` de la raíz, texto plano, para
que puedan leerlo tanto Python como cualquier herramienta del build sin
importar un módulo de interfaz.
"""

from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def app_version() -> str:
    """Número de versión, sin salto de línea."""
    return VERSION_FILE.read_text(encoding="utf-8").strip()


APP_VERSION = app_version()

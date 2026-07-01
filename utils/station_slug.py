"""
Slugificación estable de nombres de estación para URLs compartibles.

Se usa en AMBOS lados del contrato:
- backend (``server/services/stations.find_by_slug``) para resolver
  ``provider + slug`` → ficha de estación.
- frontend (``meteolabx.py``) para construir la URL ``?e=<provider>~<slug>``
  a partir de la estación activa.

Mantener una única implementación garantiza el ida-y-vuelta: el slug que
escribe el frontend es exactamente el que el backend sabe resolver.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convierte un nombre legible en un slug ASCII apto para URL.

    ``"Barcelona - El Raval (Drassanes)"`` → ``"barcelona-el-raval-drassanes"``.
    Devuelve cadena vacía si no queda ningún carácter útil.
    """
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_ALNUM.sub("-", ascii_text).strip("-")

"""
Slug de URL de estación: una sola implementación para las dos orillas.

La URL indexable de una estación es ``{nombre}-{identificador}``, por ejemplo
``barcelona-drassanes-0201x``. Ese slug lo necesitan tres piezas distintas:

- ``scripts/build_seo_pages.py``, que escribe los índices y el sitemap;
- ``scripts/build_station_url_slugs.py``, que materializa la tabla de
  resolución en el catálogo SQLite;
- ``server/services/stations.py``, que traduce el slug de vuelta a la ficha
  cuando llega una petición a ``/{idioma}/observation/{slug}``.

Si las tres no calculan exactamente lo mismo, una URL ya indexada por Google
deja de resolver. De ahí que la lógica —incluido el desempate por hash— viva
aquí y no duplicada en cada consumidor.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from utils.station_slug import slugify

# Recortes heredados del generador SEO original. Cambiarlos reescribiría slugs
# ya indexados, así que son parte del contrato público, no un detalle interno.
NAME_SLUG_MAX_LENGTH = 88
IDENTITY_SLUG_MAX_LENGTH = 36


def candidate_url_slug(name: Any, station_id: Any, *, fallback: Any = "") -> str:
    """Slug sin desempatar: ``nombre-identificador``."""
    name_slug = slugify(name)[:NAME_SLUG_MAX_LENGTH] or "station"
    identity_slug = slugify(station_id)[:IDENTITY_SLUG_MAX_LENGTH] or str(fallback or "")
    return f"{name_slug}-{identity_slug}" if identity_slug else name_slug


def disambiguation_suffix(provider: Any, network_code: Any, station_id: Any) -> str:
    """Sufijo estable para las estaciones que comparten slug dentro de una red."""
    identity = f"{provider}|{network_code or ''}|{station_id}".encode()
    return hashlib.sha1(identity).hexdigest()[:8]


def _field(row: Mapping[str, Any] | Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


def url_slug_map(rows: Sequence[Any] | Iterable[Any]) -> dict[int, str]:
    """
    ``station_pk`` → slug único, resolviendo colisiones dentro de cada red.

    El desempate solo se aplica cuando dos estaciones **de la misma red**
    producen el mismo candidato, que es como se generaron los slugs que ya
    están indexados. Entre redes distintas no se toca nada: en el catálogo
    público las coincidencias entre redes no existen y añadir el hash aquí
    cambiaría URLs vivas.
    """
    owners: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        candidate = candidate_url_slug(
            _field(row, "name"),
            _field(row, "station_id"),
            fallback=_field(row, "station_pk"),
        )
        owners.setdefault((str(_field(row, "provider")), candidate), []).append(row)

    slugs: dict[int, str] = {}
    for (provider, candidate), matches in owners.items():
        if len(matches) == 1:
            slugs[int(_field(matches[0], "station_pk"))] = candidate
            continue
        for row in matches:
            suffix = disambiguation_suffix(
                provider,
                _field(row, "network_code"),
                _field(row, "station_id"),
            )
            slugs[int(_field(row, "station_pk"))] = f"{candidate}-{suffix}"
    return slugs

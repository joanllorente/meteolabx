#!/usr/bin/env python3
"""Materializa en el catálogo la tabla que resuelve slug de URL → estación.

``/{idioma}/observation/{slug}`` tiene que responder en milisegundos y el
catálogo no indexa por nombre, así que reconstruir el slug en cada petición
obligaría a escanear 230.000 filas. Esta tabla se calcula una vez —al
arrancar el servicio, después de descomprimir el catálogo— y deja la
resolución en una búsqueda por clave primaria.

Los slugs salen de ``utils.station_url``, el mismo módulo que usa el
generador SEO: la URL que publica el sitemap es exactamente la que el
backend sabe resolver.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.station_url import candidate_url_slug, url_slug_map

# Las mismas redes que publica el generador SEO. Fuera de ellas el slug no es
# único a nivel global (IEM y las redes de aficionados repiten nombres a
# millares), y son justo las que nunca se indexan.
INDEXABLE_PROVIDERS = (
    "AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "POEM", "METEOFRANCE",
    "FROST", "NWS", "METOFFICE", "METEOHUB_IT", "IPMA", "GEOSPHERE", "SMHI",
    "ECCC", "CLIMANTARTIDE",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_url_slugs (
    station_pk INTEGER PRIMARY KEY,
    url_slug   TEXT NOT NULL,
    indexable  INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_station_url_slugs_slug
    ON station_url_slugs(url_slug);
"""

# Idéntico al filtro de ``build_seo_pages.load_stations``: si los dos conjuntos
# se separan, el desempate por hash puede caer en estaciones distintas y
# cambiar slugs que ya están indexados.
_INDEXABLE_QUERY = """
    SELECT s.station_pk, s.provider, s.network_code, s.station_id, s.name
    FROM stations s
    LEFT JOIN station_visibility_overrides svo USING(station_pk)
    WHERE s.provider IN ({placeholders})
      AND COALESCE(svo.hidden, 0) = 0
      AND COALESCE(s.online, 1) = 1
      AND s.name IS NOT NULL AND TRIM(s.name) <> ''
      AND s.latitude IS NOT NULL AND s.longitude IS NOT NULL
    ORDER BY s.provider, s.name COLLATE NOCASE, s.station_id COLLATE NOCASE
"""

# El resto de estaciones de esas mismas redes: ocultas, sin coordenadas o
# marcadas fuera de servicio. No entran en el sitemap, pero si una URL ya
# indexada apunta a una estación que hoy está caída queremos servir su ficha
# —con noindex— en vez de un 404.
_RESIDUAL_QUERY = """
    SELECT s.station_pk, s.provider, s.network_code, s.station_id, s.name
    FROM stations s
    LEFT JOIN station_visibility_overrides svo USING(station_pk)
    WHERE s.provider IN ({placeholders})
      AND s.name IS NOT NULL AND TRIM(s.name) <> ''
      AND (
            COALESCE(svo.hidden, 0) <> 0
         OR COALESCE(s.online, 1) <> 1
         OR s.latitude IS NULL OR s.longitude IS NULL
      )
    ORDER BY s.provider, s.station_id COLLATE NOCASE
"""


def build_url_slugs(
    database: Path,
    providers: Sequence[str] = INDEXABLE_PROVIDERS,
) -> dict[str, int]:
    placeholders = ",".join("?" for _ in providers)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(_SCHEMA)
        indexable = connection.execute(
            _INDEXABLE_QUERY.format(placeholders=placeholders), tuple(providers)
        ).fetchall()
        slugs = url_slug_map(indexable)

        rows = [(pk, slug, 1) for pk, slug in slugs.items()]
        taken = set(slugs.values())
        residual = 0
        for row in connection.execute(
            _RESIDUAL_QUERY.format(placeholders=placeholders), tuple(providers)
        ):
            candidate = candidate_url_slug(
                row["name"], row["station_id"], fallback=row["station_pk"]
            )
            if candidate in taken:
                continue
            taken.add(candidate)
            rows.append((int(row["station_pk"]), candidate, 0))
            residual += 1

        connection.execute("DELETE FROM station_url_slugs")
        connection.executemany(
            "INSERT INTO station_url_slugs(station_pk, url_slug, indexable) VALUES (?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return {"indexable": len(slugs), "residual": residual, "total": len(rows)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/stations.sqlite"))
    args = parser.parse_args(argv)
    if not args.database.is_file():
        print(f"[build_station_url_slugs] No existe {args.database}", file=sys.stderr)
        return 1
    summary = build_url_slugs(args.database.resolve())
    print(
        "[build_station_url_slugs] "
        f"{summary['total']} slugs ({summary['indexable']} indexables, "
        f"{summary['residual']} solo resolubles)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

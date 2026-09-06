#!/usr/bin/env python3
"""Borra del sqlite de uso las visitas que ya se guardaron como rastreador.

Los endpoints de ``/v1/stats`` descartan a quien se declara bot antes de tocar
la base de datos, pero eso no toca lo que ya estaba escrito: las visitas de
Googlebot registradas hasta entonces siguen contando en los recuentos por
idioma y por estación. Esto las elimina.

Solo se limpia ``station_visits``, la única tabla que anotó de qué cliente
venía cada fila. Los errores, las secciones y las aperturas de ficha no lo
guardaron: ahí no hay forma de distinguir el ruido a posteriori.

    python3 scripts/purge_crawler_visits.py --dry-run
    python3 scripts/purge_crawler_visits.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services import usage_stats

CRAWLERS = "request_client != '' AND request_client != 'unidentified'"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default="",
        help="ruta del sqlite; por defecto la que usa el servidor (METEOLABX_USAGE_STATS_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="cuenta lo que se borraría sin tocar nada",
    )
    args = parser.parse_args()

    settings = None
    if args.database:
        from types import SimpleNamespace

        settings = SimpleNamespace(usage_stats_path=args.database)

    with usage_stats._connect(settings) as connection:
        connection.row_factory = sqlite3.Row
        reparto = connection.execute(
            f"SELECT request_client, COUNT(*) AS filas FROM station_visits"
            f" WHERE {CRAWLERS} GROUP BY request_client ORDER BY filas DESC"
        ).fetchall()
        total = sum(fila["filas"] for fila in reparto)
        quedan = connection.execute(
            f"SELECT COUNT(*) FROM station_visits WHERE NOT ({CRAWLERS})"
        ).fetchone()[0]

    for fila in reparto:
        print(f"  {fila['request_client']:<12} {fila['filas']:>8}")
    print(f"  {'total':<12} {total:>8}   (quedarían {quedan} visitas sin identificar)")

    if args.dry_run:
        print("\n--dry-run: no se ha borrado nada.")
        return 0
    if not total:
        return 0

    borradas = usage_stats.purge_crawler_visits(settings=settings)
    print(f"\nBorradas {borradas} visitas de rastreadores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

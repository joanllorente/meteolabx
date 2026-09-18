#!/usr/bin/env python3
"""Borra del sqlite de uso una ráfaga de visitas de agentes que no se declararon.

``purge_crawler_visits.py`` solo alcanza a quien dijo ser bot. Los agentes
con el user-agent de un navegador corriente quedan como «No identificado»,
igual que una persona, y el 16/09/2026 llenaron el panel de estaciones de
Météo-France: todas con navegador ``en-us,en``, escritorio y entrada directa,
cada visita con su apertura de ficha y su 429. Esto las localiza por ese
patrón dentro de una franja y, con ``--apply``, las borra junto con sus
errores y aperturas de ficha.

Sin ``--apply`` solo cuenta: revisa el reparto y las visitas de la misma
franja que quedarían antes de borrar nada.

    python3 scripts/purge_bot_burst.py --since 2026-09-16T00:00 --until 2026-09-17T00:00 \\
        --provider METEOFRANCE --browser-languages en-us,en --device desktop
    python3 scripts/purge_bot_burst.py ... --apply

Las horas se leen en hora peninsular (Europe/Madrid).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services import usage_stats

MADRID = ZoneInfo("Europe/Madrid")


def _epoch(value: str) -> int:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MADRID)
    return int(moment.timestamp())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="inicio de la franja, p. ej. 2026-09-16T00:00")
    parser.add_argument("--until", required=True, help="fin de la franja (excluido)")
    parser.add_argument("--provider", action="append", default=[], help="repetible; sin él, todos")
    parser.add_argument("--browser-languages", default="", help="idiomas del navegador tal cual, p. ej. en-us,en")
    parser.add_argument("--device", default="", choices=["", "mobile", "tablet", "desktop"])
    parser.add_argument("--pair-window", type=int, default=60,
                        help="segundos alrededor de cada visita para emparejar errores y aperturas")
    parser.add_argument("--database", default="",
                        help="ruta del sqlite; por defecto la del servidor (METEOLABX_USAGE_STATS_PATH)")
    parser.add_argument("--apply", action="store_true", help="borra de verdad; sin él solo cuenta")
    args = parser.parse_args()

    if not args.browser_languages and not args.provider:
        parser.error("indica al menos --browser-languages o --provider: sin patrón se llevaría a personas")

    settings = None
    if args.database:
        from types import SimpleNamespace

        settings = SimpleNamespace(usage_stats_path=args.database)

    result = usage_stats.purge_visit_burst(
        since=_epoch(args.since),
        until=_epoch(args.until),
        providers=args.provider,
        browser_languages=args.browser_languages,
        device=args.device,
        pair_window_s=args.pair_window,
        dry_run=not args.apply,
        settings=settings,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.apply:
        print(f"\nBorradas {result['visits']} visitas, {result['errors']} errores"
              f" y {result['seo_page_views']} aperturas de ficha.")
    else:
        print("\nSin --apply: no se ha borrado nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

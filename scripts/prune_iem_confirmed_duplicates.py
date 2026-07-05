#!/usr/bin/env python3
"""
Elimina del catálogo las estaciones IEM confirmadas como duplicados de un
proveedor oficial (AEMET, FROST, METEOFRANCE…). Nos quedamos con la oficial.

"Confirmada" = alias ``observation_confirmed``: el validador
(``validate_station_alias_observations.py``) comparó observaciones horarias
reales de ambas y coinciden. Es el nivel máximo de certeza del pipeline; los
``inventory_*`` (solo indicios de inventario) NO se borran.

Excepción NWS: los duplicados IEM de estaciones NWS se CONSERVAN — NWS no
tiene endpoint bulk y el ranking los necesita.

Reejecutable: tras cada ronda de validación que confirme pares nuevos, correr
de nuevo. Borra de: stations, station_aliases (+checks), visibility_overrides,
station_rtree, station_inventory_records y del inventario JSON. Además apunta
cada baja en ``data/iem_confirmed_duplicates_removed.json`` (kill-list
persistente: si el inventario IEM se re-descarga de cero, sirve para volver a
podarlas sin re-validar).

Uso::

    python3 scripts/prune_iem_confirmed_duplicates.py --dry-run
    python3 scripts/prune_iem_confirmed_duplicates.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SQLITE_PATH = ROOT_DIR / "data" / "stations.sqlite"
INVENTORY_PATH = ROOT_DIR / "data" / "data_estaciones_iem.json"
KILL_LIST_PATH = ROOT_DIR / "data" / "iem_confirmed_duplicates_removed.json"

_SELECT_DOOMED = """
SELECT DISTINCT s_iem.station_pk, s_iem.network_code, s_iem.station_id,
       s_iem.name, s_iem.source_record_pk,
       s_src.provider AS canonical_provider, s_src.station_id AS canonical_id
FROM station_aliases a
JOIN stations s_iem ON s_iem.station_pk = a.station_pk AND s_iem.provider = 'IEM'
JOIN stations s_src ON s_src.station_pk = a.canonical_station_pk
WHERE a.method = 'observation_confirmed'
  AND s_src.provider <> 'NWS'
  -- Defensa: si la misma IEM estuviera además confirmada contra NWS
  -- (imposible geográficamente hoy), se conserva.
  AND s_iem.station_pk NOT IN (
      SELECT a2.station_pk FROM station_aliases a2
      JOIN stations n ON n.station_pk = a2.canonical_station_pk
      WHERE a2.method = 'observation_confirmed' AND n.provider = 'NWS'
  )
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    doomed = connection.execute(_SELECT_DOOMED).fetchall()
    print(f"Duplicados IEM confirmados (no-NWS) a eliminar: {len(doomed)}")
    if not doomed:
        connection.close()
        return 0
    for row in doomed[:5]:
        print(
            f"  {row['network_code']}|{row['station_id']} ({row['name']}) "
            f"→ se queda {row['canonical_provider']}:{row['canonical_id']}"
        )
    if len(doomed) > 5:
        print(f"  … y {len(doomed) - 5} más")

    if args.dry_run:
        print("(dry-run: no se escribe nada)")
        connection.close()
        return 0

    # Una misma IEM puede estar confirmada contra DOS oficiales (fila por
    # canónico): dedupe por station_pk.
    pks = sorted({int(row["station_pk"]) for row in doomed})
    record_pks = sorted({int(row["source_record_pk"]) for row in doomed})
    keys = {(str(row["network_code"]), str(row["station_id"])) for row in doomed}

    connection.execute("BEGIN")
    connection.execute("CREATE TEMP TABLE doomed(pk INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO doomed(pk) VALUES (?)", [(pk,) for pk in pks])
    connection.execute("CREATE TEMP TABLE doomed_records(pk INTEGER PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO doomed_records(pk) VALUES (?)", [(pk,) for pk in record_pks]
    )
    counts = {}
    counts["alias_checks"] = connection.execute(
        "DELETE FROM station_alias_observation_checks WHERE alias_pk IN ("
        "  SELECT alias_pk FROM station_aliases"
        "  WHERE station_pk IN (SELECT pk FROM doomed)"
        "     OR canonical_station_pk IN (SELECT pk FROM doomed))"
    ).rowcount
    counts["aliases"] = connection.execute(
        "DELETE FROM station_aliases WHERE station_pk IN (SELECT pk FROM doomed)"
        " OR canonical_station_pk IN (SELECT pk FROM doomed)"
    ).rowcount
    counts["visibility"] = connection.execute(
        "DELETE FROM station_visibility_overrides WHERE station_pk IN (SELECT pk FROM doomed)"
        " OR preferred_station_pk IN (SELECT pk FROM doomed)"
    ).rowcount
    counts["sensors"] = connection.execute(
        "DELETE FROM station_sensors WHERE station_pk IN (SELECT pk FROM doomed)"
    ).rowcount
    counts["rtree"] = connection.execute(
        "DELETE FROM station_rtree WHERE station_pk IN (SELECT pk FROM doomed)"
    ).rowcount
    counts["stations"] = connection.execute(
        "DELETE FROM stations WHERE station_pk IN (SELECT pk FROM doomed)"
    ).rowcount
    counts["inventory_records"] = connection.execute(
        "DELETE FROM station_inventory_records WHERE record_pk IN (SELECT pk FROM doomed_records)"
    ).rowcount
    connection.commit()
    connection.execute("VACUUM")
    connection.execute("ANALYZE")
    connection.commit()
    connection.close()
    print("Borrado sqlite:", counts)

    # Kill-list persistente (acumulativa e idempotente por clave network|id).
    kill_list: dict = {"stations": {}}
    if KILL_LIST_PATH.is_file():
        try:
            kill_list = json.loads(KILL_LIST_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    entries = kill_list.setdefault("stations", {})
    now = datetime.now(timezone.utc).isoformat()
    for row in doomed:
        key = f"{row['network_code']}|{row['station_id']}"
        entries.setdefault(key, {
            "name": row["name"],
            "canonical": f"{row['canonical_provider']}:{row['canonical_id']}",
            "removed_at": now,
        })
    KILL_LIST_PATH.write_text(
        json.dumps(kill_list, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
    )
    print(f"Kill-list: {len(entries)} bajas acumuladas en {KILL_LIST_PATH.name}")

    # Inventario JSON: que un rebuild del catálogo no las reintroduzca.
    if INVENTORY_PATH.is_file():
        payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        stations = payload.get("stations")
        if isinstance(stations, list):
            before = len(stations)
            payload["stations"] = [
                row for row in stations
                if not (
                    isinstance(row, dict)
                    and (str(row.get("network") or ""), str(row.get("id") or "")) in keys
                )
            ]
            payload["station_count"] = len(payload["stations"])
            INVENTORY_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            print(f"Inventario JSON: {before - len(payload['stations'])} estaciones retiradas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

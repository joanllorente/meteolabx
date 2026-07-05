#!/usr/bin/env python3
"""
Elimina del catálogo las estaciones IEM de redes DCP sin sensores meteo.

Las DCP (Data Collection Platforms, mayormente hidrológicas: nivel de río,
caudal) no reportan ningún campo meteorológico en ``currents.json`` — por eso
``enrich_iem_sensor_inventory.py`` no les creó fila en ``station_sensors``.
No son conectables con datos útiles, no tienen histórico (los marcadores son
ASOS/AWOS/METAR) y engordan catálogo y memoria sin aportar nada al mapa.

Se preservan las DCP CON sensores meteo (p. ej. ``CA_DCP|DEVC1`` — Furnace
Creek, Death Valley) y además cualquier joya de ``_IEM_DCP_SCAN_KEEP``.

Ejecutar DESPUÉS de ``enrich_iem_sensor_inventory.py`` (necesita la tabla
``station_sensors`` poblada para saber qué DCP sí tienen sensores).

Borra de: stations, station_aliases (+checks), station_visibility_overrides,
station_rtree, station_inventory_records y del inventario JSON
(``data_estaciones_iem.json``) para que los rebuilds no las reintroduzcan.
Termina con VACUUM (reduce el fichero y por tanto el ``.gz`` del repo).

Uso::

    python3 scripts/prune_iem_hydro_dcp.py --dry-run
    python3 scripts/prune_iem_hydro_dcp.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SQLITE_PATH = ROOT_DIR / "data" / "stations.sqlite"
INVENTORY_PATH = ROOT_DIR / "data" / "data_estaciones_iem.json"

# Joyas a conservar aunque no tuvieran sensores (mismo criterio que el
# ranking: server/services/stations.py::_IEM_DCP_SCAN_KEEP).
KEEP = {"CA_DCP|DEVC1"}

_SELECT_DOOMED = r"""
SELECT s.station_pk, s.network_code, s.station_id, s.source_record_pk
FROM stations s
LEFT JOIN station_sensors ss USING(station_pk)
WHERE s.provider = 'IEM'
  AND s.network_code LIKE '%DCP%'
  AND ss.station_pk IS NULL
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(SQLITE_PATH)
    rows = connection.execute(_SELECT_DOOMED).fetchall()
    doomed = [
        (int(pk), str(net), str(sid), int(rec_pk))
        for pk, net, sid, rec_pk in rows
        if f"{net}|{sid}" not in KEEP
    ]
    print(f"DCP sin sensores meteo a eliminar: {len(doomed)}")
    if not doomed:
        connection.close()
        return 0

    pks = [pk for pk, _net, _sid, _rec in doomed]
    record_pks = [rec for _pk, _net, _sid, rec in doomed]
    keys = {(net, sid) for _pk, net, sid, _rec in doomed}

    if args.dry_run:
        sample = sorted(keys)[:5]
        print(f"(dry-run) ejemplo: {sample}")
        connection.close()
        return 0

    connection.execute("BEGIN")
    # Tabla temporal para no pelear con límites de placeholders (27k ids).
    connection.execute("CREATE TEMP TABLE doomed(pk INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO doomed(pk) VALUES (?)", [(pk,) for pk in pks])
    connection.execute("CREATE TEMP TABLE doomed_records(pk INTEGER PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO doomed_records(pk) VALUES (?)", [(rec,) for rec in record_pks]
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

    # Inventario JSON: sin esto, el próximo build_stations_sqlite.py las
    # reintroduciría desde data_estaciones_iem.json.
    if INVENTORY_PATH.is_file():
        with INVENTORY_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
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
            removed = before - len(payload["stations"])
            payload["station_count"] = len(payload["stations"])
            INVENTORY_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            print(f"Inventario JSON: {removed} estaciones retiradas")
    else:
        print(f"⚠ {INVENTORY_PATH} no existe; solo se ha limpiado el sqlite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

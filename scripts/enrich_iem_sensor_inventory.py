#!/usr/bin/env python3
"""
Enriquece el catálogo con la disponibilidad de sensores de las estaciones IEM.

IEM no publica un catálogo de sensores por estación, así que (igual que NWS)
se infiere de los campos no nulos de ``currents.json`` por red: si una
estación reporta ``tmpf`` tiene termómetro, si reporta ``srad`` tiene
piranómetro, etc. Con esto el filtro de sensores del mapa aplica también a
IEM (el único proveedor del catálogo sin sensores).

Ámbito: estaciones ``online=1`` de redes NO manuales. Quedan fuera a
propósito:
  - las manuales (``*_COOP`` y ``*COCORAHS*``): observadores que publican
    lecturas a mano una vez al día — el concepto "sensores en vivo" no les
    aplica y currents no devuelve nada para ellas;
  - las histórico-solo (``online=0``): no aparecen en currents y el filtro
    del mapa ya las trata aparte.

Una estación presente en currents pero con TODOS los campos meteo a null se
deja como "desconocida" (sin fila en ``station_sensors``): mejor sin dato que
un all-False de una estación que simplemente llevaba un rato sin reportar.

Escribe en dos sitios:
  1. ``data/stations.sqlite`` → tabla ``station_sensors`` (efecto inmediato;
     recuerda regenerar ``data/stations.sqlite.gz``, que es lo que viaja en
     el repo).
  2. ``data/data_estaciones_iem.json`` → campo ``sensors`` por estación, para
     que ``build_stations_sqlite.py`` los conserve en futuros rebuilds.

Uso::

    python3 scripts/enrich_iem_sensor_inventory.py            # todo
    python3 scripts/enrich_iem_sensor_inventory.py --max-networks 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CURRENTS_ENDPOINT = "https://mesonet.agron.iastate.edu/api/1/currents.json"
SQLITE_PATH = ROOT_DIR / "data" / "stations.sqlite"
INVENTORY_PATH = ROOT_DIR / "data" / "data_estaciones_iem.json"
TIMEOUT_SECONDS = 60
RETRIES = 2

# Campo(s) de currents.json cuya presencia (no null) implica cada sensor.
# ``gust``/``drct`` solo aparecen cuando hay racha/viento, por eso cada sensor
# mira varios campos alternativos (p. ej. los máximos diarios).
SENSOR_FIELDS: Dict[str, Tuple[str, ...]] = {
    "thermometer": ("tmpf", "max_tmpf", "min_tmpf"),
    "hygrometer": ("relh", "dwpf"),
    "barometer": ("alti", "mslp", "pres"),
    "anemometer": ("sknt", "gust", "max_sknt", "max_gust"),
    "wind_vane": ("drct",),
    "rain_gauge": ("pday", "phour", "ob_pday", "ob_pmonth"),
    "pyranometer": ("srad",),
    # IEM no expone UV en currents: siempre False (igual que NWS).
    "uv": (),
}
SENSOR_KEYS = tuple(SENSOR_FIELDS)


def _sensors_from_row(row: Dict[str, Any]) -> Optional[Dict[str, bool]]:
    """Sensores inferidos de una fila de currents; None si no reporta nada."""
    sensors = {
        key: any(row.get(field) is not None for field in fields)
        for key, fields in SENSOR_FIELDS.items()
    }
    if not any(sensors.values()):
        return None
    return sensors


def _fetch_network(session: requests.Session, network: str) -> Tuple[str, Dict[str, Dict[str, bool]], str]:
    """Devuelve (red, {station_id: sensors}, error)."""
    last_error = ""
    for attempt in range(RETRIES + 1):
        try:
            response = session.get(
                CURRENTS_ENDPOINT, params={"network": network}, timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            rows = response.json().get("data") or []
        except (requests.RequestException, ValueError) as exc:
            last_error = f"{type(exc).__name__}"
            if attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))
            continue
        out: Dict[str, Dict[str, bool]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            station = str(row.get("station") or "").strip()
            if not station:
                continue
            sensors = _sensors_from_row(row)
            if sensors is not None:
                out[station] = sensors
        return network, out, ""
    return network, {}, last_error


def _target_stations(connection: sqlite3.Connection) -> Dict[Tuple[str, str], int]:
    """(network, station_id) → station_pk de las IEM online no-COOP."""
    rows = connection.execute(
        r"SELECT station_pk, network_code, station_id FROM stations "
        r"WHERE provider = 'IEM' AND online = 1 "
        r"  AND network_code NOT LIKE '%\_COOP' ESCAPE '\' "
        r"  AND network_code NOT LIKE '%COCORAHS%'"
    ).fetchall()
    return {(str(net), str(sid)): int(pk) for pk, net, sid in rows}


def _write_sqlite(
    connection: sqlite3.Connection,
    resolved: Dict[Tuple[str, str], Dict[str, bool]],
    targets: Dict[Tuple[str, str], int],
) -> int:
    written = 0
    for key, sensors in resolved.items():
        station_pk = targets.get(key)
        if station_pk is None:
            continue
        connection.execute(
            "INSERT OR REPLACE INTO station_sensors("
            "  station_pk, thermometer, hygrometer, barometer, anemometer,"
            "  wind_vane, rain_gauge, pyranometer, uv"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (station_pk, *(int(sensors[k]) for k in SENSOR_KEYS)),
        )
        written += 1
    connection.commit()
    return written


def _write_inventory(resolved: Dict[Tuple[str, str], Dict[str, bool]]) -> int:
    """Añade ``sensors`` a las estaciones del inventario JSON (para rebuilds)."""
    if not INVENTORY_PATH.is_file():
        print(f"⚠ Inventario {INVENTORY_PATH} no encontrado; solo se actualiza el sqlite")
        return 0
    with INVENTORY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    stations = payload.get("stations") if isinstance(payload, dict) else None
    if not isinstance(stations, list):
        print("⚠ Inventario IEM sin lista 'stations'; se omite")
        return 0
    updated = 0
    for row in stations:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("network") or ""), str(row.get("id") or ""))
        sensors = resolved.get(key)
        if sensors is not None:
            row["sensors"] = sensors
            updated += 1
    INVENTORY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-networks", type=int, default=0, help="limitar (pruebas)")
    parser.add_argument("--dry-run", action="store_true", help="no escribe nada")
    args = parser.parse_args()

    connection = sqlite3.connect(SQLITE_PATH)
    targets = _target_stations(connection)
    networks = sorted({net for net, _sid in targets})
    if args.max_networks:
        networks = networks[: args.max_networks]
    print(f"Estaciones objetivo: {len(targets)} en {len(networks)} redes")

    session = requests.Session()
    session.headers["User-Agent"] = "MeteoLabX/1.0 (+https://meteolabx.com)"
    resolved: Dict[Tuple[str, str], Dict[str, bool]] = {}
    failed: List[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_fetch_network, session, net): net for net in networks}
        for future in as_completed(futures):
            network, sensors_by_station, error = future.result()
            done += 1
            if error:
                failed.append(f"{network} ({error})")
            for station, sensors in sensors_by_station.items():
                resolved[(network, station)] = sensors
            if done % 50 == 0 or done == len(networks):
                print(f"  {done}/{len(networks)} redes · {len(resolved)} estaciones con sensores")

    matched = sum(1 for key in resolved if key in targets)
    print(f"Resueltas: {len(resolved)} (en catálogo objetivo: {matched})")
    if failed:
        print(f"Redes con fallo ({len(failed)}): {', '.join(sorted(failed)[:10])}…")

    if args.dry_run:
        print("(dry-run: no se escribe nada)")
        connection.close()
        return 0

    written = _write_sqlite(connection, resolved, targets)
    connection.close()
    updated = _write_inventory(resolved)
    print(f"✓ station_sensors: {written} filas · inventario JSON: {updated} estaciones")
    print("Recuerda regenerar data/stations.sqlite.gz (gzip -9kf data/stations.sqlite → .gz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

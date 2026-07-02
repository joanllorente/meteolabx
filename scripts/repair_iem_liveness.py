#!/usr/bin/env python3
"""Marca offline las estaciones IEM de redes ASOS/AWOS/METAR sin observación actual.

El metadata de IEM trae ``online`` incorrecto para algunas estaciones que
llevan años sin reportar (p. ej. bases militares cerradas: AF__ASOS|QQS dice
``online=1`` y ``time_domain (2009-Now)`` pero no emite desde ~2014). El
filtro "ocultar estaciones históricas" del mapa y la exclusión del ranking
dependen de ese flag, así que esas estaciones muertas aparecían como
conectables y al conectar solo se obtenía el aviso de "archivo histórico".

Este script cruza cada red ASOS/AWOS/METAR del catálogo con el endpoint
``/api/1/currents.json`` de IEM (observación más reciente por estación):

  - estación ausente de currents            → online=0
  - último dato más viejo que --stale-days  → online=0
  - red sin NINGUNA entrada en currents     → todas sus online pasan a 0

Solo se procesan redes ASOS/AWOS/METAR: para otros tipos currents no es
señal fiable de vida (p. ej. TX_COCORAHS devuelve 0 entradas teniendo miles
de observadores activos), y además son las redes donde vive el problema
(has_historical=1, aeropuertos y bases cerradas).

Ejecutar DESPUÉS de build_stations_sqlite.py: un rebuild del catálogo
restaura los flags originales de IEM. Actualiza ``data/stations.sqlite`` y,
si existe, ``data/iem_stations.sqlite``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STATIONS_DB = ROOT / "data" / "stations.sqlite"
IEM_DB = ROOT / "data" / "iem_stations.sqlite"
CURRENTS_URL = "https://mesonet.agron.iastate.edu/api/1/currents.json"
USER_AGENT = "MeteoLabX-IEM-Liveness/1.0 (station liveness check)"
ASOS_MARKERS = ("ASOS", "AWOS", "METAR")


def _is_asos_like(network_code: str) -> bool:
    upper = network_code.upper()
    return any(marker in upper for marker in ASOS_MARKERS)


def _fetch_network_currents(network: str, retries: int = 3) -> dict[str, str]:
    """Devuelve {station_id: utc_valid} para la red, según currents de IEM."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                CURRENTS_URL,
                params={"network": network},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=(10, 120),
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            return {
                str(row.get("station") or "").strip(): str(row.get("utc_valid") or "")
                for row in rows
                if isinstance(row, dict) and row.get("station")
            }
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"currents falló para {network}: {last_error}")


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stale-days", type=int, default=30,
                        help="Días sin reportar para considerar muerta una estación (default: 30)")
    parser.add_argument("--workers", type=int, default=4, help="Peticiones concurrentes a IEM")
    parser.add_argument("--dry-run", action="store_true", help="No escribe; solo informa")
    args = parser.parse_args()

    if not STATIONS_DB.is_file():
        print(f"No existe {STATIONS_DB}", file=sys.stderr)
        return 1

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.stale_days)
    conn = sqlite3.connect(STATIONS_DB)
    rows = conn.execute(
        "SELECT network_code, station_id FROM stations WHERE provider='IEM' AND online=1"
    ).fetchall()
    online_by_network: dict[str, set[str]] = {}
    for network, station in rows:
        if _is_asos_like(str(network or "")):
            online_by_network.setdefault(str(network), set()).add(str(station))

    print(f"Redes ASOS/AWOS/METAR con estaciones online: {len(online_by_network)}")

    dead: list[tuple[str, str]] = []  # (network, station_id)
    failed_networks: list[str] = []

    def _check(network: str) -> tuple[str, list[str]]:
        currents = _fetch_network_currents(network)
        dead_here = []
        for station in sorted(online_by_network[network]):
            last_txt = currents.get(station)
            if last_txt is None:
                dead_here.append(station)
                continue
            last = _parse_utc(last_txt)
            if last is not None and last < cutoff:
                dead_here.append(station)
        return network, dead_here

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_check, network): network for network in sorted(online_by_network)}
        for future in concurrent.futures.as_completed(futures):
            network = futures[future]
            try:
                network, dead_here = future.result()
            except Exception as exc:  # noqa: BLE001 — red caída ≠ estaciones muertas
                failed_networks.append(network)
                print(f"  AVISO: {network} no comprobada ({exc})", file=sys.stderr)
                continue
            if dead_here:
                print(f"  {network}: {len(dead_here)}/{len(online_by_network[network])} sin observación reciente")
                dead.extend((network, station) for station in dead_here)

    print(f"Total a marcar offline: {len(dead)} estaciones"
          f" ({len(failed_networks)} redes no comprobadas)")
    if args.dry_run or not dead:
        return 0

    with conn:
        conn.executemany(
            "UPDATE stations SET online=0 WHERE provider='IEM' AND network_code=? AND station_id=?",
            dead,
        )
    conn.close()
    print(f"Actualizado {STATIONS_DB}")

    if IEM_DB.is_file():
        iem_conn = sqlite3.connect(IEM_DB)
        with iem_conn:
            iem_conn.executemany(
                "UPDATE iem_stations SET online=0 WHERE network_code=? AND station_id=?",
                dead,
            )
        iem_conn.close()
        print(f"Actualizado {IEM_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

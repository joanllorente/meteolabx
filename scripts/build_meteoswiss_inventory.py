#!/usr/bin/env python3
"""
Construye el inventario de estaciones de MeteoSwiss (Suiza y Liechtenstein)
desde sus datos abiertos (``data.geo.admin.ch``, sin clave, CC BY 4.0).

Fuentes, una colección por red:
  - ``ogd-smn``: SwissMetNet, estaciones meteorológicas automáticas completas.
  - ``ogd-smn-precip``: pluviómetros automáticos (lluvia de 10 min).
  - ``ogd-nime``: pluviómetros manuales (lluvia de 6 a 6 UTC, nieve nueva y
    espesor), leídos cada mañana y publicados al día siguiente.

  De cada una: ``*_meta_stations.csv`` (nombre, cantón, WIGOS, altitud,
  coordenadas WGS84, inicio de datos) y ``*_meta_datainventory.csv`` (qué
  parámetro mide cada estación y desde cuándo), de donde salen los sensores y
  el inicio del histórico diario.

Fuera:
  - ``ogd-smn-tower``: son las mismas cuatro estaciones de SwissMetNet con
    sensores en torre; ya están en ``ogd-smn``.
  - Manuales que comparten código y sitio con una automática (82): el
    pluviómetro manual está junto a la estación automática, que ya tiene su
    lluvia.
  - ``ogd-tot`` (totalizadores): solo dan un valor al año.

Clasificación:
  - Automáticas online si la última fila de su ``t_now`` (10 min) tiene
    menos de 3 h. El bulk de valores actuales (``VQHA80``/``VQHA98``) no
    sirve para esto: es una foto de un instante y deja fuera a estaciones que
    sí publican.
  - Manuales activas si han publicado lluvia en el último mes y medio
    (``realtime: False``: no hay dato del momento).
  - Los datos abiertos solo incluyen estaciones en servicio: no hay
    históricas cerradas.

Uso:
  python3 scripts/build_meteoswiss_inventory.py --output data/data_estaciones_meteoswiss.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METEOSWISS_STATIONS_PATH

BASE_URL = "https://data.geo.admin.ch"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
COLLECTIONS = ("ogd-smn", "ogd-smn-precip", "ogd-nime")
MANUAL_ACTIVE_DAYS = 45
ONLINE_MAX_AGE = timedelta(hours=3)

PARAMETER_SENSORS = {
    "tre200s0": "thermometer",
    "ure200s0": "hygrometer",
    "prestas0": "barometer",
    "pp0qffs0": "barometer",
    "fkl010z0": "anemometer",
    "dkl010z0": "wind_vane",
    "rre150z0": "rain_gauge",
    "rre150d0": "rain_gauge",
    "gre000z0": "pyranometer",
}
EXTRA_PARAMETERS = {
    "htoauts0": "snow_depth",
    "hto000d0": "snow_depth",
    "hns000d0": "fresh_snow",
    "sre000z0": "sunshine_duration",
    "tso005s0": "soil_temperature",
}
# Parámetros diarios cuyo inicio marca el del histórico.
DAILY_PARAMETERS = ("tre200d0", "tre200dx", "rka150d0", "rre150d0")
CANTON_COUNTRY = {"FL": ("LI", "Liechtenstein")}


def _get_text(url: str, *, tail_bytes: Optional[int] = None) -> str:
    headers = {"User-Agent": USER_AGENT}
    if tail_bytes:
        headers["Range"] = f"bytes=-{tail_bytes}"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        return response.read().decode("cp1252", errors="replace")


def _read_csv(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def _day(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def _empty_sensors() -> Dict[str, bool]:
    return {
        "thermometer": False, "hygrometer": False, "barometer": False,
        "anemometer": False, "wind_vane": False, "rain_gauge": False,
        "pyranometer": False, "uv": False,
    }


def last_reading(collection: str, code: str) -> Optional[datetime]:
    """Marca (UTC) de la última fila con algún valor del ``t_now``."""
    lower = code.lower()
    url = f"{BASE_URL}/ch.meteoschweiz.{collection}/{lower}/{collection}_{lower}_t_now.csv"
    try:
        lines = _get_text(url, tail_bytes=3000).splitlines()
    except Exception:
        return None
    for line in reversed(lines[1:]):
        parts = line.split(";")
        if len(parts) > 2 and parts[0].upper() == code and any(v.strip() for v in parts[2:]):
            try:
                return datetime.strptime(parts[1].strip(), "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def manual_last_rain(code: str) -> Optional[date]:
    """Último día con lluvia publicada (cola del ``d_recent``)."""
    lower = code.lower()
    url = f"{BASE_URL}/ch.meteoschweiz.ogd-nime/{lower}/ogd-nime_{lower}_d_recent.csv"
    try:
        lines = _get_text(url, tail_bytes=4000).splitlines()
    except Exception:
        return None
    for line in reversed(lines[1:]):
        parts = line.split(";")
        if len(parts) > 2 and parts[0].upper() == code and parts[2].strip():
            return _day(parts[1])
    return None


def build_inventory() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    automatic_codes = set()
    manual_candidates = []
    for collection in COLLECTIONS:
        base = f"{BASE_URL}/ch.meteoschweiz.{collection}/{collection}"
        stations = _read_csv(_get_text(f"{base}_meta_stations.csv"))
        inventory: Dict[str, List[Dict[str, str]]] = {}
        for item in _read_csv(_get_text(f"{base}_meta_datainventory.csv")):
            inventory.setdefault(item["station_abbr"].strip().upper(), []).append(item)

        for station in stations:
            code = station["station_abbr"].strip().upper()
            manual = collection == "ogd-nime"
            if manual and code in automatic_codes:
                continue
            params = [item for item in inventory.get(code, []) if not item.get("data_till")]
            sensors = _empty_sensors()
            extras = set()
            for item in params:
                name = item["parameter_shortname"]
                if name in PARAMETER_SENSORS:
                    sensors[PARAMETER_SENSORS[name]] = True
                if name in EXTRA_PARAMETERS:
                    extras.add(EXTRA_PARAMETERS[name])
            daily_starts = [
                _day(item["data_since"]) for item in inventory.get(code, [])
                if item["parameter_shortname"] in DAILY_PARAMETERS and _day(item["data_since"])
            ]
            start = min(daily_starts) if daily_starts else _day(station.get("station_data_since", ""))
            try:
                lat = float(station["station_coordinates_wgs84_lat"])
                lon = float(station["station_coordinates_wgs84_lon"])
            except (TypeError, ValueError):
                continue
            try:
                elev: Optional[float] = float(station["station_height_masl"])
            except (TypeError, ValueError):
                elev = None
            canton = station["station_canton"].strip().upper()
            country_code, country_name = CANTON_COUNTRY.get(canton, ("CH", "Suiza"))
            row = {
                "id": code,
                "source_id": code,
                "name": station["station_name"].strip(),
                "lat": lat,
                "lon": lon,
                "elev": elev,
                "altitude": elev,
                "tz": "Europe/Vaduz" if country_code == "LI" else "Europe/Zurich",
                "country": country_name,
                "country_code": country_code,
                "region": canton,
                "owner": station.get("station_dataowner", "").strip(),
                "wigos_id": station.get("station_wigos_id", "").strip(),
                "wmo_id": station["station_wigos_id"].rsplit("-", 1)[-1]
                if station.get("station_wigos_id", "").startswith("0-20000-0-") else "",
                "collection": collection,
                "station_type": station.get("station_type_en", "").split(" - ")[0].strip(),
                "network": {"ogd-smn": "SWISSMETNET", "ogd-smn-precip": "SMN-PRECIP"}.get(collection, "MANUAL"),
                "manual": manual,
                "realtime": not manual,
                "active_now": False,
                "status": "active",
                "has_historical": True,
                "archive_start": start.isoformat() if start else None,
                "archive_end": None,
                "extra_sensors": sorted(extras),
                "provider": "METEOSWISS",
                "source": f"{BASE_URL}/ch.meteoschweiz.{collection}",
                "sensors": sensors,
            }
            if manual:
                manual_candidates.append(row)
            else:
                automatic_codes.add(code)
            rows.append(row)

    limit = datetime.now(timezone.utc).date() - timedelta(days=MANUAL_ACTIVE_DAYS)
    automatic = [row for row in rows if not row["manual"]]
    fresh_from = datetime.now(timezone.utc) - ONLINE_MAX_AGE
    with ThreadPoolExecutor(max_workers=8) as pool:
        for row, last in zip(manual_candidates, pool.map(manual_last_rain, [r["id"] for r in manual_candidates])):
            row["active_now"] = bool(last and last >= limit)
        stamps = pool.map(lambda r: last_reading(r["collection"], r["id"]), automatic)
        for row, stamp in zip(automatic, stamps):
            row["active_now"] = bool(stamp and stamp >= fresh_from)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=METEOSWISS_STATIONS_PATH)
    args = parser.parse_args()

    rows = build_inventory()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "METEOSWISS",
        "source": "https://opendatadocs.meteoswiss.ch",
        "license": "CC BY 4.0 (MeteoSwiss)",
        "stations": rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    from collections import Counter

    print(f"Guardadas {len(rows)} estaciones MeteoSwiss en {args.output}")
    print(f"  por país: {dict(Counter(r['country_code'] for r in rows))}")
    print(f"  por red: {dict(Counter(r['collection'] for r in rows))}")
    print(f"  automáticas online:  {sum(1 for r in rows if not r['manual'] and r['active_now'])}")
    print(f"  automáticas offline: {sum(1 for r in rows if not r['manual'] and not r['active_now'])}")
    print(f"  manuales activas:    {sum(1 for r in rows if r['manual'] and r['active_now'])}")
    print(f"  manuales sin datos recientes: {sum(1 for r in rows if r['manual'] and not r['active_now'])}")
    for sensor in _empty_sensors():
        print(f"  {sensor:12} {sum(1 for r in rows if r['sensors'].get(sensor)):5}")


if __name__ == "__main__":
    main()

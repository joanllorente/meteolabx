#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Austria desde GeoSphere Austria
(ex-ZAMG, dataset.api.hub.geosphere.at, open-data sin API key).

Cruza tres peticiones:
  - .../station/current/tawes-v1-10min/metadata   catálogo en tiempo real
    (id, nombre, estado federado, coordenadas, altitud, is_active)
  - .../station/current/tawes-v1-10min?parameters=…&station_ids=…
    últimos valores por parámetro de TODA la red en una llamada; un sensor
    se considera presente si su parámetro trae algún valor no nulo
  - .../station/historical/klima-v2-1d/metadata   archivo climatológico
    (series diarias desde 1775)

El archivo klima tiene VARIOS registros por estación física (uno por época
o instrumentación); GeoSphere los agrupa con ``group_id`` bajo un registro
``COMBINED``. Aquí cada estación física es UNA entrada:
  - Las TAWES ganan ``klima_station_id`` (serie canónica) y
    ``klima_series`` (todas las series del sitio) → histórico.
  - Los sitios klima SIN estación TAWES entran como estaciones propias
    (``network: "KLIMA"``, ``manual: true``): convencionales con dato
    diario, sin observación 10-minutal.

Uso:
  python3 scripts/build_geosphere_inventory.py \
      --output data/data_estaciones_geosphere.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import GEOSPHERE_STATIONS_PATH

BASE_URL = "https://dataset.api.hub.geosphere.at/v1"
DATASET = "station/current/tawes-v1-10min"
METADATA_URL = f"{BASE_URL}/{DATASET}/metadata"
CURRENT_URL = f"{BASE_URL}/{DATASET}"
KLIMA_METADATA_URL = f"{BASE_URL}/station/historical/klima-v2-1d/metadata"

# Distancia máxima para considerar que una TAWES y un sitio klima son la
# misma estación física (los colocalizados suelen coincidir al metro).
MATCH_MAX_KM = 2.0

# Parámetro TAWES → sensor del catálogo. GLOW se contrasta además con el
# flag has_global_radiation del propio catálogo.
PARAM_SENSORS = {
    "TL": "thermometer",
    "RF": "hygrometer",
    "P": "barometer",
    "FF": "anemometer",
    "DD": "wind_vane",
    "RR": "rain_gauge",
    "GLOW": "pyranometer",
}


def _fetch_json(url: str, *, timeout: int = 120) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _empty_sensors() -> Dict[str, bool]:
    return {
        "thermometer": False,
        "hygrometer": False,
        "barometer": False,
        "anemometer": False,
        "wind_vane": False,
        "rain_gauge": False,
        "pyranometer": False,
        "uv": False,
    }


def _fetch_current_sensors(
    station_ids: List[str], *, timeout: int = 120
) -> tuple[Dict[str, Dict[str, bool]], set[str]]:
    """→ (sensores por estación, ids con algún dato reciente)."""
    params = ",".join(PARAM_SENSORS)
    url = f"{CURRENT_URL}?parameters={params}&station_ids={','.join(station_ids)}"
    payload = _fetch_json(url, timeout=timeout)
    sensors_by_station: Dict[str, Dict[str, bool]] = {}
    online_ids: set[str] = set()
    for feature in payload.get("features", []) if isinstance(payload, dict) else []:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        station_id = str(properties.get("station") or "").strip()
        if not station_id:
            continue
        sensors = sensors_by_station.setdefault(station_id, _empty_sensors())
        parameters = properties.get("parameters")
        for name, block in (parameters or {}).items() if isinstance(parameters, dict) else []:
            sensor = PARAM_SENSORS.get(str(name))
            if sensor is None or not isinstance(block, dict):
                continue
            values = block.get("data")
            if isinstance(values, list) and any(v is not None for v in values):
                sensors[sensor] = True
                online_ids.add(station_id)
    return sensors_by_station, online_ids


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    rlat1, rlon1, rlat2, rlon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((rlat2 - rlat1) / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin((rlon2 - rlon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def _series_entry(record: Dict[str, Any]) -> Dict[str, Any]:
    valid_to = str(record.get("valid_to") or "")[:10]
    return {
        "id": str(record.get("id")),
        "type": record.get("type"),
        "from": str(record.get("valid_from") or "")[:10] or None,
        # GeoSphere marca las series abiertas con valid_to en 2100.
        "to": None if valid_to >= "2099-01-01" else (valid_to or None),
    }


def _klima_sites(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Registros klima → sitios físicos (COMBINED + agrupados, o sueltos)."""
    by_id = {int(r["id"]): r for r in records if isinstance(r.get("id"), (int, str))}
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    standalone: List[Dict[str, Any]] = []
    for record in records:
        group_id = record.get("group_id")
        if group_id is not None and int(group_id) in by_id:
            grouped.setdefault(int(group_id), []).append(record)
        elif record.get("type") != "COMBINED":
            standalone.append(record)

    sites: List[Dict[str, Any]] = []
    for record in records:
        if record.get("type") == "COMBINED":
            members = sorted(
                grouped.get(int(record["id"]), []),
                key=lambda r: str(r.get("valid_from") or ""),
            )
            sites.append({"canonical": record, "series": [record, *members]})
    for record in standalone:
        sites.append({"canonical": record, "series": [record]})
    return sites


def build_inventory(*, timeout: int = 120) -> List[Dict[str, Any]]:
    metadata = _fetch_json(METADATA_URL, timeout=timeout)
    catalog = metadata.get("stations", []) if isinstance(metadata, dict) else []
    station_ids = [str(s.get("id") or "").strip() for s in catalog if s.get("id")]
    sensors_by_station, online_ids = _fetch_current_sensors(station_ids, timeout=timeout)

    klima_meta = _fetch_json(KLIMA_METADATA_URL, timeout=timeout)
    klima_records = klima_meta.get("stations", []) if isinstance(klima_meta, dict) else []
    sites = _klima_sites([r for r in klima_records if isinstance(r, dict)])

    def _base_row(station_id: str, source: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": station_id,
            "source_id": station_id,
            "name": str(source.get("name") or station_id).strip(),
            "lat": source.get("lat"),
            "lon": source.get("lon"),
            "elev": source.get("altitude"),
            "altitude": source.get("altitude"),
            "tz": "Europe/Vienna",
            "country": "Austria",
            "country_code": "AT",
            "region": str(source.get("state") or "").strip(),
            "valid_from": source.get("valid_from"),
            "valid_to": source.get("valid_to"),
            "has_sunshine": bool(source.get("has_sunshine")),
            "has_global_radiation": bool(source.get("has_global_radiation")),
            "provider": "GEOSPHERE",
            "source": METADATA_URL,
        }

    rows: List[Dict[str, Any]] = []
    matched_sites: set[int] = set()
    for station in catalog:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("id") or "").strip()
        if not station_id:
            continue
        sensors = sensors_by_station.get(station_id, _empty_sensors())
        # El catálogo sabe qué estaciones miden radiación global; si el
        # muestreo actual no trajo dato (noche) manda el flag del catálogo.
        if station.get("has_global_radiation"):
            sensors["pyranometer"] = True

        # Sitio klima más cercano (misma estación física) → histórico.
        best_idx, best_km = None, MATCH_MAX_KM
        lat, lon = station.get("lat"), station.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            for idx, site in enumerate(sites):
                canonical = site["canonical"]
                km = _haversine_km(lat, lon, canonical["lat"], canonical["lon"])
                if km < best_km:
                    best_idx, best_km = idx, km

        row = _base_row(station_id, station)
        row.update(
            {
                "network": "",
                "manual": False,
                "active_now": bool(station.get("is_active")) and station_id in online_ids,
                "sensors": sensors,
            }
        )
        if best_idx is not None:
            site = sites[best_idx]
            matched_sites.add(best_idx)
            row["klima_station_id"] = str(site["canonical"]["id"])
            row["klima_series"] = [_series_entry(r) for r in site["series"]]
            row["has_historical"] = True
        rows.append(row)

    # Sitios klima sin TAWES: estaciones convencionales de dato diario.
    for idx, site in enumerate(sites):
        if idx in matched_sites:
            continue
        canonical = site["canonical"]
        # Prefijo K: hay IDs klima que colisionan con IDs TAWES de OTRA
        # estación; el station_id conectable queda inequívoco (K105) y el
        # ID numérico para la API vive en klima_station_id.
        row = _base_row(f"K{canonical['id']}", canonical)
        sensors = _empty_sensors()
        # Toda serie klima diaria lleva temperatura y precipitación; la
        # radiación según el flag del catálogo.
        sensors["thermometer"] = True
        sensors["rain_gauge"] = True
        sensors["pyranometer"] = bool(canonical.get("has_global_radiation"))
        row.update(
            {
                "network": "KLIMA",
                "manual": True,
                "active_now": bool(canonical.get("is_active")),
                "sensors": sensors,
                "klima_station_id": str(canonical["id"]),
                "klima_series": [_series_entry(r) for r in site["series"]],
                "has_historical": True,
            }
        )
        rows.append(row)

    rows.sort(key=lambda row: (row.get("network") or "", row["id"]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=GEOSPHERE_STATIONS_PATH)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    rows = build_inventory(timeout=args.timeout)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "GEOSPHERE",
        "source": BASE_URL,
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    online = sum(1 for row in rows if row["active_now"])
    with_elevation = sum(1 for row in rows if row["elev"] is not None)
    tawes = [row for row in rows if not row.get("manual")]
    klima_only = [row for row in rows if row.get("manual")]
    with_historical = sum(1 for row in rows if row.get("has_historical"))
    print(f"Guardadas {len(rows)} estaciones GeoSphere en {args.output}")
    print(f"  TAWES (10 min):       {len(tawes)}")
    print(f"  KLIMA manuales:       {len(klima_only)} "
          f"({sum(1 for r in klima_only if r['active_now'])} activas)")
    print(f"  con histórico klima:  {with_historical}")
    print(f"  online (último dato): {online}")
    print(f"  con altitud:          {with_elevation}")
    for sensor in _empty_sensors():
        count = sum(1 for row in rows if row["sensors"].get(sensor))
        print(f"  {sensor:12} {count:4}")


if __name__ == "__main__":
    main()

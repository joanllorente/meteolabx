#!/usr/bin/env python3
"""
Construye el inventario de estaciones meteorológicas del Port de Barcelona
desde su portal CKAN de datos abiertos (``opendata.portdebarcelona.cat``,
CC BY-SA 4.0).

Cada estación es un conjunto de datos con cuatro recursos en el almacén de
CKAN: el último dato, los últimos tres días y un fichero por año. El
inventario guarda sus identificadores, que es lo que consulta el servicio.

Ni los metadatos ni la web del puerto dan la ubicación de las estaciones, así
que solo entran las que tienen coordenadas verificadas en ``STATIONS``:

  - Dispensari: el edificio «APB Serveis Mèdics» (el dispensario del Moll de
    Bosch i Alsina) del CSV oficial de direcciones del puerto.

Fuera, aunque se publiquen:
  - ZAL Prat y Bocana Sud: son las estaciones YQ e Y7 de la XEMA (Meteocat),
    que ya están en el catálogo. La ZAL Prat del puerto coincide con la YQ al
    décimo en temperatura, y su pluviómetro marcó 0 mm el 3/10/2026 con 58,3
    en Meteocat.
  - Sirena, Adossat, Dàrsena Sud y Bocana Nord: sin coordenadas todavía.

Uso:
  python3 scripts/build_portbcn_inventory.py --output data/data_estaciones_portbcn.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import PORTBCN_STATIONS_PATH

CKAN_URL = "https://opendata.portdebarcelona.cat/api/3/action"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
ONLINE_MAX_AGE = timedelta(hours=6)

# Conjunto de datos de CKAN → estación. Las variables de cada una salen de
# comprobar qué columnas traen datos (las de solo viento dejan el resto en NAN).
STATIONS: Dict[str, Dict[str, Any]] = {
    "estacio-meteorolgica-dispensari-bosch-i-alsina": {
        "id": "01",
        "name": "Port de Barcelona - Dispensari",
        "site": "Moll de Bosch i Alsina",
        "lat": 41.381774,
        "lon": 2.183219,
        "coords_source": "portbcn_adreces.csv: APB SERVEIS MEDICS (UTM 31N 431705, 4581461)",
        # El modelo del terreno de Open-Meteo (90 m) da 19 m: mezcla el muelle
        # con la ciudad. La cota de los muelles ronda los 3 m (Meteocat da 3 m a
        # Bocana Sud y 5 m a ZAL Prat), y con ella se reduce la presión.
        "elev": 3.0,
        "elevation_source": "cota del muelle (aproximada)",
    },
}

# Columna → sensor del catálogo.
COLUMN_SENSORS = {
    "TEM_Avg": "thermometer",
    "HUM_Avg": "hygrometer",
    "PRE_Avg": "barometer",
    "VV_S_WVT": "anemometer",
    "DV_D1_WVT": "wind_vane",
    "PLU_Tot": "rain_gauge",
    "RAD_Avg": "pyranometer",
}


def _fetch_json(url: str, *, timeout: int = 90) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _ckan(action: str, **params: Any) -> Any:
    payload = _fetch_json(f"{CKAN_URL}/{action}?{urlencode(params)}")
    if not payload.get("success"):
        raise RuntimeError(f"CKAN {action} falló: {payload.get('error')}")
    return payload["result"]


def _resources(package: Dict[str, Any]) -> Dict[str, Any]:
    """Recursos del almacén: el de tres días y uno por año."""
    out: Dict[str, Any] = {"recent": None, "years": {}}
    for resource in package.get("resources") or []:
        if not resource.get("datastore_active"):
            continue
        name = str(resource.get("name") or "").lower()
        if "3 dies" in name:
            out["recent"] = resource["id"]
            continue
        for token in name.replace("-", " ").split():
            if token.isdigit() and len(token) == 4:
                out["years"][token] = resource["id"]
    return out


def _sample(resource_id: str) -> List[Dict[str, Any]]:
    result = _ckan("datastore_search", resource_id=resource_id, sort="TIMESTAMP desc", limit=200)
    return result.get("records") or []


def _has_values(rows: List[Dict[str, Any]], column: str) -> bool:
    for row in rows:
        try:
            value = float(str(row.get(column)))
        except (TypeError, ValueError):
            continue
        if value == value:
            return True
    return False


def _elevation(lat: float, lon: float) -> Optional[float]:
    try:
        payload = _fetch_json(f"{ELEVATION_URL}?latitude={lat}&longitude={lon}")
        value = float((payload.get("elevation") or [None])[0])
    except Exception as exc:
        print(f"Aviso: sin altitud de Open-Meteo para {lat},{lon} ({exc})")
        return None
    return value if value == value else None


def build_inventory(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for package_name, station in STATIONS.items():
        package = _ckan("package_show", id=package_name)
        resources = _resources(package)
        if not resources["recent"]:
            print(f"Aviso: {package_name} sin recurso de tres días en el almacén")
            continue
        sample = _sample(resources["recent"])
        sensors = {sensor: False for sensor in (
            "thermometer", "hygrometer", "barometer", "anemometer",
            "wind_vane", "rain_gauge", "pyranometer", "uv",
        )}
        for column, sensor in COLUMN_SENSORS.items():
            sensors[sensor] = sensors[sensor] or _has_values(sample, column)
        last = max((str(row.get("TIMESTAMP") or "") for row in sample), default="")
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            last_dt = None
        active = last_dt is not None and now_utc - last_dt <= ONLINE_MAX_AGE
        elevation = station.get("elev")
        elevation_source = station.get("elevation_source")
        if elevation is None:
            elevation = _elevation(station["lat"], station["lon"])
            elevation_source = "open_meteo_dem" if elevation is not None else None
        rows.append({
            "id": station["id"],
            "source_id": package_name,
            "name": station["name"],
            "site": station["site"],
            "lat": station["lat"],
            "lon": station["lon"],
            "coords_source": station["coords_source"],
            "elev": elevation,
            "altitude": elevation,
            "elevation_source": elevation_source,
            "tz": "Europe/Madrid",
            "country": "España",
            "country_code": "ES",
            "region": "Barcelona",
            "municipality": "Barcelona",
            "network": "PORTBCN",
            "manual": False,
            "realtime": True,
            "active_now": bool(active),
            "status": "active" if active else "inactive",
            "has_historical": False,
            "last_observation_utc": last_dt.isoformat() if last_dt else None,
            "resource_recent": resources["recent"],
            "resource_years": resources["years"],
            "extra_sensors": [],
            "provider": "PORTBCN",
            "source": f"https://opendata.portdebarcelona.cat/dataset/{package_name}",
            "sensors": sensors,
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PORTBCN_STATIONS_PATH)
    args = parser.parse_args()

    rows = build_inventory()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "PORTBCN",
        "source": CKAN_URL,
        "license": "CC BY-SA 4.0 (Port de Barcelona)",
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Guardadas {len(rows)} estaciones del Port de Barcelona en {args.output}")
    for row in rows:
        sensors = ", ".join(name for name, present in row["sensors"].items() if present)
        print(f"  {row['id']} {row['name']}: {'online' if row['active_now'] else 'OFFLINE'} · {sensors}")


if __name__ == "__main__":
    main()

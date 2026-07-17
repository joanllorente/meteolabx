#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Suecia desde SMHI metobs
(opendata-download-metobs.smhi.se, open-data sin API key).

La API de SMHI es POR PARÁMETRO: cada parámetro publica su propia lista
de estaciones. El inventario es la unión de los parámetros que usa la
app, y los sensores de cada estación salen de su pertenencia a ellos.

Clasificación (mismo criterio que GeoSphere/Austria):
  - Automática: activa en algún parámetro HORARIO (red en tiempo real).
  - Manual (``network: "MANUAL"``): activa solo en parámetros DIARIOS
    (observadores de precipitación/temperatura, una lectura al día).
  - Archivo: inactiva en todo; queda con ``online=False`` para histórico.
Todas llevan ``has_historical`` (el corrected-archive cubre cada serie).

Uso:
  python3 scripts/build_smhi_inventory.py \
      --output data/data_estaciones_smhi.json
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

from data_files import SMHI_STATIONS_PATH

BASE_URL = "https://opendata-download-metobs.smhi.se/api/version/1.0"

# Parámetro horario → sensor. La racha (21) y la velocidad (4) comparten
# anemómetro; el 3 es la veleta.
HOURLY_PARAM_SENSORS = {
    "1": "thermometer",     # Lufttemperatur, momentan 1/h
    "6": "hygrometer",      # Relativ Luftfuktighet
    "9": "barometer",       # Lufttryck (MSL)
    "4": "anemometer",      # Vindhastighet, medel 10 min
    "3": "wind_vane",       # Vindriktning
    "21": "anemometer",     # Byvind (racha máx horaria)
    "7": "rain_gauge",      # Nederbörd 1 h
    "11": "pyranometer",    # Global Irradians
}
# Parámetro diario → sensor (red convencional/manual y climatológica).
DAILY_PARAM_SENSORS = {
    "2": "thermometer",     # Temp media diaria
    "19": "thermometer",    # Temp mín diaria
    "20": "thermometer",    # Temp máx diaria
    "5": "rain_gauge",      # Precipitación diaria
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


def build_inventory(*, timeout: int = 120) -> List[Dict[str, Any]]:
    # station_id → agregado de pertenencia a parámetros.
    stations: Dict[str, Dict[str, Any]] = {}

    def _ingest(param: str, sensor: str, *, hourly: bool) -> None:
        payload = _fetch_json(f"{BASE_URL}/parameter/{param}.json", timeout=timeout)
        for entry in payload.get("station", []) if isinstance(payload, dict) else []:
            if not isinstance(entry, dict):
                continue
            station_id = str(entry.get("key") or "").strip()
            if not station_id:
                continue
            record = stations.setdefault(
                station_id,
                {
                    "id": station_id,
                    "name": str(entry.get("name") or station_id).strip(),
                    "lat": entry.get("latitude"),
                    "lon": entry.get("longitude"),
                    "elev": entry.get("height"),
                    "owner": str(entry.get("owner") or "").strip(),
                    "sensors": _empty_sensors(),
                    "hourly_params": [],
                    "daily_params": [],
                    "active_hourly": False,
                    "active_daily": False,
                },
            )
            record["sensors"][sensor] = True
            bucket = "hourly_params" if hourly else "daily_params"
            if param not in record[bucket]:
                record[bucket].append(param)
            if entry.get("active"):
                record["active_hourly" if hourly else "active_daily"] = True

    for param, sensor in HOURLY_PARAM_SENSORS.items():
        _ingest(param, sensor, hourly=True)
    for param, sensor in DAILY_PARAM_SENSORS.items():
        _ingest(param, sensor, hourly=False)

    rows: List[Dict[str, Any]] = []
    for record in stations.values():
        # Manual = nunca tuvo parámetros horarios (red convencional de dato
        # diario). Una automática CERRADA sigue siendo automática (offline).
        automatic = bool(record["hourly_params"])
        active = bool(record["active_hourly"]) or (
            not automatic and bool(record["active_daily"])
        )
        rows.append(
            {
                "id": record["id"],
                "source_id": record["id"],
                "name": record["name"],
                "lat": record["lat"],
                "lon": record["lon"],
                "elev": record["elev"],
                "altitude": record["elev"],
                "tz": "Europe/Stockholm",
                "country": "Suecia",
                "country_code": "SE",
                "region": "",
                "owner": record["owner"],
                "network": "" if automatic else "MANUAL",
                "manual": not automatic,
                "active_now": active,
                "has_historical": True,
                "hourly_params": sorted(record["hourly_params"], key=int),
                "daily_params": sorted(record["daily_params"], key=int),
                "provider": "SMHI",
                "source": f"{BASE_URL}/parameter/1.json",
                "sensors": record["sensors"],
            }
        )

    rows.sort(key=lambda row: (row["network"], int(row["id"])))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SMHI_STATIONS_PATH)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    rows = build_inventory(timeout=args.timeout)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "SMHI",
        "source": BASE_URL,
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    automatic_active = [r for r in rows if not r["manual"] and r["active_now"]]
    manual_active = [r for r in rows if r["manual"] and r["active_now"]]
    archived = [r for r in rows if not r["active_now"]]
    print(f"Guardadas {len(rows)} estaciones SMHI en {args.output}")
    print(f"  automáticas activas: {len(automatic_active)}")
    print(f"  manuales activas:    {len(manual_active)}")
    print(f"  archivo (inactivas): {len(archived)}")
    for sensor in _empty_sensors():
        count = sum(1 for row in rows if row["sensors"].get(sensor))
        print(f"  {sensor:12} {count:5}")


if __name__ == "__main__":
    main()

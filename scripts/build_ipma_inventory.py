#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Portugal desde IPMA (open-data).

Cruza tres endpoints públicos (sin API key):
  - stations.json        catálogo (id, nombre, coordenadas)
  - observations.json    últimas 24 h por estación; un sensor se considera
                         presente si el campo tiene algún valor válido (-99.0
                         es el centinela de "sin dato" de IPMA)
  - obs-surface.geojson  últimas 3 h; una estación está online si reporta al
                         menos un valor válido reciente

IPMA no publica la altitud de sus estaciones; se resuelve por coordenadas con
el DEM de Open-Meteo (Copernicus GLO-90) salvo que se pase --skip-elevation.

Uso:
  python3 scripts/build_ipma_inventory.py \
      --output data/data_estaciones_ipma.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import IPMA_STATIONS_PATH

BASE_URL = "https://api.ipma.pt/open-data/observation/meteorology/stations"
STATIONS_URL = f"{BASE_URL}/stations.json"
OBSERVATIONS_URL = f"{BASE_URL}/observations.json"
SURFACE_URL = f"{BASE_URL}/obs-surface.geojson"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH = 100

NODATA = -99.0

# Campo de la observación IPMA -> sensor del catálogo. La veleta se trata
# aparte porque idDireccVento usa clases 0-9 en vez del centinela -99.0.
FIELD_SENSORS = {
    "temperatura": "thermometer",
    "humidade": "hygrometer",
    "pressao": "barometer",
    "intensidadeVento": "anemometer",
    "precAcumulada": "rain_gauge",
    "radiacao": "pyranometer",
}


def _fetch_json(url: str, *, timeout: int = 120) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _valid(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number != NODATA


def _valid_wind_direction(value: Any) -> bool:
    try:
        return 1 <= int(value) <= 9
    except (TypeError, ValueError):
        return False


def _merge_reading(sensors: Dict[str, bool], reading: Dict[str, Any]) -> bool:
    """Acumula los sensores vistos en una lectura; True si hubo algún dato."""
    any_data = False
    for field, sensor in FIELD_SENSORS.items():
        if _valid(reading.get(field)):
            sensors[sensor] = True
            any_data = True
    if _valid_wind_direction(reading.get("idDireccVento")):
        sensors["wind_vane"] = True
        any_data = True
    return any_data


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


def _zone(lat: Optional[float], lon: Optional[float]) -> tuple[str, str]:
    """(región, zona horaria IANA) según el archipiélago o el continente."""
    if lon is not None and lon <= -24.0:
        return "Açores", "Atlantic/Azores"
    if lon is not None and lat is not None and -18.5 <= lon <= -15.5 and 29.5 <= lat <= 34.0:
        return "Madeira", "Atlantic/Madeira"
    return "Continente", "Europe/Lisbon"


def _fetch_elevations(
    coordinates: List[tuple[float, float]], *, timeout: int = 120
) -> List[Optional[float]]:
    elevations: List[Optional[float]] = []
    for start in range(0, len(coordinates), ELEVATION_BATCH):
        batch = coordinates[start : start + ELEVATION_BATCH]
        params = urlencode(
            {
                "latitude": ",".join(f"{lat:.4f}" for lat, _ in batch),
                "longitude": ",".join(f"{lon:.4f}" for _, lon in batch),
            }
        )
        payload = _fetch_json(f"{ELEVATION_URL}?{params}", timeout=timeout)
        values = payload.get("elevation") if isinstance(payload, dict) else None
        if not isinstance(values, list) or len(values) != len(batch):
            raise RuntimeError("Respuesta de elevación inesperada de Open-Meteo")
        elevations.extend(float(value) if value is not None else None for value in values)
    return elevations


def _latest_observation_hours(observations: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for timestamp in sorted(observations, reverse=True):
        readings = observations.get(timestamp)
        if isinstance(readings, dict):
            yield readings


def build_inventory(*, skip_elevation: bool = False, timeout: int = 120) -> List[Dict[str, Any]]:
    catalog = _fetch_json(STATIONS_URL, timeout=timeout)
    observations = _fetch_json(OBSERVATIONS_URL, timeout=timeout)
    surface = _fetch_json(SURFACE_URL, timeout=timeout)

    sensors_by_station: Dict[str, Dict[str, bool]] = {}
    if isinstance(observations, dict):
        for readings in _latest_observation_hours(observations):
            for station_id, reading in readings.items():
                if not isinstance(reading, dict):
                    continue
                sensors = sensors_by_station.setdefault(str(station_id), _empty_sensors())
                _merge_reading(sensors, reading)

    online_ids: set[str] = set()
    features = surface.get("features") if isinstance(surface, dict) else None
    for feature in features or []:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        station_id = str(properties.get("idEstacao") or "").strip()
        if station_id and _merge_reading(
            sensors_by_station.setdefault(station_id, _empty_sensors()), properties
        ):
            online_ids.add(station_id)

    rows: List[Dict[str, Any]] = []
    for feature in catalog if isinstance(catalog, list) else []:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        station_id = str(properties.get("idEstacao") or "").strip()
        if not station_id:
            continue
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        lon, lat = (coordinates + [None, None])[:2] if isinstance(coordinates, list) else (None, None)
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
        region, tz = _zone(lat, lon)
        rows.append(
            {
                "id": station_id,
                "source_id": station_id,
                "name": str(properties.get("localEstacao") or station_id).strip(),
                "lat": lat,
                "lon": lon,
                "elev": None,
                "altitude": None,
                "tz": tz,
                "country": "Portugal",
                "country_code": "PT",
                "region": region,
                "active_now": station_id in online_ids,
                "provider": "IPMA",
                "source": STATIONS_URL,
                "elevation_source": None,
                "sensors": sensors_by_station.get(station_id, _empty_sensors()),
            }
        )

    if not skip_elevation:
        located = [row for row in rows if row["lat"] is not None and row["lon"] is not None]
        elevations = _fetch_elevations(
            [(row["lat"], row["lon"]) for row in located], timeout=timeout
        )
        for row, elevation in zip(located, elevations):
            row["elev"] = elevation
            row["altitude"] = elevation
            row["elevation_source"] = "open-meteo-dem" if elevation is not None else None

    rows.sort(key=lambda row: row["id"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=IPMA_STATIONS_PATH)
    parser.add_argument(
        "--skip-elevation",
        action="store_true",
        help="No resolver altitudes con el DEM de Open-Meteo",
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    rows = build_inventory(skip_elevation=args.skip_elevation, timeout=args.timeout)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "IPMA",
        "source": BASE_URL,
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    online = sum(1 for row in rows if row["active_now"])
    with_elevation = sum(1 for row in rows if row["elev"] is not None)
    print(f"Guardadas {len(rows)} estaciones IPMA en {args.output}")
    print(f"  online (últimas 3 h): {online}")
    print(f"  con altitud DEM:      {with_elevation}")
    for sensor in _empty_sensors():
        count = sum(1 for row in rows if row["sensors"].get(sensor))
        print(f"  {sensor:12} {count:4}")


if __name__ == "__main__":
    main()

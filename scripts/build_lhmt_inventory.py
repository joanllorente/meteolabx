#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Lituania desde LHMT
(Lietuvos hidrometeorologijos tarnyba): la API pública Meteo.lt
(``api.meteo.lt/v1``, sin API key, CC BY-SA 4.0).

Fuentes:
  - ``/stations`` + ``/stations/{code}``: lista y tipo de estación
    («Automatinė meteorologijos stotis» = automática).
  - ``/stations/{code}/observations``: rango de datos guardados. La API
    solo sirve los últimos 10 años, así que un inicio pegado a ese tope
    significa «al menos desde entonces».
  - ``/stations/{code}/observations/{latest|fecha}``: muestras horarias
    con las que se decide qué sensores tiene cada estación (una variable
    que llega siempre ``null`` es un sensor que no existe). Se miran las
    últimas 24 h y dos días sueltos anteriores para que un sensor averiado
    un día no desaparezca del inventario.
  - data.gov.lt ``lhmt/klimatologija/MeteorologineAikstele``: registro de
    recintos meteorológicos de LHMT con altitud, año de fundación y
    dirección. Se empareja por coordenadas (coinciden al micrograd).
  - Open-Meteo Elevation API: altitud del modelo digital del terreno para
    las estaciones que no están en ese registro (Anykščiai, abierta en 2022).

Clasificación:
  - Manual: tipo distinto de «Automatinė…» (hoy ninguna: la API solo
    publica la red automática).
  - Online (``active_now``): última observación hace menos de 6 h.
  - Histórico: todas (10 años en la API y archivo horario desde 2013 en
    data.gov.lt). La API no publica estaciones retiradas: el archivo de
    data.gov.lt contiene exactamente los mismos 52 códigos.

Uso:
  python3 scripts/build_lhmt_inventory.py --output data/data_estaciones_lhmt.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import LHMT_STATIONS_PATH

BASE_URL = "https://api.meteo.lt/v1"
SITES_URL = (
    "https://get.data.gov.lt/datasets/gov/lhmt/klimatologija/"
    "MeteorologineAikstele?limit(1000)"
)
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
# 180 peticiones/minuto por IP: con esta pausa se queda en ~150.
REQUEST_PAUSE_S = 0.4
ONLINE_MAX_AGE = timedelta(hours=6)
# Distancia máxima para dar por buena la pareja estación ↔ recinto.
SITE_MATCH_MAX_KM = 1.0
# Con el mismo topónimo se admite más distancia.
SITE_NAME_MATCH_MAX_KM = 5.0

# Variable de la API → sensor del catálogo.
VARIABLE_SENSORS = {
    "airTemperature": "thermometer",
    "relativeHumidity": "hygrometer",
    "seaLevelPressure": "barometer",
    "windSpeed": "anemometer",
    "windGust": "anemometer",
    "windDirection": "wind_vane",
    "precipitation": "rain_gauge",
}
# Variables sin columna propia en ``station_sensors``.
EXTRA_VARIABLES = {
    "snowDepth": "snow_depth",
    "cloudCover": "cloud_cover",
    "conditionCode": "present_weather",
}


def _fetch_json(url: str, *, timeout: int = 60) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _api(path: str) -> Optional[Any]:
    """GET a la API con pausa de cortesía; 404 = sin datos para esa fecha."""
    time.sleep(REQUEST_PAUSE_S)
    try:
        return _fetch_json(f"{BASE_URL}{path}")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _parse_point(text: Any) -> Optional[tuple]:
    """``POINT (lat lon)`` de data.gov.lt (el orden es latitud, longitud)."""
    try:
        inner = str(text).split("(", 1)[1].rsplit(")", 1)[0]
        lat, lon = (float(part) for part in inner.split())
        return lat, lon
    except (IndexError, ValueError):
        return None


def _load_sites() -> List[Dict[str, Any]]:
    try:
        payload = _fetch_json(SITES_URL, timeout=90)
    except Exception as exc:  # el registro solo enriquece: sin él seguimos
        print(f"Aviso: registro de recintos no disponible ({exc})")
        return []
    sites = []
    for row in payload.get("_data") or []:
        point = _parse_point(row.get("koord"))
        if point is None:
            continue
        sites.append({
            "name": str(row.get("mat_stotis") or "").strip(),
            "lat": point[0],
            "lon": point[1],
            "elev": row.get("aukst_virs_jur_lygio"),
            "founded": row.get("isteigta"),
            "address": str(row.get("meteorologines_aikst_adr") or "").strip(),
        })
    return sites


def _name_stem(text: str) -> str:
    """Raíz comparable del topónimo, sin la desinencia del genitivo:
    «Ventės AMS» y «Ventė» → «vent»."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return plain.split()[0].lower()[:4] if plain.split() else ""


def _nearest_site(
    lat: float, lon: float, name: str, sites: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Recinto de la estación: el que coincide en coordenadas o, si el
    registro conserva una ubicación algo distinta (Kalvarija, Ventė), el del
    mismo topónimo a pocos kilómetros."""
    best, best_km = None, SITE_MATCH_MAX_KM
    stem = _name_stem(name)
    for site in sites:
        km = _haversine_km(lat, lon, site["lat"], site["lon"])
        if km <= best_km:
            best, best_km = site, km
    if best is not None:
        return best
    for site in sites:
        km = _haversine_km(lat, lon, site["lat"], site["lon"])
        if km <= SITE_NAME_MATCH_MAX_KM and stem and _name_stem(site["name"]) == stem:
            return site
    return None


def _dem_elevation(lat: float, lon: float) -> Optional[float]:
    """Altitud del terreno según Open-Meteo (DEM de 90 m)."""
    try:
        payload = _fetch_json(f"{ELEVATION_URL}?latitude={lat}&longitude={lon}")
        value = float((payload.get("elevation") or [None])[0])
    except Exception as exc:
        print(f"Aviso: sin altitud de Open-Meteo para {lat},{lon} ({exc})")
        return None
    return value if value == value else None


def _parse_utc(text: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text).strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_inventory(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sites = _load_sites()
    stations = _fetch_json(f"{BASE_URL}/stations")
    sample_dates = [
        (now_utc - timedelta(days=days)).date().isoformat() for days in (7, 45)
    ]

    rows: List[Dict[str, Any]] = []
    for entry in stations:
        code = str(entry.get("code") or "").strip()
        if not code:
            continue
        coords = entry.get("coordinates") or {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        detail = _api(f"/stations/{code}") or {}
        data_range = (_api(f"/stations/{code}/observations") or {}).get("observationsDataRange") or {}

        observations: List[Dict[str, Any]] = []
        for when in ["latest", *sample_dates]:
            payload = _api(f"/stations/{code}/observations/{when}") or {}
            observations.extend(payload.get("observations") or [])

        sensors = _empty_sensors()
        extras = []
        for variable, sensor in VARIABLE_SENSORS.items():
            if any(obs.get(variable) is not None for obs in observations):
                sensors[sensor] = True
        for variable, label in EXTRA_VARIABLES.items():
            if any(obs.get(variable) is not None for obs in observations):
                extras.append(label)

        station_type = str(detail.get("type") or "").strip()
        manual = bool(station_type) and "automatin" not in station_type.lower()
        end = _parse_utc(data_range.get("endTimeUtc"))
        active = end is not None and now_utc - end <= ONLINE_MAX_AGE
        official_name = str(entry.get("name") or code).strip()
        site = (
            _nearest_site(float(lat), float(lon), official_name, sites)
            if lat is not None and lon is not None else None
        )

        elevation = (site or {}).get("elev")
        elevation_source = "lhmt_site_register" if elevation is not None else None
        if elevation is None and lat is not None and lon is not None:
            elevation = _dem_elevation(float(lat), float(lon))
            elevation_source = "open_meteo_dem" if elevation is not None else None

        rows.append({
            "id": code,
            "source_id": code,
            # El nombre de la API va en genitivo («Vilniaus AMS»); el del
            # registro de recintos es el topónimo («Vilnius»).
            "name": (site or {}).get("name") or official_name,
            "official_name": official_name,
            "lat": lat,
            "lon": lon,
            "elev": elevation,
            "altitude": elevation,
            "elevation_source": elevation_source,
            "tz": "Europe/Vilnius",
            "country": "Lituania",
            "country_code": "LT",
            "region": "",
            "municipality": (site or {}).get("name") or "",
            "address": (site or {}).get("address") or "",
            "founded": (site or {}).get("founded"),
            "station_type": station_type,
            "network": "MANUAL" if manual else "",
            "manual": manual,
            "active_now": active,
            "has_historical": True,
            "data_start_utc": data_range.get("startTimeUtc"),
            "data_end_utc": data_range.get("endTimeUtc"),
            "extra_sensors": extras,
            "provider": "LHMT",
            "source": f"{BASE_URL}/stations",
            "sensors": sensors,
        })

    rows.sort(key=lambda row: row["id"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LHMT_STATIONS_PATH)
    args = parser.parse_args()

    rows = build_inventory()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "LHMT",
        "source": BASE_URL,
        "license": "CC BY-SA 4.0 (Lietuvos hidrometeorologijos tarnyba)",
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print(f"Guardadas {len(rows)} estaciones LHMT en {args.output}")
    print(f"  automáticas online: {sum(1 for r in rows if not r['manual'] and r['active_now'])}")
    print(f"  manuales online:    {sum(1 for r in rows if r['manual'] and r['active_now'])}")
    print(f"  offline:            {sum(1 for r in rows if not r['active_now'])}")
    print(f"  altitud de Open-Meteo: {sum(1 for r in rows if r['elevation_source'] == 'open_meteo_dem')}")
    print(f"  sin altitud:           {sum(1 for r in rows if r['elev'] is None)}")
    for sensor in _empty_sensors():
        print(f"  {sensor:12} {sum(1 for r in rows if r['sensors'].get(sensor)):5}")
    for extra in EXTRA_VARIABLES.values():
        print(f"  {extra:12} {sum(1 for r in rows if extra in r['extra_sensors']):5}")


if __name__ == "__main__":
    main()

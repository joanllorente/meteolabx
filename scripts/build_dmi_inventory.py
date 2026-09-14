#!/usr/bin/env python3
"""
Construye el inventario de estaciones de DMI (Danmarks Meteorologiske
Institut): Dinamarca, Groenlandia y las Feroe, desde la API abierta
``opendataapi.dmi.dk/v2`` (sin clave, CC BY 4.0).

Fuentes:
  - ``metObs/collections/station/items``: una fila por VERSIÓN de estación
    (cambian coordenadas, sensores o estado); la vigente es la que tiene
    ``validTo`` vacío. Trae tipo (Synop, Pluvio, GIWS, Manual precipitation,
    Manual snow), estado (Active/Inactive), fechas de operación y la lista de
    parámetros que publica, de la que salen los sensores.
  - ``metObs/collections/observation/items``: qué estaciones han publicado de
    verdad, para no dar por online una estación «Active» muda. Se mira el bulk
    de las últimas 24 h de los parámetros comunes y, a las que no salen ahí
    (hay Synop que solo miden radiación o viento), su propio último día.

Clasificación:
  - Automáticas: Synop (estaciones completas), Pluvio (pluviómetros) y GIWS
    (red groenlandesa de aeropuertos y puertos). Online si han publicado en
    las últimas 24 h.
  - Manuales: «Manual precipitation» (lluvia de 24 h leída a mano, en
    Groenlandia). DMI las publica por tandas con semanas de retraso, así que
    no tienen dato actual: siguen activas si han publicado en el último mes y
    medio (``realtime: False``).
  - «Manual snow» (espesor de nieve a mano) queda fuera: la app no tiene
    dónde enseñar ese dato y serían fichas vacías.
  - Histórico: la API de clima (``climateData``) empieza en 2011, así que lo
    tiene toda estación que operase desde entonces. ``archive_start`` es su
    inicio recortado a 2011 y ``archive_end`` su cierre (vacío si sigue).

Uso:
  python3 scripts/build_dmi_inventory.py --output data/data_estaciones_dmi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import DMI_STATIONS_PATH

API_URL = "https://opendataapi.dmi.dk/v2/metObs/collections"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
CLIMATE_FIRST_DAY = date(2011, 1, 1)

AUTOMATIC_TYPES = {"Synop", "Pluvio", "GIWS"}
MANUAL_TYPES = {"Manual precipitation"}
COUNTRIES = {"DNK": ("DK", "Dinamarca"), "GRL": ("GL", "Groenlandia"), "FRO": ("FO", "Islas Feroe")}

PARAMETER_SENSORS = {
    "temp_dry": "thermometer",
    "humidity": "hygrometer",
    "pressure": "barometer",
    "pressure_at_sea": "barometer",
    "wind_speed": "anemometer",
    "wind_max": "anemometer",
    "wind_dir": "wind_vane",
    "precip_past10min": "rain_gauge",
    "precip_past1h": "rain_gauge",
    "precip_past24h": "rain_gauge",
    "radia_glob": "pyranometer",
}
EXTRA_PARAMETERS = {
    "snow_depth_man": "snow_depth",
    "visibility": "visibility",
    "cloud_cover": "cloud_cover",
    "weather": "present_weather",
    "temp_grass": "grass_temperature",
    "temp_soil": "soil_temperature",
}


def _get_json(url: str) -> Any:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _empty_sensors() -> Dict[str, bool]:
    return {
        "thermometer": False, "hygrometer": False, "barometer": False,
        "anemometer": False, "wind_vane": False, "rain_gauge": False,
        "pyranometer": False, "uv": False,
    }


def station_timezone(country: str, lat: float, lon: float) -> str:
    """Huso IANA. Groenlandia tiene cuatro: Pituffik (Thule), la costa NE de
    Danmarkshavn, la zona de Ittoqqortoormiit y el resto (Nuuk)."""
    if country == "DK":
        return "Europe/Copenhagen"
    if country == "FO":
        return "Atlantic/Faroe"
    if lat >= 75.0 and lon <= -60.0:
        return "America/Thule"
    if lon > -30.0 and lat >= 74.0:
        return "America/Danmarkshavn"
    if lon > -30.0 and lat >= 68.0:
        return "America/Scoresbysund"
    return "America/Nuuk"


def _parse_time(text: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _recent_publishers(parameter: str, period: str) -> set:
    payload = _get_json(f"{API_URL}/observation/items?parameterId={parameter}&period={period}&limit=300000")
    return {feature["properties"]["stationId"] for feature in payload.get("features") or []}


def _station_published(station_id: str, since: datetime) -> bool:
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = _get_json(f"{API_URL}/observation/items?stationId={station_id}&datetime={stamp}/..&limit=1")
    return bool(payload.get("features"))


def build_inventory() -> List[Dict[str, Any]]:
    payload = _get_json(f"{API_URL}/station/items?limit=10000")
    versions: Dict[str, List[Dict[str, Any]]] = {}
    for feature in payload.get("features") or []:
        props = dict(feature.get("properties") or {})
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        props["lon"], props["lat"] = coords[0], coords[1]
        versions.setdefault(str(props.get("stationId")), []).append(props)

    now = datetime.now(timezone.utc)
    alive_day = set()
    for parameter in ("temp_dry", "precip_past10min", "wind_speed", "pressure_at_sea"):
        alive_day |= _recent_publishers(parameter, "latest-day")

    rows: List[Dict[str, Any]] = []
    skipped_snow = 0
    for station_id, history in sorted(versions.items()):
        history.sort(key=lambda item: str(item.get("validFrom") or ""))
        current = next((item for item in reversed(history) if item.get("validTo") is None), history[-1])
        kind = str(current.get("type") or "").strip()
        if kind == "Manual snow" or not kind:
            skipped_snow += int(kind == "Manual snow")
            continue
        if kind not in AUTOMATIC_TYPES | MANUAL_TYPES:
            continue
        country_code, country_name = COUNTRIES.get(str(current.get("country")), ("DK", "Dinamarca"))
        if current.get("lat") is None or current.get("lon") is None:
            continue

        sensors = _empty_sensors()
        extras = set()
        for parameter in current.get("parameterId") or []:
            if parameter in PARAMETER_SENSORS:
                sensors[PARAMETER_SENSORS[parameter]] = True
            if parameter in EXTRA_PARAMETERS:
                extras.add(EXTRA_PARAMETERS[parameter])

        manual = kind in MANUAL_TYPES
        # Cerrada: la versión vigente se dio de baja, o ninguna versión sigue
        # abierta (06132 tiene todas con fin).
        closed = str(current.get("status")) == "Inactive" or current.get("validTo") is not None
        if closed:
            published = False
        elif manual:
            published = _station_published(station_id, now - timedelta(days=45))
        else:
            published = station_id in alive_day or _station_published(station_id, now - timedelta(days=1))
        active = not closed and published

        started = min((_parse_time(item.get("operationFrom")) for item in history if item.get("operationFrom")), default=None)
        ended = _parse_time(current.get("operationTo")) or (
            _parse_time(current.get("validTo")) if current.get("validTo") else None
        )
        start_day = max(started.date(), CLIMATE_FIRST_DAY) if started else CLIMATE_FIRST_DAY
        end_day = ended.date() if (closed and ended) else None
        has_historical = end_day is None or end_day >= CLIMATE_FIRST_DAY

        lat, lon = float(current["lat"]), float(current["lon"])
        rows.append({
            "id": station_id,
            "source_id": station_id,
            "name": str(current.get("name") or station_id).strip(),
            "lat": lat,
            "lon": lon,
            "elev": current.get("stationHeight"),
            "altitude": current.get("stationHeight"),
            "tz": station_timezone(country_code, lat, lon),
            "country": country_name,
            "country_code": country_code,
            "region": "",
            "owner": str(current.get("owner") or "").strip(),
            "wmo_id": str(current.get("wmoStationId") or "").strip(),
            "station_type": kind,
            "network": "MANUAL" if manual else kind.upper(),
            "manual": manual and not closed,
            "realtime": not manual,
            "active_now": active,
            "status": "historical" if closed else "active",
            "has_historical": has_historical,
            "archive_start": start_day.isoformat() if has_historical else None,
            "archive_end": end_day.isoformat() if end_day else None,
            "extra_sensors": sorted(extras),
            # Parámetros que publica: el servicio pide solo esos, uno a uno
            # (la API no admite varios en la misma consulta).
            "parameters": sorted(current.get("parameterId") or []),
            "provider": "DMI",
            "source": f"{API_URL}/station",
            "sensors": sensors,
        })

    # Una cerrada antes de 2011 no tiene nada que enseñar: ni dato actual ni
    # histórico en la API.
    kept = [row for row in rows if row["active_now"] or row["has_historical"]]
    print(f"  «Manual snow» descartadas (solo nieve): {skipped_snow}")
    print(f"  cerradas antes de 2011 descartadas: {len(rows) - len(kept)}")
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DMI_STATIONS_PATH)
    args = parser.parse_args()

    rows = build_inventory()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "DMI",
        "source": "https://opendataapi.dmi.dk/v2",
        "license": "CC BY 4.0 (Danmarks Meteorologiske Institut)",
        "stations": rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    from collections import Counter

    print(f"Guardadas {len(rows)} estaciones DMI en {args.output}")
    print(f"  por país: {dict(Counter(r['country_code'] for r in rows))}")
    print(f"  por tipo: {dict(Counter(r['station_type'] for r in rows))}")
    print(f"  automáticas online:  {sum(1 for r in rows if not r['manual'] and r['active_now'])}")
    print(f"  automáticas offline (siguen dadas de alta): {sum(1 for r in rows if not r['manual'] and not r['active_now'] and r['status'] == 'active')}")
    print(f"  manuales online:     {sum(1 for r in rows if r['manual'] and r['active_now'])}")
    print(f"  solo históricas:     {sum(1 for r in rows if r['status'] == 'historical')}")
    for sensor in _empty_sensors():
        print(f"  {sensor:12} {sum(1 for r in rows if r['sensors'].get(sensor)):5}")


if __name__ == "__main__":
    main()

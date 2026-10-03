#!/usr/bin/env python3
"""
Construye el inventario de pluviómetros de la ACA (Agència Catalana de
l'Aigua) desde su plataforma Sentilo de datos en tiempo real
(``aplicacions.aca.gencat.cat/sdim2/apirest``, pública y sin clave).

Fuentes:
  - ``/catalog?componentType=pluviometre``: un sensor por estación
    («Intensitat de precipitació», mm/h) con coordenadas, comarca, término
    municipal, cuenca y río.
  - ``/data/PLUVIOMETREACA-EST``: último dato de todos los sensores, para
    decidir cuáles están vivos.
  - Open-Meteo Elevation API: altitud del modelo digital del terreno (el
    catálogo no la publica).

Solo lluvia: el resto de sensores del catálogo son aforos, embalses y
piezómetros. Sin histórico: la plataforma borra las medidas a los tres meses.

Uso:
  python3 scripts/build_aca_inventory.py --output data/data_estaciones_aca.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import ACA_STATIONS_PATH

BASE_URL = "https://aplicacions.aca.gencat.cat/sdim2/apirest"
PROVIDER_CODE = "PLUVIOMETREACA-EST"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
ONLINE_MAX_AGE = timedelta(hours=6)
# Open-Meteo admite hasta 100 puntos por consulta.
ELEVATION_BATCH = 100

# Artículos que el catálogo pospone en mayúsculas: «ROCA DEL VALLÈS, LA».
_TRAILING_ARTICLES = ("LA", "EL", "LES", "ELS", "L'")
_LOWER_WORDS = {"de", "del", "d", "la", "les", "el", "els", "i", "l", "dels", "de la"}


def _fetch_json(url: str, *, timeout: int = 90) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _nice_case(text: Any) -> str:
    """«ROCA DEL VALLÈS, LA» → «La Roca del Vallès»; «BAIX LLOBREGAT» →
    «Baix Llobregat»."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if "," in raw:
        head, tail = (part.strip() for part in raw.rsplit(",", 1))
        if tail.upper() in _TRAILING_ARTICLES:
            raw = f"{tail}{'' if tail.endswith(chr(39)) else ' '}{head}"
    words = re.split(r"(\s+|-|')", raw.lower())
    out = []
    for index, word in enumerate(words):
        if index > 0 and word in _LOWER_WORDS:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return "".join(out)


def _parse_location(text: Any) -> Optional[tuple]:
    """``"41.51 1.91"`` (latitud y longitud separadas por un espacio)."""
    try:
        lat, lon = (float(part) for part in str(text).split())
    except (TypeError, ValueError):
        return None
    return lat, lon


def _elevations(points: List[tuple]) -> List[Optional[float]]:
    """Altitud del terreno según Open-Meteo (DEM de 90 m), en lotes."""
    out: List[Optional[float]] = []
    for start in range(0, len(points), ELEVATION_BATCH):
        batch = points[start:start + ELEVATION_BATCH]
        query = urlencode({
            "latitude": ",".join(f"{lat:.6f}" for lat, _ in batch),
            "longitude": ",".join(f"{lon:.6f}" for _, lon in batch),
        })
        try:
            values = _fetch_json(f"{ELEVATION_URL}?{query}").get("elevation") or []
        except Exception as exc:
            print(f"Aviso: sin altitud de Open-Meteo ({exc})")
            values = []
        for index in range(len(batch)):
            try:
                value = float(values[index])
            except (IndexError, TypeError, ValueError):
                value = None
            out.append(value if value is not None and value == value else None)
    return out


def _last_observations() -> Dict[str, int]:
    """Epoch UTC del último dato de cada sensor."""
    payload = _fetch_json(f"{BASE_URL}/data/{PROVIDER_CODE}")
    out: Dict[str, int] = {}
    for sensor in payload.get("sensors") or []:
        observations = sensor.get("observations") or []
        if observations and observations[0].get("time") is not None:
            out[str(sensor.get("sensor"))] = int(observations[0]["time"]) // 1000
    return out


def _sensors() -> Dict[str, bool]:
    return {
        "thermometer": False,
        "hygrometer": False,
        "barometer": False,
        "anemometer": False,
        "wind_vane": False,
        "rain_gauge": True,
        "pyranometer": False,
        "uv": False,
    }


def build_inventory(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    catalog = _fetch_json(f"{BASE_URL}/catalog?componentType=pluviometre")
    last = _last_observations()

    rows: List[Dict[str, Any]] = []
    for provider in catalog.get("providers") or []:
        if provider.get("provider") != PROVIDER_CODE:
            continue
        for sensor in provider.get("sensors") or []:
            component = str(sensor.get("component") or "").strip()
            sensor_id = str(sensor.get("sensor") or "").strip()
            point = _parse_location(sensor.get("location"))
            if not component or not sensor_id or point is None:
                continue
            info = sensor.get("componentAdditionalInfo") or {}
            extra = sensor.get("additionalInfo") or {}
            last_epoch = last.get(sensor_id)
            active = (
                last_epoch is not None
                and now_utc - datetime.fromtimestamp(last_epoch, tz=timezone.utc) <= ONLINE_MAX_AGE
            )
            try:
                step_min = int(extra.get("Temps mostreig (min)"))
            except (TypeError, ValueError):
                step_min = None
            rows.append({
                "id": component,
                "source_id": component,
                "sensor": sensor_id,
                "name": str(sensor.get("componentDesc") or component).strip(),
                "lat": point[0],
                "lon": point[1],
                "elev": None,
                "altitude": None,
                "elevation_source": None,
                "tz": "Europe/Madrid",
                "country": "España",
                "country_code": "ES",
                "region": _nice_case(info.get("Comarca")),
                "province": _nice_case(info.get("Província")),
                "municipality": _nice_case(info.get("Terme municipal")),
                "basin": _nice_case(info.get("Conca")),
                "river": _nice_case(info.get("Riu")),
                "site": str(info.get("Topònim") or "").strip(),
                "unit": str(sensor.get("unit") or "").strip(),
                "step_min": step_min,
                "network": "ACA",
                "manual": False,
                "realtime": True,
                "active_now": bool(active),
                "status": "active" if active else "inactive",
                "has_historical": False,
                "last_observation_utc": (
                    datetime.fromtimestamp(last_epoch, tz=timezone.utc).isoformat()
                    if last_epoch is not None else None
                ),
                "extra_sensors": [],
                "provider": "ACA",
                "source": f"{BASE_URL}/catalog?componentType=pluviometre",
                "sensors": _sensors(),
            })

    elevations = _elevations([(row["lat"], row["lon"]) for row in rows])
    for row, elevation in zip(rows, elevations):
        row["elev"] = row["altitude"] = elevation
        row["elevation_source"] = "open_meteo_dem" if elevation is not None else None

    rows.sort(key=lambda row: row["id"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ACA_STATIONS_PATH)
    args = parser.parse_args()

    rows = build_inventory()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "ACA",
        "source": BASE_URL,
        "license": "Dades obertes (Agència Catalana de l'Aigua)",
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print(f"Guardados {len(rows)} pluviómetros ACA en {args.output}")
    print(f"  online:      {sum(1 for r in rows if r['active_now'])}")
    print(f"  offline:     {sum(1 for r in rows if not r['active_now'])}")
    print(f"  sin altitud: {sum(1 for r in rows if r['elev'] is None)}")
    print(f"  sin paso declarado: {sum(1 for r in rows if r['step_min'] is None)}")


if __name__ == "__main__":
    main()

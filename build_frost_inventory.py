#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Noruega desde MET Norway Frost.

Uso:
  python3 build_frost_inventory.py \
      --client-id YOUR_CLIENT_ID \
      --client-secret YOUR_CLIENT_SECRET \
      --output data_estaciones_frost.json
"""

from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://frost.met.no/sources/v0.jsonld"
DEFAULT_OUTPUT = "data_estaciones_frost.json"
DEFAULT_VALIDTIME = "1900-01-01/now"
DEFAULT_FIELDS = (
    "id,name,shortName,country,countryCode,wmoId,geometry,masl,validFrom,validTo,"
    "county,countyId,municipality,municipalityId,ontologyId,stationHolders,"
    "externalIds,icaoCodes,shipCodes,wigosId"
)


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    token = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _fetch_sources(
    client_id: str,
    client_secret: str,
    *,
    current_only: bool = False,
    timeout: int = 180,
) -> Dict[str, Any]:
    params = {
        "country": "NO",
        "types": "SensorSystem",
        "fields": DEFAULT_FIELDS,
    }
    if not current_only:
        params["validtime"] = DEFAULT_VALIDTIME
    url = f"{API_URL}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Authorization": _basic_auth_header(client_id, client_secret),
            "Accept": "application/json",
            "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_listish(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [text]
    return [str(value).strip()]


def _parse_coordinates(raw_geometry: Any) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(raw_geometry, dict):
        return None, None
    coords = raw_geometry.get("coordinates")
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        try:
            lon = float(coords[0])
            lat = float(coords[1])
            return lat, lon
        except Exception:
            return None, None
    if isinstance(coords, str):
        parts = [part.strip() for part in coords.split(",")]
        if len(parts) >= 2:
            try:
                first = float(parts[0])
                second = float(parts[1])
            except Exception:
                return None, None
            # Algunos ejemplos antiguos del swagger muestran "lat, lon" como string.
            if abs(first) > 35 and abs(second) <= 35:
                return first, second
            return second, first
    return None, None


def _parse_iso8601(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _iso_or_none(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _normalize_station(item: Dict[str, Any], now_utc: datetime) -> Dict[str, Any]:
    lat, lon = _parse_coordinates(item.get("geometry"))
    valid_to_dt = _parse_iso8601(item.get("validTo"))
    active_now = valid_to_dt is None or valid_to_dt >= now_utc
    wmo = item.get("wmoId")

    return {
        "id": str(item.get("id", "")).strip(),
        "source_id": str(item.get("id", "")).strip(),
        "name": str(item.get("name", "")).strip(),
        "short_name": str(item.get("shortName", "")).strip() or None,
        "lat": lat,
        "lon": lon,
        "elev": _to_float(item.get("masl")),
        "altitude": _to_float(item.get("masl")),
        "country": str(item.get("country", "")).strip() or None,
        "country_code": str(item.get("countryCode", "")).strip() or None,
        "wmo_id": None if wmo in (None, "") else str(wmo),
        "county": str(item.get("county", "")).strip() or None,
        "county_id": _to_int(item.get("countyId")),
        "municipality": str(item.get("municipality", "")).strip() or None,
        "municipality_id": _to_int(item.get("municipalityId")),
        "ontology_id": _to_int(item.get("ontologyId")),
        "valid_from": _iso_or_none(item.get("validFrom")),
        "valid_to": _iso_or_none(item.get("validTo")),
        "active_now": bool(active_now),
        "station_holders": _parse_listish(item.get("stationHolders")),
        "external_ids": _parse_listish(item.get("externalIds")),
        "icao_codes": _parse_listish(item.get("icaoCodes")),
        "ship_codes": _parse_listish(item.get("shipCodes")),
        "wigos_id": str(item.get("wigosId", "")).strip() or None,
        "provider": "FROST",
        "source": "MET Norway Frost /sources/v0.jsonld",
        "raw": item,
    }


def _normalize_inventory(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    stations = [_normalize_station(item, now_utc) for item in items if item.get("id")]
    stations.sort(key=lambda station: (station.get("id") or "", station.get("name") or ""))
    return stations


def _save_inventory(stations: List[Dict[str, Any]], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(stations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Construye inventario Frost de estaciones noruegas.")
    parser.add_argument("--client-id", required=True, help="Client ID de Frost")
    parser.add_argument("--client-secret", required=True, help="Client secret de Frost")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Ruta de salida JSON (por defecto: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="Incluye solo estaciones actualmente válidas. Por defecto se incluye histórico + activas.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    payload = _fetch_sources(
        args.client_id,
        args.client_secret,
        current_only=args.current_only,
    )
    items = payload.get("data")
    if not isinstance(items, list):
        raise RuntimeError(f"Respuesta inesperada de Frost: {payload!r}")

    stations = _normalize_inventory(items)
    output_path = Path(args.output).resolve()
    _save_inventory(stations, output_path)

    active_count = sum(1 for station in stations if station.get("active_now"))
    historical_count = len(stations) - active_count

    print(f"Inventario guardado en: {output_path}")
    print(f"Total estaciones: {len(stations)}")
    print(f"Activas ahora: {active_count}")
    print(f"Solo históricas: {historical_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

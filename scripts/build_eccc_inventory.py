#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Canadá desde ECCC MSC GeoMet
(api.weather.gc.ca, OGC API Features, sin API key).

Cruza tres colecciones:
  - swob-stations          red automática de MSC en tiempo real (~930)
  - swob-partner-stations  redes asociadas (provinciales/otros, ~1900)
  - climate-stations       catálogo climatológico (~8600, con rangos de
                           fechas diario/horario/mensual desde 1840)

Cada estación SWOB se enlaza a su estación climática (histórico) por
WMO id o por proximidad (≤1 km). Las climáticas sin SWOB entran como
estaciones propias (``network: "CLIMATE"``, ``manual: true``): dato
diario, activas si su serie diaria llega a los últimos 45 días.

El station_id conectable es el ``msc_id`` (SWOB) o el
``CLIMATE_IDENTIFIER`` (climáticas); el enlace al histórico viaja en
``climate_identifier``.

Uso:
  python3 scripts/build_eccc_inventory.py \
      --output data/data_estaciones_eccc.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import ECCC_STATIONS_PATH

BASE_URL = "https://api.weather.gc.ca"
MATCH_MAX_KM = 1.0

# Provincia/territorio → zona horaria IANA (aproximación por provincia;
# suficiente para el "día local" de ranking e interfaz).
PROVINCE_TZ = {
    "NL": "America/St_Johns",
    "NS": "America/Halifax", "NB": "America/Halifax", "PE": "America/Halifax",
    "QC": "America/Toronto", "ON": "America/Toronto",
    "MB": "America/Winnipeg",
    "SK": "America/Regina",
    "AB": "America/Edmonton",
    "BC": "America/Vancouver",
    "YT": "America/Whitehorse",
    "NT": "America/Yellowknife",
    "NU": "America/Iqaluit",
}
PROVINCE_NAMES_TO_CODE = {
    "NEWFOUNDLAND AND LABRADOR": "NL", "NOVA SCOTIA": "NS",
    "NEW BRUNSWICK": "NB", "PRINCE EDWARD ISLAND": "PE",
    "QUEBEC": "QC", "ONTARIO": "ON", "MANITOBA": "MB",
    "SASKATCHEWAN": "SK", "ALBERTA": "AB", "BRITISH COLUMBIA": "BC",
    "YUKON": "YT", "YUKON TERRITORY": "YT",
    "NORTHWEST TERRITORIES": "NT", "NUNAVUT": "NU",
}


def _fetch_json(url: str, *, timeout: int = 180) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_collection(collection: str, *, limit: int = 10000) -> List[Dict[str, Any]]:
    features: List[Dict[str, Any]] = []
    offset = 0
    while True:
        payload = _fetch_json(
            f"{BASE_URL}/collections/{collection}/items?f=json&limit={limit}&offset={offset}"
        )
        batch = payload.get("features", []) if isinstance(payload, dict) else []
        features.extend(f for f in batch if isinstance(f, dict))
        if len(batch) < limit:
            return features
        offset += limit


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((rlat2 - rlat1) / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin((rlon2 - rlon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def _province_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in PROVINCE_TZ:
        return text
    return PROVINCE_NAMES_TO_CODE.get(text, "")


def _empty_sensors() -> Dict[str, bool]:
    return {
        "thermometer": False, "hygrometer": False, "barometer": False,
        "anemometer": False, "wind_vane": False, "rain_gauge": False,
        "pyranometer": False, "uv": False,
    }


def _swob_row(feature: Dict[str, Any], network: str) -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    msc_id = str(props.get("msc_id") or "").strip()
    if not msc_id:
        return None
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    lat = coords[1] if len(coords) >= 2 else None
    lon = coords[0] if len(coords) >= 2 else None
    elev = coords[2] if len(coords) >= 3 else None
    province = _province_code(props.get("province_territory"))
    name = str(props.get("name") or props.get("name_en") or msc_id).strip()
    return {
        "id": msc_id,
        "source_id": msc_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "elev": elev,
        "altitude": elev,
        "tz": PROVINCE_TZ.get(province, "America/Toronto"),
        "country": "Canadá",
        "country_code": "CA",
        "region": province,
        "network": network,
        "manual": str(props.get("auto_man") or "").strip().upper() == "MAN",
        "active_now": True,  # los catálogos SWOB solo listan la red operativa
        "has_historical": False,
        "wmo_id": props.get("wmo_id"),
        "iata_id": str(props.get("iata_id") or "").strip(),
        "data_provider": str(
            props.get("data_provider") or props.get("data_provider_en") or ""
        ).strip(),
        "provider": "ECCC",
        "source": f"{BASE_URL}/collections/swob-stations",
        "sensors": None,  # SWOB no publica inventario de sensores
    }


def _climate_meta(feature: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    climate_id = str(props.get("CLIMATE_IDENTIFIER") or "").strip()
    if not climate_id:
        return None

    def _scaled(value: Any) -> Optional[float]:
        try:
            return float(value) / 1e7
        except (TypeError, ValueError):
            return None

    def _date(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text[:10] or None

    return {
        "climate_id": climate_id,
        "name": str(props.get("STATION_NAME") or climate_id).strip(),
        "lat": _scaled(props.get("LATITUDE")),
        "lon": _scaled(props.get("LONGITUDE")),
        "elev": (
            float(props.get("ELEVATION"))
            if str(props.get("ELEVATION") or "").replace(".", "", 1).isdigit()
            else None
        ),
        "province": _province_code(props.get("PROV_STATE_TERR_CODE")),
        "wmo_id": props.get("WMO_IDENTIFIER"),
        "first_date": _date(props.get("FIRST_DATE")),
        "last_date": _date(props.get("LAST_DATE")),
        "dly_first": _date(props.get("DLY_FIRST_DATE")),
        "dly_last": _date(props.get("DLY_LAST_DATE")),
    }


def build_inventory(*, timeout: int = 180) -> List[Dict[str, Any]]:
    swob = _fetch_collection("swob-stations")
    partners = _fetch_collection("swob-partner-stations")
    climate = _fetch_collection("climate-stations")

    rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for feature in swob:
        row = _swob_row(feature, "")
        if row and row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            rows.append(row)
    for feature in partners:
        row = _swob_row(feature, "PARTNER")
        if row and row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            rows.append(row)

    metas = [m for m in (_climate_meta(f) for f in climate) if m]

    # Índices para el enlace SWOB → climática: por WMO y por celda espacial.
    by_wmo: Dict[int, Dict[str, Any]] = {}
    grid: Dict[tuple, List[Dict[str, Any]]] = {}
    for meta in metas:
        try:
            wmo = int(meta.get("wmo_id"))
            by_wmo.setdefault(wmo, meta)
        except (TypeError, ValueError):
            pass
        if meta["lat"] is not None and meta["lon"] is not None:
            key = (round(meta["lat"], 1), round(meta["lon"], 1))
            grid.setdefault(key, []).append(meta)

    def _nearest(lat: float, lon: float) -> Optional[Dict[str, Any]]:
        best, best_km = None, MATCH_MAX_KM
        base = (round(lat, 1), round(lon, 1))
        for dlat in (-0.1, 0.0, 0.1):
            for dlon in (-0.1, 0.0, 0.1):
                key = (round(base[0] + dlat, 1), round(base[1] + dlon, 1))
                for meta in grid.get(key, []):
                    km = _haversine_km(lat, lon, meta["lat"], meta["lon"])
                    if km < best_km:
                        best, best_km = meta, km
        return best

    matched: set[str] = set()
    fresh_cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    for row in rows:
        meta = None
        try:
            meta = by_wmo.get(int(row.get("wmo_id")))
        except (TypeError, ValueError):
            meta = None
        if meta is None and row["lat"] is not None and row["lon"] is not None:
            meta = _nearest(float(row["lat"]), float(row["lon"]))
        if meta is None:
            continue
        matched.add(meta["climate_id"])
        row["climate_identifier"] = meta["climate_id"]
        row["series_first_date"] = meta.get("first_date")
        row["has_historical"] = True

    for meta in metas:
        if meta["climate_id"] in matched or meta["climate_id"] in seen_ids:
            continue
        sensors = _empty_sensors()
        sensors["thermometer"] = True
        sensors["rain_gauge"] = True
        active = bool(meta.get("dly_last") and meta["dly_last"] >= fresh_cutoff)
        rows.append(
            {
                "id": meta["climate_id"],
                "source_id": meta["climate_id"],
                "name": meta["name"],
                "lat": meta["lat"],
                "lon": meta["lon"],
                "elev": meta["elev"],
                "altitude": meta["elev"],
                "tz": PROVINCE_TZ.get(meta["province"], "America/Toronto"),
                "country": "Canadá",
                "country_code": "CA",
                "region": meta["province"],
                "network": "CLIMATE",
                "manual": True,
                "active_now": active,
                "has_historical": True,
                "climate_identifier": meta["climate_id"],
                "series_first_date": meta.get("first_date"),
                "series_last_date": meta.get("last_date"),
                "provider": "ECCC",
                "source": f"{BASE_URL}/collections/climate-stations",
                "sensors": sensors,
            }
        )

    rows.sort(key=lambda row: (row.get("network") or "", row["id"]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ECCC_STATIONS_PATH)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    rows = build_inventory(timeout=args.timeout)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "ECCC",
        "source": BASE_URL,
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    swob_rows = [r for r in rows if r["network"] in ("", "PARTNER")]
    with_hist = sum(1 for r in rows if r.get("has_historical"))
    climate_rows = [r for r in rows if r["network"] == "CLIMATE"]
    print(f"Guardadas {len(rows)} estaciones ECCC en {args.output}")
    print(f"  SWOB tiempo real:      {len(swob_rows)} "
          f"(MSC {sum(1 for r in swob_rows if not r['network'])}, "
          f"partner {sum(1 for r in swob_rows if r['network'] == 'PARTNER')})")
    print(f"  con histórico enlazado: {sum(1 for r in swob_rows if r.get('has_historical'))}")
    print(f"  climáticas propias:     {len(climate_rows)} "
          f"({sum(1 for r in climate_rows if r['active_now'])} activas)")
    print(f"  con histórico total:    {with_hist}")


if __name__ == "__main__":
    main()

"""
Adaptador de Meteo-France al contrato comun de proveedores.
"""

import json
from functools import lru_cache
from typing import Any, Dict, List

from aemet_utils import haversine_distance

from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_stations(stations_path: str) -> List[Dict[str, Any]]:
    try:
        with open(stations_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


class MeteofranceProvider:
    provider_id = "METEOFRANCE"
    provider_name = "Meteo-France"

    def __init__(self, stations_path: str = "data_estaciones_meteofrance.json"):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = _load_stations(self.stations_path)
        results: List[StationCandidate] = []

        for station in stations:
            if not isinstance(station, dict):
                continue

            station_id = str(station.get("id_station") or station.get("id") or "").strip()
            if not station_id:
                continue

            try:
                s_lat = float(station.get("lat"))
                s_lon = float(station.get("lon"))
                s_alt = float(station.get("elev", station.get("altitude", 0.0)) or 0.0)
            except Exception:
                continue

            if not (-90.0 <= s_lat <= 90.0 and -180.0 <= s_lon <= 180.0):
                continue

            try:
                dist_km = float(haversine_distance(lat, lon, s_lat, s_lon))
            except Exception:
                continue

            results.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=station_id,
                    name=str(station.get("name") or station.get("nom_usuel") or station_id).strip(),
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=s_alt,
                    distance_km=dist_km,
                    metadata=station,
                )
            )

        results.sort(key=lambda s: s.distance_km)
        return results[: max(1, int(max_results))]

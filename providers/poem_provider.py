"""
Adaptador de POEM (Puertos del Estado) al contrato comun de proveedores.
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


class PoemProvider:
    provider_id = "POEM"
    provider_name = "POEM"

    def __init__(self, stations_path: str = "data_estaciones_poem.json"):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = _load_stations(self.stations_path)
        results: List[StationCandidate] = []

        for station in stations:
            if not isinstance(station, dict):
                continue

            station_id = str(station.get("codigo", "")).strip()
            if not station_id:
                continue

            try:
                s_lat = float(station.get("lat"))
                s_lon = float(station.get("lon"))
            except Exception:
                continue

            if not (-90.0 <= s_lat <= 90.0 and -180.0 <= s_lon <= 180.0):
                continue

            try:
                dist_km = float(haversine_distance(lat, lon, s_lat, s_lon))
            except Exception:
                continue

            name = str(station.get("nombre") or station_id).strip()
            results.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=station_id,
                    name=name,
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=0.0,
                    distance_km=dist_km,
                    metadata=station,
                )
            )

        results.sort(key=lambda s: s.distance_km)
        return results[: max(1, int(max_results))]

"""
Adaptador de NWS (weather.gov) al contrato comun de proveedores.
"""

import json
from functools import lru_cache
from typing import Dict, List

from aemet_utils import haversine_distance

from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_stations(stations_path: str) -> List[Dict]:
    try:
        with open(stations_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


class NwsProvider:
    provider_id = "NWS"
    provider_name = "NWS"

    def __init__(self, stations_path: str = "data_estaciones_nws.json"):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = _load_stations(self.stations_path)
        results: List[StationCandidate] = []

        for station in stations:
            if not isinstance(station, dict):
                continue

            station_id = str(station.get("id", "")).strip().upper()
            if not station_id:
                continue

            try:
                s_lat = float(station.get("lat"))
                s_lon = float(station.get("lon"))
                s_alt = float(station.get("elev", 0.0) or 0.0)
            except Exception:
                continue

            # Normalizacion defensiva por si llega alguna coordenada invertida.
            if not (-90.0 <= s_lat <= 90.0 and -180.0 <= s_lon <= 180.0):
                if -90.0 <= s_lon <= 90.0 and -180.0 <= s_lat <= 180.0:
                    s_lat, s_lon = s_lon, s_lat
                else:
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
                    name=str(station.get("name", "")).strip() or station_id,
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=s_alt,
                    distance_km=dist_km,
                    metadata=station,
                )
            )

        results.sort(key=lambda s: s.distance_km)
        return results[: max(1, int(max_results))]

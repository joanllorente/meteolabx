"""
Adaptador de Euskalmet al contrato común de proveedores.
"""
import json
from functools import lru_cache
from typing import List

from data_files import EUSKALMET_STATIONS_PATH
from .helpers import maybe_swap_coordinates, nearest_records
from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_stations(stations_path: str):
    with open(stations_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


class EuskalmetProvider:
    provider_id = "EUSKALMET"
    provider_name = "Euskalmet"

    def __init__(self, stations_path: str = str(EUSKALMET_STATIONS_PATH)):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = _load_stations(self.stations_path)
        nearest = nearest_records(
            lat,
            lon,
            stations,
            max_results=max_results,
            get_coords=lambda station: maybe_swap_coordinates(
                float(station.get("lat")),
                float(station.get("lon")),
            ) if isinstance(station, dict) and station.get("lat") is not None and station.get("lon") is not None else None,
        )
        results: List[StationCandidate] = []

        for station, dist_km in nearest:
            if not isinstance(station, dict):
                continue
            station_id = str(station.get("stationId", "")).strip()
            if not station_id:
                continue

            try:
                s_lat = float(station.get("lat"))
                s_lon = float(station.get("lon"))
                alt = float(station.get("altitude_m", 0.0) or 0.0)
            except Exception:
                continue

            name = str(station.get("displayName") or station.get("stationId") or "Euskalmet")
            results.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=station_id,
                    name=name,
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=alt,
                    distance_km=dist_km,
                    metadata=station,
                )
            )

        return results

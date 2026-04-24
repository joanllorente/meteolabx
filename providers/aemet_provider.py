"""
Adaptador de AEMET al contrato común de proveedores.
"""
import json
from functools import lru_cache
from typing import Dict, List

from data_files import AEMET_STATIONS_PATH
from .helpers import nearest_records, maybe_swap_coordinates
from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_stations(stations_path: str) -> List[Dict]:
    with open(stations_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stations = data.get("estaciones", [])
    return stations if isinstance(stations, list) else []


class AemetProvider:
    provider_id = "AEMET"
    provider_name = "AEMET"

    def __init__(self, stations_path: str = str(AEMET_STATIONS_PATH)):
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
            ) if station.get("lat") is not None and station.get("lon") is not None else None,
        )

        normalized = []
        for station, distance in nearest:
            normalized.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=str(station.get("idema", "")),
                    name=str(station.get("nombre", "")),
                    lat=float(station.get("lat", 0.0)),
                    lon=float(station.get("lon", 0.0)),
                    elevation_m=float(station.get("alt", 0.0)),
                    distance_km=float(distance),
                    metadata=station,
                )
            )
        return normalized

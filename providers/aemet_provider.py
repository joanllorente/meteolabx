"""
Adaptador de AEMET al contrato común de proveedores.
"""
from typing import List
from aemet_utils import find_nearest_station, load_stations
from .types import StationCandidate


class AemetProvider:
    provider_id = "AEMET"
    provider_name = "AEMET"

    def __init__(self, stations_path: str = "data_estaciones_aemet.json"):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = load_stations(self.stations_path)
        nearest = find_nearest_station(lat, lon, stations, max_results=max_results)

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


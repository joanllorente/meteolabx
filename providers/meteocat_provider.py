"""
Adaptador de Meteocat al contrato común de proveedores.
"""
import json
from functools import lru_cache
from typing import List

from aemet_utils import haversine_distance
from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_meteocat_stations(stations_path: str):
    with open(stations_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _has_open_status(station: dict) -> bool:
    """
    Filtra estaciones sin cierre conocido (dataFi nula/vacía).
    """
    statuses = station.get("estats", [])
    if not isinstance(statuses, list) or not statuses:
        return True
    for status in statuses:
        if not isinstance(status, dict):
            continue
        if status.get("dataFi") in (None, ""):
            return True
    return False


class MeteocatProvider:
    provider_id = "METEOCAT"
    provider_name = "Meteocat"

    def __init__(self, stations_path: str = "data_estaciones_meteocat.json"):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = _load_meteocat_stations(self.stations_path)
        results = []

        for station in stations:
            if not isinstance(station, dict):
                continue
            if not _has_open_status(station):
                continue

            coords = station.get("coordenades", {}) or {}
            s_lat = coords.get("latitud")
            s_lon = coords.get("longitud")
            if s_lat is None or s_lon is None:
                continue

            try:
                s_lat = float(s_lat)
                s_lon = float(s_lon)
                dist_km = float(haversine_distance(lat, lon, s_lat, s_lon))
            except Exception:
                continue

            results.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=str(station.get("codi", "")),
                    name=str(station.get("nom", "")),
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=float(station.get("altitud", 0.0) or 0.0),
                    distance_km=dist_km,
                    metadata=station,
                )
            )

        results.sort(key=lambda s: s.distance_km)
        return results[:max_results]

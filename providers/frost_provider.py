"""
Adaptador de Frost (MET Norway) al contrato comun de proveedores.
"""

import json
from functools import lru_cache
from typing import Any, Dict, List

from data_files import FROST_STATIONS_PATH
from .helpers import maybe_swap_coordinates, nearest_records
from .types import StationCandidate


@lru_cache(maxsize=2)
def _load_stations(stations_path: str) -> List[Dict[str, Any]]:
    try:
        with open(stations_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


class FrostProvider:
    provider_id = "FROST"
    provider_name = "Frost"

    def __init__(self, stations_path: str = str(FROST_STATIONS_PATH)):
        self.stations_path = stations_path

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        stations = [
            station
            for station in _load_stations(self.stations_path)
            if isinstance(station, dict)
            and station.get("active_now") is not False
            and str(station.get("id") or station.get("source_id") or "").strip()
        ]
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
            station_id = str(station.get("id") or station.get("source_id") or "").strip().upper()

            try:
                s_lat = float(station.get("lat"))
                s_lon = float(station.get("lon"))
                s_alt = float(station.get("elev", station.get("altitude", 0.0)) or 0.0)
            except Exception:
                continue

            metadata = dict(station)
            metadata.setdefault("tz", "Europe/Oslo")

            results.append(
                StationCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    station_id=station_id,
                    name=str(station.get("name") or station.get("short_name") or station_id).strip(),
                    lat=s_lat,
                    lon=s_lon,
                    elevation_m=s_alt,
                    distance_km=dist_km,
                    metadata=metadata,
                )
            )

        return results

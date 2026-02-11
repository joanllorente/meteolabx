"""
Capa de acceso a proveedores de estaciones.
"""
from .types import StationCandidate
from .registry import get_provider, get_providers, search_nearby_stations

__all__ = [
    "StationCandidate",
    "get_provider",
    "get_providers",
    "search_nearby_stations",
]


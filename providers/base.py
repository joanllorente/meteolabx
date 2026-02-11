"""
Contrato base para proveedores de estaciones.
"""
from typing import List, Protocol
from .types import StationCandidate


class StationProvider(Protocol):
    """Interfaz común para búsqueda de estaciones por proximidad."""
    provider_id: str
    provider_name: str

    def search_nearby_stations(self, lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
        """Devuelve estaciones cercanas normalizadas para este proveedor."""
        ...


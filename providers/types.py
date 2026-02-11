"""
Tipos de dominio para proveedores de estaciones meteorológicas.
"""
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass(frozen=True)
class StationCandidate:
    """Representa una estación normalizada, independiente del proveedor."""
    provider_id: str
    provider_name: str
    station_id: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    distance_km: float
    metadata: Dict[str, Any] = field(default_factory=dict)


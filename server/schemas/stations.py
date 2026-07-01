"""Esquemas del inventario de estaciones (``/v1/stations/*``)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from server.schemas.observation import StationInfo


class StationWithDistance(StationInfo):
    """Estación del catálogo + distancia al punto de búsqueda."""

    distance_km: float = Field(description="Distancia al punto de búsqueda (km).")


class StationSearchResponse(BaseModel):
    """Resultado de ``GET /v1/stations/near``."""

    count: int = Field(description="Número de estaciones devueltas.")
    stations: List[StationWithDistance] = Field(default_factory=list)


class GeocodeResponse(BaseModel):
    """First geocoding match for a textual location query."""

    found: bool = False
    lat: Optional[float] = None
    lon: Optional[float] = None
    display_name: str = ""


class WeatherLinkStationsRequest(BaseModel):
    """Credenciales personales necesarias para listar estaciones WeatherLink."""

    api_key: str = Field(min_length=1, max_length=256)
    api_secret: str = Field(min_length=1, max_length=256)


class WeatherLinkStationsResponse(BaseModel):
    stations: List[Dict[str, Any]] = Field(default_factory=list)

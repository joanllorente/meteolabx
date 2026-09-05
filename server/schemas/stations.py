"""Esquemas del inventario de estaciones (``/v1/stations/*``)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from server.schemas.observation import StationInfo


class StationWithDistance(StationInfo):
    """Estación del catálogo + distancia al punto de búsqueda."""

    distance_km: float = Field(description="Distancia al punto de búsqueda (km).")


class IndexableStation(StationInfo):
    """Estación resuelta desde el slug de su URL pública.

    ``url_slug`` es el identificador canónico de ``/{idioma}/observation/{slug}``:
    quien reciba esta respuesta a partir de otro slug (mayúsculas, un alias
    antiguo) debe redirigir 301 a este.
    """

    url_slug: str = Field(description="Slug canónico de la URL indexable.")
    catalog_country: str = Field(
        default="",
        description=(
            "País tal cual figura en el catálogo, sin correcciones por "
            "proveedor. Decide en qué idiomas existe la ficha."
        ),
    )
    indexable: bool = Field(
        default=True,
        description=(
            "``False`` para estaciones ocultas, sin coordenadas o fuera de "
            "servicio: la ficha se sigue sirviendo, pero con ``noindex``."
        ),
    )


class IndexableStationSlug(BaseModel):
    """Entrada mínima del catálogo indexable, para construir el sitemap."""

    url_slug: str
    provider: str
    catalog_country: str = ""


class IndexableCatalogResponse(BaseModel):
    """Página de ``GET /v1/stations/indexable``."""

    total: int = Field(description="Total de estaciones indexables del catálogo.")
    offset: int = 0
    count: int = Field(description="Estaciones devueltas en esta página.")
    stations: List[IndexableStationSlug] = Field(default_factory=list)


class NearbyIndexableStation(BaseModel):
    """Vecina indexable de una ficha, para el enlazado interno."""

    provider: str
    station_id: str
    name: str
    url_slug: str
    distance_km: float


class NearbyIndexableResponse(BaseModel):
    count: int
    stations: List[NearbyIndexableStation] = Field(default_factory=list)


class MapCatalogResponse(BaseModel):
    """Catálogo de un país en formato compacto, para pintarlo en el mapa.

    El mapa solo necesita dónde está cada estación, de qué red es y cómo se
    llama. Devolver la ficha entera —sensores, timezone, banderas— multiplica
    por seis el peso: España pasa de 3,4 MB a poco más de medio mega, y son
    megas que viajan enteros antes de que se vea un punto.

    Las columnas van en arrays paralelos por el mismo motivo: repetir las
    claves del objeto en cada una de las 7.866 filas es la mitad del JSON.
    """

    countries: List[str] = Field(default_factory=list)
    count: int = Field(description="Estaciones devueltas.")
    total: int = Field(description="Estaciones del país antes del recorte.")
    truncated: bool = Field(
        default=False,
        description="``True`` si se alcanzó el límite y faltan estaciones por enviar.",
    )
    lat: List[float] = Field(default_factory=list)
    lon: List[float] = Field(default_factory=list)
    name: List[str] = Field(default_factory=list)
    provider: List[str] = Field(default_factory=list)
    station_id: List[str] = Field(default_factory=list)


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

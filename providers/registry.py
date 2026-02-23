"""
Registro y orquestación de proveedores de estaciones.
"""
from typing import Dict, List, Optional
from .aemet_provider import AemetProvider
from .euskalmet_provider import EuskalmetProvider
from .meteocat_provider import MeteocatProvider
from .meteogalicia_provider import MeteogaliciaProvider
from .nws_provider import NwsProvider
from .types import StationCandidate


def get_providers() -> Dict[str, object]:
    """
    Devuelve proveedores habilitados.
    Nota: incluye AEMET, Meteocat, Euskalmet, MeteoGalicia y NWS.
    """
    providers = [AemetProvider(), MeteocatProvider(), EuskalmetProvider(), MeteogaliciaProvider(), NwsProvider()]
    return {p.provider_id: p for p in providers}


def get_provider(provider_id: str) -> Optional[object]:
    return get_providers().get(provider_id)


def search_nearby_stations(lat: float, lon: float, max_results: int = 5) -> List[StationCandidate]:
    """
    Busca estaciones cercanas en todos los proveedores habilitados.
    """
    results: List[StationCandidate] = []
    for provider in get_providers().values():
        try:
            results.extend(provider.search_nearby_stations(lat, lon, max_results=max_results))
        except Exception:
            # Aislar fallos de un proveedor para no bloquear el resto.
            continue

    results.sort(key=lambda s: s.distance_km)
    return results[:max_results]

"""
Registro y orquestación de proveedores de estaciones.
"""
import logging
from functools import lru_cache
from typing import Dict, List, Optional, Sequence
from .aemet_provider import AemetProvider
from .euskalmet_provider import EuskalmetProvider
from .frost_provider import FrostProvider
from .meteocat_provider import MeteocatProvider
from .meteofrance_provider import MeteofranceProvider
from .meteogalicia_provider import MeteogaliciaProvider
from .nws_provider import NwsProvider
from .poem_provider import PoemProvider
from .types import StationCandidate

logger = logging.getLogger(__name__)


def _per_provider_result_budget(max_results: int, provider_count: int) -> int:
    """
    Limita el número de candidatos pedidos a cada proveedor.
    Pedimos algo más que el reparto exacto para no empeorar la calidad
    del top-N global, pero evitamos sobrecargar cada backend.
    """
    max_results = max(1, int(max_results))
    provider_count = max(1, int(provider_count))
    return min(max_results, max(2, ((max_results + provider_count - 1) // provider_count) + 1))

@lru_cache(maxsize=1)
def get_providers() -> Dict[str, object]:
    """
    Devuelve proveedores habilitados.
    Nota: incluye AEMET, Meteocat, Euskalmet, Frost, Meteo-France, MeteoGalicia, NWS y POEM.
    """
    providers = [
        AemetProvider(),
        MeteocatProvider(),
        EuskalmetProvider(),
        FrostProvider(),
        MeteofranceProvider(),
        MeteogaliciaProvider(),
        NwsProvider(),
        PoemProvider(),
    ]
    return {p.provider_id: p for p in providers}


def get_provider(provider_id: str) -> Optional[object]:
    return get_providers().get(str(provider_id or "").strip().upper())


def search_nearby_stations(
    lat: float,
    lon: float,
    max_results: int = 5,
    provider_ids: Optional[Sequence[str]] = None,
) -> List[StationCandidate]:
    """
    Busca estaciones cercanas en todos los proveedores habilitados.
    """
    allowed = {
        str(provider_id).strip().upper()
        for provider_id in (provider_ids or [])
        if str(provider_id).strip()
    }
    results: List[StationCandidate] = []
    providers = [
        provider
        for provider in get_providers().values()
        if not allowed or str(getattr(provider, "provider_id", "")).strip().upper() in allowed
    ]
    per_provider_max = _per_provider_result_budget(max_results=max_results, provider_count=len(providers))

    for provider in providers:
        try:
            results.extend(provider.search_nearby_stations(lat, lon, max_results=per_provider_max))
        except Exception as exc:
            logger.warning(
                "Búsqueda de estaciones falló para proveedor %s: %s",
                getattr(provider, "provider_id", "UNKNOWN"),
                exc,
            )
            continue

    results.sort(key=lambda s: s.distance_km)
    return results[:max_results]

"""
Registro y orquestación de proveedores de estaciones.
"""
import logging
from functools import lru_cache
from typing import Dict, List, Optional, Sequence
from .types import StationCandidate
from utils.api_errors import BackendApiError

logger = logging.getLogger(__name__)


PROVIDER_NAMES = {
    "AEMET": "AEMET",
    "METEOCAT": "Meteocat",
    "EUSKALMET": "Euskalmet",
    "FROST": "Frost",
    "METEOFRANCE": "Meteo-France",
    "METEOGALICIA": "MeteoGalicia",
    "NWS": "NWS",
    "POEM": "POEM",
    "METOFFICE": "Met Office",
    "METEOHUB_IT": "MeteoHub IT",
    "IPMA": "IPMA",
    "GEOSPHERE": "GeoSphere",
    "SMHI": "SMHI",
    "ECCC": "ECCC",
    "IEM": "IEM",
    "WINDY": "Windy PWS",
    "NETATMO": "Netatmo",
}


@lru_cache(maxsize=1)
def get_providers() -> Dict[str, object]:
    """
    Devuelve proveedores locales habilitados.

    La búsqueda de estaciones es 100% FastAPI/SQLite; este registry ya no
    instancia adaptadores locales de inventario.
    """
    return {}


def get_provider(provider_id: str) -> Optional[object]:
    return get_providers().get(str(provider_id or "").strip().upper())


def search_nearby_stations(
    lat: float,
    lon: float,
    max_results: int = 5,
    provider_ids: Optional[Sequence[str]] = None,
    countries: Optional[Sequence[str]] = None,
    has_historical: bool = False,
    hide_historical_only: bool = False,
) -> List[StationCandidate]:
    """
    Busca estaciones cercanas en el catálogo canónico de FastAPI.
    """
    allowed = {
        str(provider_id).strip().upper()
        for provider_id in (provider_ids or [])
        if str(provider_id).strip()
    }
    try:
        from utils.api_client import fetch_station_catalog_via_api, fetch_stations_near_via_api

        country_filter = [
            str(country).strip().upper()
            for country in (countries or [])
            if str(country).strip()
        ]
        if country_filter:
            payload = fetch_station_catalog_via_api(
                lat=lat,
                lon=lon,
                max_results=max_results,
                provider_ids=sorted(allowed),
                countries=country_filter,
                has_historical=has_historical,
                hide_historical_only=hide_historical_only,
            )
        else:
            payload = fetch_stations_near_via_api(
                lat,
                lon,
                max_results=max_results,
                provider_ids=sorted(allowed),
                countries=country_filter,
                has_historical=has_historical,
                hide_historical_only=hide_historical_only,
            )

        rows = payload.get("stations", []) if isinstance(payload, dict) else []
        results = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            provider_id = str(row.get("provider") or row.get("provider_id") or "").strip().upper()
            station_id = str(row.get("station_id") or "").strip()
            network = str(row.get("network") or "").strip()
            if provider_id == "IEM" and network and "|" not in station_id:
                station_id = f"{network}|{station_id}"
            if not provider_id or not station_id:
                continue
            results.append(
                StationCandidate(
                    provider_id=provider_id,
                    provider_name=_provider_name(provider_id),
                    station_id=station_id,
                    name=str(row.get("name") or station_id).strip(),
                    lat=float(row.get("lat") or 0.0),
                    lon=float(row.get("lon") or 0.0),
                    elevation_m=float(row.get("elevation") or 0.0),
                    distance_km=float(row.get("distance_km") or 0.0),
                    connectable=bool(row.get("connectable", True)),
                    metadata={
                        **row,
                        "network": network,
                        "sensors": row.get("sensors"),
                        "tz": row.get("tz"),
                    },
                )
            )
        return results[:max_results]
    except (BackendApiError, OSError, ValueError, TypeError) as exc:
        logger.warning("Búsqueda FastAPI de estaciones no disponible: %s", exc)
        return []


def _provider_name(provider_id: str) -> str:
    provider_id = str(provider_id or "").strip().upper()
    return PROVIDER_NAMES.get(provider_id, provider_id)

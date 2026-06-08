"""Router del endpoint /health."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server import __version__
from server.config import Settings, get_settings
from server.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Estado del servicio",
    description=(
        "Endpoint de liveness/readiness. Devuelve la versión del backend y "
        "de la API. Útil para Railway healthchecks y para que el frontend "
        "confirme que la URL configurada es alcanzable."
    ),
)
def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        ok=True,
        version=__version__,
        api_version=settings.api_version,
    )

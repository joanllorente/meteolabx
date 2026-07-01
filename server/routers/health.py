"""Router del endpoint /health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

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


def _credentials_status(settings: Settings) -> dict:
    """
    Estado de configuración por proveedor:
    ``server_key`` (configurada/ausente), ``per_user`` (las credenciales
    viajan en cada request) o ``public`` (sin credenciales).
    """
    def _key(value: str) -> str:
        return "configured" if str(value or "").strip() else "missing"

    euskalmet_ok = bool(
        str(settings.euskalmet_jwt or "").strip()
        or str(settings.euskalmet_private_key_path or "").strip()
    )
    return {
        "WU": "per_user",
        "WEATHERLINK": "per_user",
        "METEOGALICIA": "public",
        "NWS": "public",
        "METEOHUB_IT": "public",
        "POEM": "public",  # auth opcional; sin ella el feed público funciona
        "AEMET": _key(settings.aemet_api_key),
        "METEOCAT": _key(settings.meteocat_api_key),
        "METEOFRANCE": _key(settings.meteofrance_api_key),
        "METOFFICE": _key(settings.metoffice_api_key),
        "FROST": "configured" if (
            str(settings.frost_client_id or "").strip()
            and str(settings.frost_client_secret or "").strip()
        ) else "missing",
        "EUSKALMET": "configured" if euskalmet_ok else "missing",
    }


@router.get(
    "/health/providers",
    summary="Estado por proveedor",
    description=(
        "Para cada proveedor: estado de credenciales en el servidor "
        "(``configured``/``missing``/``per_user``/``public``) y métricas "
        "runtime (llamadas reales al upstream, errores, último OK y "
        "último error). Las métricas se resetean al reiniciar el proceso."
    ),
)
def get_health_providers(settings: Settings = Depends(get_settings)) -> dict:
    from server.services import metrics

    runtime = metrics.snapshot()
    credentials = _credentials_status(settings)
    providers = sorted(set(credentials) | set(runtime))
    return {
        provider: {
            "credentials": credentials.get(provider, "unknown"),
            **runtime.get(
                provider,
                {"calls": 0, "errors": 0, "last_ok_epoch": None, "last_error": None},
            ),
        }
        for provider in providers
    }


@router.get(
    "/diagnostics",
    summary="Diagnóstico del backend",
    description=(
        "Contadores de caché (hits/misses/coalescing por caché) y "
        "métricas por proveedor. En memoria del proceso: se resetea al "
        "reiniciar y no se agrega entre réplicas."
    ),
)
def get_diagnostics(request: Request) -> dict:
    from server.services import metrics

    caches = {}
    for name in ("current", "series"):
        cache = getattr(request.app.state, f"cache_{name}", None)
        if cache is not None and hasattr(cache, "stats"):
            caches[name] = cache.stats()
    return {
        "caches": caches,
        "providers": metrics.snapshot(),
    }

"""
App FastAPI de MeteoLabX.

Lanzar en desarrollo::

    uvicorn server.main:app --reload --port 8000

El frontend Streamlit (puerto 8501) consume estos endpoints. Mientras
dure la migración, ambos procesos conviven; el frontend va sustituyendo
cálculos locales por llamadas a esta API endpoint a endpoint.

Convención de rutas: **todas** las rutas funcionales viven bajo
``/{api_version}/...`` (por defecto ``/v1/...``). Esto evita romper a los
consumidores cuando haya breaking changes — basta con publicar ``/v2/...``
y dejar ``/v1/...`` con el comportamiento antiguo durante una ventana.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server import __version__
from server.config import Settings, get_settings
from server.dependencies.http import http_client_lifespan
from server.routers import climo, health, observations, ranking, stations
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app() -> FastAPI:
    """
    Factory de la app. Se usa un factory (en vez de instanciar ``FastAPI()``
    a nivel de módulo) para poder crear apps frescas en tests sin caches
    pegadas, y para retrasar la lectura de settings hasta el momento del
    arranque.
    """
    settings = get_settings()
    _configure_logging(settings)

    app = FastAPI(
        title="MeteoLabX API",
        version=__version__,
        description=(
            "Backend de MeteoLabX. Expone observaciones meteorológicas en "
            "tiempo real, series temporales y tendencias a partir de "
            "múltiples proveedores."
        ),
        # Solo exponemos /docs y /redoc si debug=True para no dar pistas
        # gratuitas en producción. En local quedan en `/docs`.
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        # Lifespan: arranca un httpx.AsyncClient compartido y lo cierra
        # al apagar el server. Los endpoints lo reciben vía Depends.
        lifespan=http_client_lifespan,
    )

    # CORS: Streamlit en :8501 hace requests desde otro origen. Sin esto
    # el navegador bloquea las llamadas. La lista de orígenes permitidos
    # vive en Settings (env var METEOLABX_CORS_ORIGINS en producción).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Exception handler único para ProviderError. Cualquier servicio
    # (server/services/*) que lance ProviderError genera automáticamente
    # una respuesta JSON con el shape de ErrorResponse y el status_code
    # adecuado. Así los servicios no necesitan importar FastAPI.
    @app.exception_handler(ProviderError)
    async def _provider_error_handler(_: Request, exc: ProviderError) -> JSONResponse:
        logger.info(
            "ProviderError: code=%s provider=%s status=%s detail=%s",
            exc.error_code, exc.provider, exc.status_code, exc.detail,
        )
        from server.services import metrics

        metrics.record_error(exc.provider, exc.error_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(),
        )

    # Routers versionados. Cada router que se añada aquí se cuelga del
    # prefijo ``/v1/...``. Los nuevos dominios (stations, observations,
    # trends, historical) seguirán este mismo patrón.
    api_prefix = f"/{settings.api_version}"
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(observations.router, prefix=api_prefix)
    app.include_router(climo.router, prefix=api_prefix)
    app.include_router(stations.router, prefix=api_prefix)
    app.include_router(ranking.router, prefix=api_prefix)

    return app


# Instancia exportada para uvicorn (``uvicorn server.main:app``).
app = create_app()

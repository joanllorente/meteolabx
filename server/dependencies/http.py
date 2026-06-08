"""
Recursos compartidos del backend (cliente HTTP y cachés) gestionados
por lifespan.

¿Por qué un cliente compartido?
- ``httpx.AsyncClient`` reusa conexiones TCP (keep-alive) y limita el
  número de conexiones simultáneas. Crear un cliente nuevo por request
  desperdicia el handshake TLS (decenas de ms por proveedor).
- Permite configurar timeouts y límites de pool en un solo sitio.

¿Por qué cachés compartidos?
- Misma instancia → varios usuarios concurrentes a la misma estación
  generan UN solo fetch por TTL. Ver ``server/services/cache.py``.

Patrón:
1. ``main.create_app()`` arranca el ``lifespan`` y guarda los recursos en
   ``app.state.http_client``, ``app.state.cache_current``, etc.
2. Los endpoints los reciben vía ``Depends(get_http_client)`` /
   ``Depends(get_current_cache)`` / ``Depends(get_series_cache)``.
3. Al apagar el server, el ``lifespan`` cierra el cliente limpiamente.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request

from server.config import get_settings
from server.services.cache import AsyncTTLCache


# Límites conservadores para empezar. Se ajustan cuando haya métricas
# reales de carga. ``max_connections`` es el techo global del pool;
# ``max_keepalive_connections`` cuántas se mantienen idle entre requests.
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)

# Timeout por defecto generoso (los proveedores meteo a veces tardan).
# Cada llamada puede sobrescribirlo si necesita más/menos.
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=20.0,
    write=10.0,
    pool=2.0,
)


@asynccontextmanager
async def http_client_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Lifespan que crea el ``AsyncClient`` + cachés al arrancar y libera
    al parar.

    Se monta en ``server.main.create_app`` con
    ``FastAPI(lifespan=http_client_lifespan)``.

    El nombre se conserva por compatibilidad histórica; en realidad
    inicializa todos los recursos compartidos (cliente HTTP + cachés).
    """
    settings = get_settings()
    client = httpx.AsyncClient(
        limits=_DEFAULT_LIMITS,
        timeout=_DEFAULT_TIMEOUT,
        # Pasar un User-Agent honesto ayuda a algunos proveedores a
        # distinguirnos de scrapers anónimos.
        headers={"User-Agent": "MeteoLabX/0.1 (+https://meteolabx.com)"},
    )
    app.state.http_client = client

    # Un caché por endpoint: TTLs distintos por tipo de dato.
    app.state.cache_current = AsyncTTLCache[dict](
        default_ttl_s=settings.cache_ttl_current_s,
        max_entries=settings.cache_max_entries,
    )
    app.state.cache_series = AsyncTTLCache[dict](
        default_ttl_s=settings.cache_ttl_series_s,
        max_entries=settings.cache_max_entries,
    )

    try:
        yield
    finally:
        await client.aclose()


def get_http_client(request: Request) -> httpx.AsyncClient:
    """
    Dependency para endpoints que necesitan hacer HTTP a proveedores.

    Uso::

        @router.post("/current")
        async def current(
            req: CurrentObservationRequest,
            http: httpx.AsyncClient = Depends(get_http_client),
        ):
            data = await wu.fetch_current(req.station_id, req.api_key, client=http)
    """
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        # No debería pasar si el lifespan se montó correctamente. Si
        # ocurre, es un bug de configuración del backend, no del cliente.
        raise RuntimeError(
            "http_client no inicializado. "
            "¿Falta montar http_client_lifespan en create_app()?"
        )
    return client


def get_current_cache(request: Request) -> "AsyncTTLCache[dict]":
    """Dependency: caché de observaciones current (TTL ~30s)."""
    cache = getattr(request.app.state, "cache_current", None)
    if cache is None:
        raise RuntimeError(
            "cache_current no inicializado. ¿Falta el lifespan en create_app()?"
        )
    return cache


def get_series_cache(request: Request) -> "AsyncTTLCache[dict]":
    """Dependency: caché de series del día (TTL ~5min)."""
    cache = getattr(request.app.state, "cache_series", None)
    if cache is None:
        raise RuntimeError(
            "cache_series no inicializado. ¿Falta el lifespan en create_app()?"
        )
    return cache

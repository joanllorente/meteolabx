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

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request

from server.config import get_settings
from server.services.cache import AsyncTTLCache

logger = logging.getLogger(__name__)


# Límites conservadores para empezar. Se ajustan cuando haya métricas
# reales de carga. ``max_connections`` es el techo global del pool;
# ``max_keepalive_connections`` cuántas se mantienen idle entre requests.
# El ranking corre todos los proveedores EN PARALELO y el más pesado es IEM
# (Semaphore 24); junto a MeteoHub (8), Frost (6), el resto y las peticiones de
# usuario se superan fácil las 40 → ``PoolTimeout``. El pool debe cubrir la suma
# de concurrencias internas + margen de usuario.
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=80,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)

# Timeout por defecto generoso (los proveedores meteo a veces tardan).
# Cada llamada puede sobrescribirlo si necesita más/menos. ``pool`` (espera por
# una conexión libre) holgado para no reventar en los picos del ranking.
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=20.0,
    write=10.0,
    pool=10.0,
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

    # Euskalmet en plataformas sin FS persistente (Railway): si la clave
    # privada llega como PEM por env (``METEOLABX_EUSKALMET_PRIVATE_KEY_PEM``)
    # y la ruta configurada no apunta a un fichero existente, la materializamos
    # a disco y apuntamos ahí, para que la autogeneración del JWT (firma con
    # ``openssl``) funcione sin exponer la clave en el repo.
    pem = getattr(settings, "euskalmet_private_key_pem", "")
    key_path = str(getattr(settings, "euskalmet_private_key_path", "") or "").strip()
    if pem and not (key_path and os.path.exists(key_path)):
        from server.services import euskalmet

        materialized = euskalmet.materialize_private_key(pem)
        if materialized:
            settings.euskalmet_private_key_path = materialized
            logger.info("Euskalmet: clave privada materializada desde env en %s", materialized)

    # Silencia el log INFO por-petición de httpx/httpcore (una línea por cada
    # GET) — con el ranking (MeteoHub 25 + Frost 4 + Meteo-France ~15 + …) era
    # una "biblia" que tapaba lo importante. Los errores (WARNING+) sí salen.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Transport con reintentos a nivel de CONEXIÓN: httpx reintenta los
    # ``ConnectError`` (handshake/conexión fallida) hasta N veces antes de
    # propagar. Mitiga la conectividad intermitente (p.ej. fallback IPv6 que
    # tumba el TLS) hacia las APIs de proveedores. ``limits`` van en el
    # transport cuando se pasa uno propio.
    transport = httpx.AsyncHTTPTransport(limits=_DEFAULT_LIMITS, retries=2)
    client = httpx.AsyncClient(
        transport=transport,
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

    # Ranking diario: store en memoria + job horario que rellena los
    # agregados por proveedor. Con un snapshot en disco (Railway Volume o
    # ``METEOLABX_RANKING_STATE_PATH``) el estado sobrevive a reinicios y
    # redeploys: días anteriores del selector y horas acumuladas de
    # AEMET/Meteo-France incluidos. Sin ruta configurada, comportamiento
    # histórico (memoria pura).
    from server.services.ranking import RankingStore, refresh_loop

    ranking_state_path = str(getattr(settings, "ranking_state_path", "") or "").strip()
    if not ranking_state_path:
        volume_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        if volume_dir:
            ranking_state_path = os.path.join(volume_dir, "ranking_state.json.gz")

    app.state.ranking_store = RankingStore()
    if ranking_state_path:
        app.state.ranking_store.load_from_disk(ranking_state_path)
    ranking_task = asyncio.create_task(
        refresh_loop(
            app.state.ranking_store,
            client=client,
            settings=settings,
            interval_s=float(getattr(settings, "ranking_refresh_interval_s", 1800.0)),
            retry_interval_s=float(getattr(settings, "ranking_retry_interval_s", 60.0)),
            state_path=ranking_state_path,
        )
    )

    try:
        yield
    finally:
        ranking_task.cancel()
        try:
            await ranking_task
        except asyncio.CancelledError:
            pass
        # Último volcado antes de apagar: captura lo acumulado desde el
        # último ciclo (p. ej. horas de Meteo-France de los reintentos).
        if ranking_state_path:
            try:
                app.state.ranking_store.save_to_disk(ranking_state_path)
            except Exception:
                logger.warning(
                    "ranking: no se pudo guardar el snapshot final en %s",
                    ranking_state_path, exc_info=True,
                )
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

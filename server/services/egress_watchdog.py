"""
Vigilante de la salida a internet del backend.

El 16/09/2026 el proceso de uvicorn dejó de abrir conexiones hacia fuera tras
una avalancha de rastreadores y siguió así durante horas: ``/v1/health``
contestaba, así que nadie lo reiniciaba, mientras el worker de predicción del
mismo contenedor descargaba sin problema. La red estaba bien; lo atascado era
el propio proceso.

Cada minuto se pide una URL ligera con el cliente HTTP compartido. Tras varias
sondas fallidas seguidas se repite con una conexión independiente —otro hilo,
sin event loop ni pool—. Si esa sí llega, el atasco es del proceso: se termina
y ``scripts/start_web.sh`` sale, con lo que Railway reinicia el servicio. Si
tampoco llega, es un corte de red y reiniciar no arreglaría nada.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 10.0
INDEPENDENT_PROBE_DEADLINE_S = 30.0
FORCED_EXIT_AFTER_S = 60.0

# Hilo propio: el ejecutor por defecto de asyncio es justo uno de los
# sospechosos de atasco (resuelve los DNS de todo el cliente compartido).
_independent_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="egress-probe")


async def probe_shared(client: httpx.AsyncClient, url: str) -> bool:
    try:
        response = await client.get(url, timeout=httpx.Timeout(PROBE_TIMEOUT_S))
    except httpx.HTTPError:
        return False
    # Cualquier respuesta, aunque sea un 4xx, demuestra que la conexión salió.
    return response.status_code < 500


def probe_independent(url: str) -> bool:
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_S) as client:
            return client.get(url).status_code < 500
    except httpx.HTTPError:
        return False


def pool_state(client: httpx.AsyncClient) -> dict:
    """Foto del pool y del ejecutor por defecto para diagnosticar el atasco.

    Lee atributos privados de httpx/httpcore y asyncio: si cambian, se
    devuelve lo que se pueda en vez de fallar.
    """
    state: dict = {}
    try:
        pool = client._transport._pool  # type: ignore[attr-defined]
        connections = list(pool.connections)
        state["connections"] = len(connections)
        state["idle"] = sum(1 for connection in connections if connection.is_idle())
        state["queued"] = len(pool._requests)
    except Exception:
        pass
    try:
        executor = asyncio.get_running_loop()._default_executor  # type: ignore[attr-defined]
        if executor is not None:
            state["default_executor_threads"] = len(executor._threads)
            state["default_executor_queue"] = executor._work_queue.qsize()
    except Exception:
        pass
    return state


def _avisar(*, restarting: bool, pool: dict) -> None:
    """Manda el correo sin dejar que un fallo suyo estorbe al reinicio."""
    try:
        from server.services.alerts import send
        from server.services.forecast_store import get_forecast_store
        from server.services.health_alerts import egress_alert

        send(egress_alert(restarting=restarting, pool=pool), store=get_forecast_store())
    except Exception:
        logger.warning("No se pudo avisar del atasco de salida", exc_info=True)


def restart_process() -> None:
    """SIGTERM para un cierre ordenado; si se cuelga, salida forzada."""
    def forced_exit() -> None:
        time.sleep(FORCED_EXIT_AFTER_S)
        os._exit(1)

    threading.Thread(target=forced_exit, name="egress-forced-exit", daemon=True).start()
    os.kill(os.getpid(), signal.SIGTERM)


class EgressWatchdog:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        failures_before_check: int = 5,
        restart: Callable[[], None] = restart_process,
    ) -> None:
        self._client = client
        self._url = url
        self._failures_before_check = max(1, int(failures_before_check))
        self._restart = restart
        self.failures = 0

    async def tick(self) -> bool:
        """Una sonda. Devuelve ``True`` si ha mandado reiniciar el proceso."""
        if await probe_shared(self._client, self._url):
            if self.failures:
                logger.info("Salida a internet recuperada tras %d sondas fallidas", self.failures)
            self.failures = 0
            return False

        self.failures += 1
        logger.warning(
            "Sonda de salida a internet fallida (%d/%d) · %s",
            self.failures, self._failures_before_check, pool_state(self._client),
        )
        if self.failures < self._failures_before_check:
            return False

        loop = asyncio.get_running_loop()
        try:
            independent_ok = await asyncio.wait_for(
                loop.run_in_executor(_independent_executor, probe_independent, self._url),
                INDEPENDENT_PROBE_DEADLINE_S,
            )
        except TimeoutError:
            independent_ok = False
        if not independent_ok:
            logger.warning(
                "Tampoco sale una conexión independiente: parece un corte de red; no se reinicia"
            )
            _avisar(restarting=False, pool=pool_state(self._client))
            return False

        logger.error(
            "El cliente HTTP compartido no sale a internet y una conexión independiente sí: "
            "proceso atascado, se reinicia · %s",
            pool_state(self._client),
        )
        # Antes de reiniciar: el reinicio borra el log del proceso y, sin
        # aviso, este fallo solo se descubre viendo el mapa vacío.
        _avisar(restarting=True, pool=pool_state(self._client))
        self._restart()
        return True


async def watchdog_loop(watchdog: EgressWatchdog, *, interval_s: float = 60.0) -> None:
    """Se cancela con el lifespan; termina tras mandar reiniciar."""
    while True:
        await asyncio.sleep(max(10.0, interval_s))
        try:
            if await watchdog.tick():
                return
        except Exception:
            logger.exception("Falló el vigilante de salida a internet; se reintentará")

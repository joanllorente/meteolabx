"""
Tope de consultas en vivo atendidas a la vez.

Cada ficha de observación, tendencia o histórico acaba en una o varias
llamadas a un proveedor, y todas salen por el mismo pool de httpx que usa el
ranking. El 16/09/2026 una avalancha de rastreadores llenó ese pool: durante
horas ninguna consulta llegó a salir, ni siquiera cuando la avalancha ya había
pasado. Con este tope, lo que no cabe espera unos segundos y, si sigue sin
hueco, recibe un 503 inmediato en vez de sumarse a la cola.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

LIVE_PATHS = ("/observations/", "/climo/summary")

_BUSY_BODY = json.dumps({
    "ok": False,
    "error_code": "backend_busy",
    "provider": None,
    "detail": "Demasiadas consultas en vivo a la vez; vuelve a intentarlo en unos segundos.",
}).encode()


class LiveRequestLimiter:
    """Middleware ASGI con un semáforo para las rutas de datos en vivo."""

    def __init__(self, app, *, api_prefix: str, max_concurrent: int, queue_timeout_s: float) -> None:
        self.app = app
        self._prefixes = tuple(f"{api_prefix}{path}" for path in LIVE_PATHS)
        self._max_concurrent = max(1, int(max_concurrent))
        self._queue_timeout_s = max(0.0, float(queue_timeout_s))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._rejected = 0
        self._last_log = 0.0

    def _semaphore_for_running_loop(self) -> asyncio.Semaphore:
        # Un semáforo queda ligado al loop en que se usa por primera vez; los
        # TestClient levantan uno nuevo cada vez.
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._semaphore is None:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self._prefixes):
            await self.app(scope, receive, send)
            return

        semaphore = self._semaphore_for_running_loop()
        try:
            async with asyncio.timeout(self._queue_timeout_s):
                await semaphore.acquire()
        except TimeoutError:
            self._reject()
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"30"),
                ],
            })
            await send({"type": "http.response.body", "body": _BUSY_BODY})
            return

        try:
            await self.app(scope, receive, send)
        finally:
            semaphore.release()

    def _reject(self) -> None:
        # En plena avalancha serían cientos de líneas por minuto: basta una
        # por minuto con la cuenta.
        self._rejected += 1
        now = time.monotonic()
        if now - self._last_log >= 60.0:
            logger.warning(
                "Consultas en vivo saturadas (%d a la vez): %d rechazadas con 503 desde el último aviso",
                self._max_concurrent, self._rejected,
            )
            self._rejected = 0
            self._last_log = now

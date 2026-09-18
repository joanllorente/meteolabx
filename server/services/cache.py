"""
Caché en memoria async-safe con TTL y request coalescing.

Diseñado para reducir las llamadas a proveedores meteorológicos (WU,
AEMET, etc.) cuando múltiples clientes piden la misma estación dentro
de la ventana de refresco. Vive en el proceso del backend FastAPI; al
reiniciarse el contenedor el caché se vacía (aceptable porque la primera
petición tras el restart paga el coste y las siguientes son hits).

## Propiedades

1. **TTL por entrada**: cada valor cacheado tiene tiempo de vida; tras
   expirar se considera miss y se vuelve a fetchear.

2. **Request coalescing**: si N corutinas piden la MISMA key a la vez
   con caché frío, solo una hace el fetch real; las otras N-1 esperan
   el ``Future`` del leader. Crítico contra "thundering herd".

3. **Stale-if-error opcional**: una entrada expirada puede seguir sirviéndose
   durante una ventana de gracia si el proveedor falla al refrescarla. Sin
   esa opción, las excepciones se propagan como antes.

4. **LRU eviction**: cuando se supera ``max_entries``, se descarta la
   entrada menos recientemente usada (move_to_end al hit + popitem al
   final cuando hace falta).

5. **Async-safe**: un único ``asyncio.Lock`` protege ``_store`` e
   ``_in_flight`` durante las transiciones; el fetch real se ejecuta
   FUERA del lock para no bloquear lookups concurrentes.

## Limitaciones intencionales

- **No persiste entre reinicios.** Para eso → Redis (futuro F2+).
- **No comparte entre réplicas del backend.** Si Railway escala a N
  instancias, cada una tiene su propio caché. Idem: Redis.
- **No event-driven.** Es por TTL, no invalida cuando el proveedor
  publica datos nuevos antes del TTL.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
import weakref
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

T = TypeVar("T")

LIVE_CACHES = weakref.WeakSet()

# Fallos que dicen «ahora no» y no «esto no existe»: un 429 o un timeout
# volverán a salir si se repite la llamada en el acto. Recordarlos un rato
# evita que una ráfaga de visitas a la misma estación martillee al proveedor
# que ya nos está frenando, como hizo Météo-France el 16/09/2026.
TRANSIENT_ERROR_CODES = frozenset({
    "provider_ratelimit",
    "provider_timeout",
    "provider_network_error",
    "provider_network",
    "provider_http_error",
    "provider_unavailable",
})


def make_cache_key(provider: str, kind: str, station_id: str, api_key: str) -> str:
    """
    Compone una clave de caché estable a partir de identificación de
    proveedor + endpoint + estación + API key (hasheada).

    Hashear la API key garantiza que un dump de memoria del proceso no
    expone credenciales. Misma estación + misma key → misma clave (los
    hits son por usuario, no globales).
    """
    api_hash = hashlib.sha1(api_key.encode("utf-8") or b"").hexdigest()[:12]
    provider_key = provider.lower()
    station_key = station_id if provider_key == "windy" else station_id.upper()
    return f"{provider_key}:{kind}:{station_key}:{api_hash}"


class AsyncTTLCache(Generic[T]):
    """
    Caché TTL async-safe con request coalescing y LRU eviction.

    Uso típico desde un endpoint::

        cache = AsyncTTLCache[dict](default_ttl_s=30, max_entries=500)
        key = make_cache_key("WU", "current", station_id, api_key)
        value = await cache.get_or_fetch(
            key,
            lambda: wu.fetch_current(station_id, api_key, client=http),
        )

    El ``fetcher`` es un callable que devuelve una ``Awaitable``. Se
    invoca solo cuando hace falta (miss o expirado) y solo UNA vez por
    grupo de llamadas concurrentes con la misma key.
    """

    def __init__(
        self,
        *,
        default_ttl_s: float,
        max_entries: int = 500,
        stale_if_error_s: float = 0.0,
        error_ttl_s: float = 0.0,
    ) -> None:
        if default_ttl_s <= 0:
            raise ValueError("default_ttl_s must be > 0")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        if stale_if_error_s < 0:
            raise ValueError("stale_if_error_s must be >= 0")
        if error_ttl_s < 0:
            raise ValueError("error_ttl_s must be >= 0")
        self._default_ttl_s = float(default_ttl_s)
        self._max_entries = int(max_entries)
        self._stale_if_error_s = float(stale_if_error_s)
        self._error_ttl_s = float(error_ttl_s)
        # (fresh_until, stale_until, value). Separar ambos límites impide que
        # varios fallos consecutivos prolonguen indefinidamente un dato viejo.
        self._store: "OrderedDict[str, tuple[float, float, T]]" = OrderedDict()
        # Future por key para corutinas que esperan el mismo fetch.
        self._in_flight: dict[str, "asyncio.Future[T]"] = {}
        # (until, excepción) del último fallo transitorio por key.
        self._errors: "OrderedDict[str, tuple[float, BaseException]]" = OrderedDict()
        self._lock = asyncio.Lock()
        # Contadores ligeros para diagnóstico/observabilidad.
        self._hits = 0
        self._misses = 0
        self._coalesced = 0
        self._stale_hits = 0
        self._error_hits = 0
        LIVE_CACHES.add(self)

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[T]],
        *,
        ttl_s: Optional[float] = None,
    ) -> T:
        """
        Devuelve el valor cacheado o, si está expirado/ausente, llama a
        ``fetcher`` y cachea el resultado.

        El ``ttl_s`` puede sobreescribir el default por llamada (útil
        cuando un mismo caché sirve a varios endpoints con TTLs
        distintos, aunque normalmente conviene un caché por endpoint).
        """
        ttl = float(ttl_s) if ttl_s is not None else self._default_ttl_s
        now = time.time()
        stale_value: Optional[T] = None
        stale_available = False

        # ----- Fase 1: bajo lock, decidir si somos leader o follower -----
        async with self._lock:
            cached = self._store.get(key)
            if cached is not None:
                fresh_until, stale_until, value = cached
                if fresh_until > now:
                    self._store.move_to_end(key)
                    self._hits += 1
                    return value
                if stale_until > now:
                    stale_value = value
                    stale_available = True
                else:
                    del self._store[key]

            remembered = self._errors.get(key)
            if remembered is not None:
                until, error = remembered
                if until > now:
                    # El proveedor acaba de fallar con esta key: ni se le
                    # vuelve a llamar ni se espera. Con respaldo, el respaldo.
                    self._error_hits += 1
                    if stale_available:
                        self._stale_hits += 1
                        return stale_value  # type: ignore[return-value]
                    raise _replay(error)
                del self._errors[key]

            existing_future = self._in_flight.get(key)
            if existing_future is not None:
                # Hay otra corutina ya pidiendo este key; esperamos su resultado.
                self._coalesced += 1
                future_to_await: "asyncio.Future[T]" = existing_future
                is_leader = False
            else:
                # Somos el leader. Creamos un future y soltamos el lock para fetchear.
                self._misses += 1
                future_to_await = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future_to_await
                is_leader = True

        # ----- Fase 2: followers esperan; leader fetchea fuera del lock -----
        if not is_leader:
            return await future_to_await

        # Métricas: las keys empiezan por "{provider}:"; un miss que
        # fetchea es una llamada real al upstream. Import diferido para
        # no acoplar el módulo de caché en imports tempranos.
        from server.services import metrics

        metrics.record_call(key.split(":", 1)[0])
        try:
            result = await fetcher()
        except BaseException as exc:
            # Un fallo transitorio no debe dejar sin datos a todos los usuarios
            # si hace unos minutos obtuvimos una observación válida. Las
            # cancelaciones quedan fuera: deben seguir propagándose.
            if stale_available and isinstance(exc, Exception):
                async with self._lock:
                    self._in_flight.pop(key, None)
                    self._remember_error(key, exc)
                    self._store.move_to_end(key)
                    self._stale_hits += 1
                if not future_to_await.done():
                    future_to_await.set_result(stale_value)  # type: ignore[arg-type]
                return stale_value  # type: ignore[return-value]

            # Sin valor de respaldo: no cacheamos el error. Notificamos a
            # followers y propagamos la excepción.
            async with self._lock:
                self._in_flight.pop(key, None)
                self._remember_error(key, exc)
            if not future_to_await.done():
                future_to_await.set_exception(exc)
            # "Marcar como retrieved" para que asyncio no chille en GC si
            # no había followers esperando. Si hay followers, ``await
            # future_to_await`` igualmente re-eleva la excepción.
            future_to_await.exception()
            raise

        metrics.record_success(key.split(":", 1)[0])

        # ----- Fase 3: cachear, notificar followers, evict si toca -----
        async with self._lock:
            stored_at = time.time()
            fresh_until = stored_at + ttl
            self._store[key] = (
                fresh_until,
                fresh_until + self._stale_if_error_s,
                result,
            )
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)
            self._in_flight.pop(key, None)
            self._errors.pop(key, None)

        if not future_to_await.done():
            future_to_await.set_result(result)
        return result

    def _remember_error(self, key: str, exc: BaseException) -> None:
        """Guarda un fallo transitorio durante ``error_ttl_s``. Llamar bajo lock."""
        if self._error_ttl_s <= 0 or getattr(exc, "error_code", None) not in TRANSIENT_ERROR_CODES:
            return
        self._errors[key] = (time.time() + self._error_ttl_s, exc)
        self._errors.move_to_end(key)
        while len(self._errors) > self._max_entries:
            self._errors.popitem(last=False)

    async def purge_expired(self) -> int:
        """Retira solo datos fuera del plazo de respaldo y sin refresh activo."""
        async with self._lock:
            now = time.time()
            expired = [key for key, (_, stale_until, _) in self._store.items()
                       if stale_until <= now and key not in self._in_flight]
            for key in expired:
                del self._store[key]
            expired_errors = [key for key, (until, _) in self._errors.items() if until <= now]
            for key in expired_errors:
                del self._errors[key]
            return len(expired) + len(expired_errors)

    def invalidate(self, key: str) -> bool:
        """
        Elimina una entrada del caché. Devuelve ``True`` si existía.

        Útil para tests y para forzar refresco programático tras un
        cambio conocido (p. ej. el usuario reconectó con nueva API key).
        Esto NO afecta a peticiones in-flight; al terminar se cachearán
        con el TTL normal.
        """
        had_error = self._errors.pop(key, None) is not None
        return self._store.pop(key, None) is not None or had_error

    def clear(self) -> None:
        """Vacía el caché entero. Para tests y debugging."""
        self._store.clear()
        self._errors.clear()
        # No tocamos _in_flight: las corutinas en vuelo deben terminar
        # normalmente; sus resultados sí se cachearán (en el caché vacío).

    def stats(self) -> dict[str, Any]:
        """Métricas básicas para health checks / logs."""
        return {
            "entries": len(self._store),
            "in_flight": len(self._in_flight),
            "hits": self._hits,
            "misses": self._misses,
            "coalesced": self._coalesced,
            "stale_hits": self._stale_hits,
            "error_entries": len(self._errors),
            "error_hits": self._error_hits,
            "error_ttl_s": self._error_ttl_s,
            "max_entries": self._max_entries,
            "default_ttl_s": self._default_ttl_s,
            "stale_if_error_s": self._stale_if_error_s,
        }


def _replay(error: BaseException) -> BaseException:
    """Copia del fallo recordado: cada petición lanza la suya, sin compartir traceback."""
    try:
        return copy.copy(error).with_traceback(None)
    except Exception:
        return error

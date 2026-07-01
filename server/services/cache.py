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

3. **No-cache on error**: las excepciones se propagan al leader y a los
   followers pero NO se cachean — al siguiente intento se reintenta.

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
import hashlib
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


def make_cache_key(provider: str, kind: str, station_id: str, api_key: str) -> str:
    """
    Compone una clave de caché estable a partir de identificación de
    proveedor + endpoint + estación + API key (hasheada).

    Hashear la API key garantiza que un dump de memoria del proceso no
    expone credenciales. Misma estación + misma key → misma clave (los
    hits son por usuario, no globales).
    """
    api_hash = hashlib.sha1(api_key.encode("utf-8") or b"").hexdigest()[:12]
    return f"{provider.lower()}:{kind}:{station_id.upper()}:{api_hash}"


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
    ) -> None:
        if default_ttl_s <= 0:
            raise ValueError("default_ttl_s must be > 0")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._default_ttl_s = float(default_ttl_s)
        self._max_entries = int(max_entries)
        self._store: "OrderedDict[str, tuple[float, T]]" = OrderedDict()
        # Future por key para corutinas que esperan el mismo fetch.
        self._in_flight: dict[str, "asyncio.Future[T]"] = {}
        self._lock = asyncio.Lock()
        # Contadores ligeros para diagnóstico/observabilidad.
        self._hits = 0
        self._misses = 0
        self._coalesced = 0

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

        # ----- Fase 1: bajo lock, decidir si somos leader o follower -----
        async with self._lock:
            cached = self._store.get(key)
            if cached is not None:
                expires_at, value = cached
                if expires_at > now:
                    self._store.move_to_end(key)
                    self._hits += 1
                    return value
                # Expirado: limpiamos para que el resto del flujo no se confunda.
                del self._store[key]

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
            # No cacheamos errores. Notificamos a followers y reraisemos.
            async with self._lock:
                self._in_flight.pop(key, None)
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
            self._store[key] = (time.time() + ttl, result)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)
            self._in_flight.pop(key, None)

        if not future_to_await.done():
            future_to_await.set_result(result)
        return result

    def invalidate(self, key: str) -> bool:
        """
        Elimina una entrada del caché. Devuelve ``True`` si existía.

        Útil para tests y para forzar refresco programático tras un
        cambio conocido (p. ej. el usuario reconectó con nueva API key).
        Esto NO afecta a peticiones in-flight; al terminar se cachearán
        con el TTL normal.
        """
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Vacía el caché entero. Para tests y debugging."""
        self._store.clear()
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
            "max_entries": self._max_entries,
            "default_ttl_s": self._default_ttl_s,
        }

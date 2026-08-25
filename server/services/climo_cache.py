"""Caché granular para descargas históricas solapadas.

El caché del router protege una consulta completa. Este módulo protege los
bloques que la componen (día, mes, variable o intervalo), de modo que ampliar
una selección reutiliza lo descargado anteriormente.
"""

from __future__ import annotations

from datetime import date
from itertools import count
from typing import Any, Awaitable, Callable, Optional

from server.services.cache import AsyncTTLCache, make_cache_key


_CLIMO_BLOCK_CACHE = AsyncTTLCache[Any](
    default_ttl_s=60 * 60,
    max_entries=20_000,
)
_CLIENT_SCOPE_COUNTER = count(1)


class _NoCacheValue(Exception):
    """Resultado tolerable para el caller, pero no válido para persistir."""


def _client_scope(client: Any) -> str:
    scope = getattr(client, "_meteolabx_climo_cache_scope", None)
    if scope is None:
        scope = str(next(_CLIENT_SCOPE_COUNTER))
        try:
            setattr(client, "_meteolabx_climo_cache_scope", scope)
        except Exception:
            scope = f"fallback-{id(client)}"
    return str(scope)


def historical_ttl_s(end_date: Optional[date]) -> float:
    """Periodos cerrados cambian raramente; el periodo actual se refresca."""
    if end_date is not None and end_date < date.today():
        return 30 * 24 * 60 * 60
    return 60 * 60


async def get_or_fetch_climo_block(
    *,
    provider: str,
    kind: str,
    station_id: str,
    credential: str,
    client: Any,
    fetcher: Callable[[], Awaitable[Any]],
    end_date: Optional[date] = None,
    ttl_s: Optional[float] = None,
) -> Any:
    # El cliente forma parte del scope para aislar apps/tests diferentes. En
    # producción FastAPI reutiliza un único AsyncClient durante todo el proceso.
    scoped_kind = f"climo-block:{kind}:client-{_client_scope(client)}"
    key = make_cache_key(provider, scoped_kind, station_id, credential or "public")
    async def _fetch_non_null() -> Any:
        result = await fetcher()
        if result is None:
            # Varios adaptadores best-effort representan timeouts/5xx como
            # None. No convertir un fallo transitorio en 30 días sin datos.
            raise _NoCacheValue()
        return result

    try:
        return await _CLIMO_BLOCK_CACHE.get_or_fetch(
            key,
            _fetch_non_null,
            ttl_s=ttl_s if ttl_s is not None else historical_ttl_s(end_date),
        )
    except _NoCacheValue:
        return None


def clear_climo_block_cache() -> None:
    _CLIMO_BLOCK_CACHE.clear()

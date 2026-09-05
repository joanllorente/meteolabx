"""
Cada cuánto caduca la observación actual en el caché.

Weather Underground publica con cada envío de la consola —diez o quince
segundos—, así que treinta de caché se comían la frescura por la que se
pregunta: el navegador pedía cada medio minuto y podía recibir un dato de
hace otro medio. Las redes de credencial propia llevan su propio plazo.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

import pytest

from server.routers.observations import CURRENT_TTL_BY_PROVIDER
from server.services.cache import AsyncTTLCache

from .conftest import WU_OK_OBSERVATION


class _SpyCache(AsyncTTLCache):
    """Caché real que apunta con qué TTL se le pide cada entrada."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ttls: list[Optional[float]] = []

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
        *,
        ttl_s: Optional[float] = None,
    ) -> Any:
        self.ttls.append(ttl_s)
        return await super().get_or_fetch(key, fetcher, ttl_s=ttl_s)


def _spy_on_current_cache(client) -> _SpyCache:
    app = client.app
    spy = _SpyCache(default_ttl_s=30.0, max_entries=64)
    app.state.cache_current = spy
    return spy


def test_wu_current_uses_its_own_shorter_ttl(app_factory) -> None:
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        spy = _spy_on_current_cache(client)
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "ITEST123", "api_key": "fake"},
        )

    assert response.status_code == 200
    assert spy.ttls == [10.0]


def test_public_networks_keep_the_default_ttl(app_factory) -> None:
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        spy = _spy_on_current_cache(client)
        client.post(
            "/v1/observations/current",
            json={"provider": "NWS", "station_id": "KJRB"},
        )

    # ``None`` es «el del caché»: 30 s. Solo las redes de credencial propia
    # traen plazo propio.
    assert spy.ttls == [None]


@pytest.mark.parametrize("provider", ["WU", "WEATHERLINK"])
def test_personal_networks_are_the_only_ones_with_an_override(provider: str) -> None:
    assert provider in CURRENT_TTL_BY_PROVIDER
    assert CURRENT_TTL_BY_PROVIDER[provider] <= 30.0
    assert set(CURRENT_TTL_BY_PROVIDER) == {"WU", "WEATHERLINK"}

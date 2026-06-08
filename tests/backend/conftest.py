"""
Fixtures comunes para los tests del backend FastAPI.

Estos tests no necesitan Streamlit ni la sesión de la app Streamlit.
``tests/conftest.py`` (raíz) sigue existiendo para los tests Streamlit
legacy; este conftest solo aporta lo del backend.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app


# Respuesta WU "OK" mínima realista para reutilizar en tests.
WU_OK_OBSERVATION: Dict[str, Any] = {
    "observations": [
        {
            "epoch": 1717255200,
            "humidity": 65,
            "winddir": 180,
            "lat": 41.387,
            "lon": 2.169,
            "elev": 12.0,
            "solarRadiation": 800.0,
            "uv": 6.0,
            "obsTimeLocal": "2026-06-01 12:00:00",
            "obsTimeUtc": "2026-06-01T10:00:00Z",
            "metric": {
                "temp": 22.0,
                "pressure": 1013.0,
                "dewpt": 14.0,
                "heatIndex": 22.0,
                "windSpeed": 8.0,
                "windGust": 12.0,
                "precipRate": 0.0,
                "precipTotal": 0.4,
            },
        }
    ]
}


def make_mock_client(
    *,
    status: int = 200,
    json_body: Optional[Dict[str, Any]] = None,
    raise_exc: Optional[BaseException] = None,
) -> httpx.AsyncClient:
    """
    Crea un ``httpx.AsyncClient`` con ``MockTransport`` que devuelve
    siempre la misma respuesta (o lanza la misma excepción de red).

    Útil para tests del servicio WU y para overrides de la dependency
    ``get_http_client`` en tests del endpoint.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, json=json_body or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.fixture
def app_factory() -> Callable[..., Any]:
    """
    Devuelve una factoría que crea una app FastAPI fresca con un cliente
    HTTP mockeado. El test controla qué responde "WU" pasando
    ``status``, ``json_body`` o ``raise_exc``.

    Uso::

        def test_x(app_factory):
            with app_factory(status=401) as client:
                response = client.post("/v1/observations/current", ...)
                assert response.status_code == 401
    """

    def _factory(**mock_kwargs: Any):
        mock_client = make_mock_client(**mock_kwargs)
        app = create_app()
        app.dependency_overrides[get_http_client] = lambda: mock_client
        # TestClient gestiona el lifespan de la app; el AsyncClient mock
        # queda atado fuera y se cierra al final del with.
        return _AppContext(app, mock_client)

    return _factory


class _AppContext:
    """Context manager que sale ``TestClient`` y cierra el mock client."""

    def __init__(self, app: Any, mock_client: httpx.AsyncClient) -> None:
        self._app = app
        self._mock_client = mock_client
        self._test_client: Optional[TestClient] = None

    def __enter__(self) -> TestClient:
        self._test_client = TestClient(self._app)
        self._test_client.__enter__()
        return self._test_client

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._test_client is not None:
                self._test_client.__exit__(exc_type, exc, tb)
        finally:
            # ``mock_client.aclose()`` es coroutine; en un test sync no
            # necesitamos await porque ``MockTransport`` no abre sockets
            # reales, pero limpiamos referencias.
            self._mock_client = None  # type: ignore[assignment]

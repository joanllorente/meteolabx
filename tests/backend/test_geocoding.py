from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client
from server.main import create_app


def test_geocode_endpoint_normalizes_first_nominatim_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "nominatim.openstreetmap.org"
        assert request.url.params["q"] == "Barcelona"
        assert request.headers["user-agent"].startswith("MeteoLabX/")
        return httpx.Response(200, json=[{
            "lat": "41.3828939",
            "lon": "2.1774322",
            "display_name": "Barcelona, Catalunya, España",
        }])

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: upstream
    app.dependency_overrides[get_settings] = lambda: Settings()
    with TestClient(app) as client:
        response = client.get(
            "/v1/stations/geocode",
            params={"q": "Barcelona", "lang": "es,en"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "found": True,
        "lat": 41.3828939,
        "lon": 2.1774322,
        "display_name": "Barcelona, Catalunya, España",
    }


def test_geocode_endpoint_returns_explicit_not_found() -> None:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: upstream
    app.dependency_overrides[get_settings] = lambda: Settings()
    with TestClient(app) as client:
        response = client.get("/v1/stations/geocode", params={"q": "NoSuchPlace"})

    assert response.status_code == 200
    assert response.json() == {"found": False, "lat": None, "lon": None, "display_name": ""}

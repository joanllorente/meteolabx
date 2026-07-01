"""
Tests del inventario de estaciones (``server/services/stations.py`` +
``/v1/stations/*``). Usa los catálogos reales de ``data/``.
"""

from __future__ import annotations

import pytest
import httpx
from fastapi.testclient import TestClient

from server.dependencies.http import get_http_client
from server.main import create_app
from server.services import stations
from tests.backend.test_weatherlink_service import STATIONS_PAYLOAD


def _client() -> TestClient:
    return TestClient(create_app())


# =====================================================================
# Servicio
# =====================================================================

def test_get_station_normalizes_per_provider() -> None:
    record = stations.get_station("METEOGALICIA", "10045")
    assert record is not None
    assert record["name"] == "Mabegondo"
    assert record["elevation"] == pytest.approx(94.0)
    assert record["sensors"]["thermometer"] is True
    assert record["has_historical"] is True

    # Case-insensitive y proveedores con id no numérico
    record = stations.get_station("metoffice", "GBY1KB")
    assert record is not None
    assert record["tz"] == "Europe/Guernsey"
    assert record["has_historical"] is False


def test_get_station_unknown_returns_none() -> None:
    assert stations.get_station("METEOGALICIA", "NOPE") is None
    assert stations.get_station("WU", "IBARCE12345") is None  # sin catálogo


def test_provider_counts_covers_all_catalogs() -> None:
    counts = stations.provider_counts()
    assert set(counts) == set(stations.CATALOG_PROVIDERS)
    assert counts["NWS"] > 30000
    assert counts["METEOCAT"] > 100
    assert counts["IEM"] > 190000


def test_country_counts_and_iem_country_filter() -> None:
    counts = stations.country_counts(providers=["IEM"])
    assert counts["US"] > 100000
    assert counts["ES"] > 0

    nws_counts = stations.country_counts(providers=["NWS"])
    assert nws_counts["US"] > 30000
    assert "UNSPECIFIED" not in nws_counts

    all_counts = stations.country_counts()
    assert all_counts["ES"] > counts["ES"]
    assert all_counts["TR"] == 75
    assert "TU" not in all_counts
    assert all_counts["PR"] == 2
    assert "RQ" not in all_counts

    results = stations.search_near(
        40.4, -3.7, radius_km=500, providers=["IEM"], countries=["ES"], limit=20,
    )
    assert results
    assert all(row["provider"] == "IEM" for row in results)
    assert all(row["country"] == "ES" for row in results)
    assert all(row["connectable"] is True for row in results)

    iem_record = stations.get_station("IEM", "ES__ASOS|LEBL")
    assert iem_record is not None
    assert iem_record["network"] == "ES__ASOS"
    assert iem_record["station_id"] == "LEBL"

    turkey_record = stations.get_station("IEM", "TR__ASOS|LTFG")
    assert turkey_record is not None
    assert turkey_record["country"] == "TR"
    assert turkey_record["has_historical"] is True
    assert turkey_record["is_historical_only"] is False

    berlin_tegel_record = stations.get_station("IEM", "DE__ASOS|EDDT")
    assert berlin_tegel_record is not None
    assert berlin_tegel_record["has_historical"] is True
    assert berlin_tegel_record["is_historical_only"] is True

    puerto_rico_record = stations.get_station("IEM", "RAOB|TJSJ")
    assert puerto_rico_record is not None
    assert puerto_rico_record["country"] == "PR"
    assert puerto_rico_record["has_historical"] is False

    spain_results = stations.search_near(
        40.4, -3.7, radius_km=2000, countries=["ES"], limit=5000,
    )
    providers = {row["provider"] for row in spain_results}
    assert "AEMET" in providers
    assert "IEM" in providers
    assert len(providers) >= 4


def test_search_catalog_filters_historical_availability() -> None:
    all_tr = stations.search_catalog(
        lat=41.0, lon=2.0, providers=["IEM"], countries=["TR"], limit=200,
    )
    historical_tr = stations.search_catalog(
        lat=41.0, lon=2.0, providers=["IEM"], countries=["TR"],
        has_historical=True, limit=200,
    )
    assert historical_tr
    assert len(historical_tr) <= len(all_tr)
    assert all(row["has_historical"] is True for row in historical_tr)
    assert {row["network"] for row in historical_tr} == {"TR__ASOS"}

    puerto_rico = stations.search_catalog(
        providers=["IEM"], countries=["PR"], has_historical=True, limit=20,
    )
    assert puerto_rico == []


def test_search_catalog_can_hide_historical_only_iem_stations() -> None:
    with_archived = stations.search_catalog(
        providers=["IEM"], countries=["DE"], has_historical=True, limit=10000,
    )
    without_archived = stations.search_catalog(
        providers=["IEM"], countries=["DE"], has_historical=True,
        hide_historical_only=True, limit=10000,
    )

    assert any(row["station_id"] == "EDDT" and row["is_historical_only"] for row in with_archived)
    assert all(row["station_id"] != "EDDT" for row in without_archived)


def test_search_near_orders_by_distance_and_filters_sensors() -> None:
    # Punto cerca de Mabegondo (A Coruña)
    results = stations.search_near(
        43.24, -8.26, radius_km=30, providers=["METEOGALICIA"],
    )
    assert results
    assert results[0]["station_id"] == "10045"
    distances = [row["distance_km"] for row in results]
    assert distances == sorted(distances)

    # Filtro de sensores: pedir piranómetro reduce el conjunto
    with_solar = stations.search_near(
        43.24, -8.26, radius_km=30, providers=["METEOGALICIA"],
        sensors=["pyranometer"],
    )
    assert len(with_solar) <= len(results)
    assert all(row["sensors"]["pyranometer"] for row in with_solar)


# =====================================================================
# Endpoints
# =====================================================================

def test_stations_near_endpoint() -> None:
    with _client() as client:
        response = client.get(
            "/v1/stations/near",
            params={
                "lat": 43.24, "lon": -8.26, "radius_km": 30,
                "providers": "METEOGALICIA", "sensors": "thermometer",
                "has_historical": "true",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    first = body["stations"][0]
    assert first["station_id"] == "10045"
    assert first["distance_km"] < 5
    assert first["sensors"]["thermometer"] is True
    assert first["has_historical"] is True


def test_station_detail_endpoint() -> None:
    with _client() as client:
        response = client.get("/v1/stations/METEOGALICIA/10045")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Mabegondo"
    assert body["tz"] == "Europe/Madrid"


def test_station_detail_not_found() -> None:
    with _client() as client:
        response = client.get("/v1/stations/METEOGALICIA/NOPE")
    assert response.status_code == 404
    assert response.json()["error_code"] == "station_not_found"


def test_providers_endpoint() -> None:
    with _client() as client:
        response = client.get("/v1/stations/providers")
    assert response.status_code == 200
    counts = response.json()
    assert counts["METEOGALICIA"] > 100


def test_countries_endpoint_filters_by_provider() -> None:
    with _client() as client:
        response = client.get("/v1/stations/countries", params={"providers": "IEM"})
    assert response.status_code == 200
    counts = response.json()
    assert counts["US"] > 100000
    assert counts["ES"] > 0


def test_stations_catalog_endpoint_filters_country_without_spatial_clipping() -> None:
    with _client() as client:
        response = client.get(
            "/v1/stations/catalog",
            params={"lat": 41.371, "lon": 2.128, "countries": "ES", "limit": 5000},
        )
    assert response.status_code == 200
    body = response.json()
    providers = {row["provider"] for row in body["stations"]}
    assert body["count"] >= 1400
    assert {"AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "POEM", "IEM"} <= providers
    assert all(row["country"] == "ES" for row in body["stations"])
    assert "QBB" not in {row["station_id"] for row in body["stations"]}


def test_stations_catalog_applies_iem_country_corrections() -> None:
    with _client() as client:
        response = client.get(
            "/v1/stations/catalog",
            params={"providers": "IEM", "countries": "FR", "limit": 5000},
        )
    assert response.status_code == 200
    rows = response.json()["stations"]
    qbb = [row for row in rows if row["network"] == "ES__ASOS" and row["station_id"] == "QBB"]
    assert qbb
    assert qbb[0]["country"] == "FR"


def test_stations_catalog_without_country_returns_empty() -> None:
    with _client() as client:
        response = client.get("/v1/stations/catalog", params={"limit": 5000})
    assert response.status_code == 200
    assert response.json() == {"count": 0, "stations": []}


def test_weatherlink_stations_endpoint_keeps_credentials_server_side() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.url.params.get("api-key")
        captured["api_secret"] = request.headers.get("X-Api-Secret")
        return httpx.Response(200, json=STATIONS_PAYLOAD)

    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/stations/weatherlink",
            json={"api_key": "personal-key", "api_secret": "personal-secret"},
        )

    assert response.status_code == 200
    assert captured == {
        "api_key": "personal-key",
        "api_secret": "personal-secret",
    }
    rows = response.json()["stations"]
    assert rows[0]["station_id"] == "123456"
    assert rows[0]["station_name"] == "Mi Davis"

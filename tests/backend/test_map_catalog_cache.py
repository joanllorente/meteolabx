"""
El catálogo del mapa no se vuelve a consultar en cada visita.

Abrir la pestaña costaba ocho décimas de las que cuatro eran el recuento por
países sobre las 230.000 filas del catálogo, y otras dos volver a leer las
mismas estaciones. Es un fichero de solo lectura: la misma vista devuelve lo
mismo hasta que se regenera.
"""

from __future__ import annotations

import server.routers.stations as router
from server.services import stations


def _clear_caches() -> None:
    router._catalog_cache.clear()
    stations._country_counts_cache.clear()


def test_the_same_view_is_served_from_memory(app_factory, monkeypatch) -> None:
    _clear_caches()
    calls = {"catalog": 0, "counts": 0}
    real_catalog = stations.search_catalog
    real_counts = stations.country_counts

    def counting_catalog(**kwargs):
        calls["catalog"] += 1
        return real_catalog(**kwargs)

    def counting_counts(**kwargs):
        calls["counts"] += 1
        return real_counts(**kwargs)

    monkeypatch.setattr(stations, "search_catalog", counting_catalog)
    monkeypatch.setattr(stations, "country_counts", counting_counts)

    with app_factory() as client:
        first = client.get("/v1/stations/map-catalog", params={"countries": "ES"})
        second = client.get("/v1/stations/map-catalog", params={"countries": "ES"})

    assert first.status_code == 200
    assert second.json() == first.json()
    assert calls["catalog"] == 1, "la segunda visita no debería releer el catálogo"

    _clear_caches()


def test_changing_a_filter_is_a_different_view(app_factory, monkeypatch) -> None:
    _clear_caches()
    calls = {"n": 0}
    real_catalog = stations.search_catalog

    def counting_catalog(**kwargs):
        calls["n"] += 1
        return real_catalog(**kwargs)

    monkeypatch.setattr(stations, "search_catalog", counting_catalog)

    with app_factory() as client:
        client.get("/v1/stations/map-catalog", params={"countries": "ES"})
        client.get("/v1/stations/map-catalog", params={"countries": "ES", "hide_amateur": "true"})

    assert calls["n"] == 2, "ocultar particulares es otra vista, no la misma"

    _clear_caches()

"""
Resolución de las URLs indexables: ``/{idioma}/observation/{slug}``.

El slug lo calcula ``utils.station_url`` y lo materializa
``scripts/build_station_url_slugs.py``. Estas pruebas cubren la vuelta
completa —catálogo → tabla → backend— porque una URL que deje de resolver es
una página perdida en Google, no un error visible en el despliegue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_files
from scripts.build_station_url_slugs import build_url_slugs
from server.main import create_app
from server.services import stations
from utils.station_url import candidate_url_slug, url_slug_map


def _catalog(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE stations (
            station_pk INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            network_code TEXT NOT NULL DEFAULT '',
            station_id TEXT NOT NULL,
            name TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            elevation_m REAL,
            timezone TEXT,
            country TEXT,
            region TEXT,
            locality TEXT,
            online INTEGER,
            has_historical INTEGER NOT NULL DEFAULT 0,
            manual INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE station_sensors (
            station_pk INTEGER PRIMARY KEY,
            thermometer INTEGER, hygrometer INTEGER, barometer INTEGER,
            anemometer INTEGER, wind_vane INTEGER, rain_gauge INTEGER,
            pyranometer INTEGER, uv INTEGER
        );
        CREATE TABLE station_visibility_overrides (
            station_pk INTEGER PRIMARY KEY,
            hidden INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    connection.executemany(
        "INSERT INTO stations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (1, "AEMET", "", "0201X", "BARCELONA  DRASSANES", 41.375, 2.177, 6,
             "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1, 1, 0),
            (2, "METEOCAT", "XEMA", "X4", "Barcelona - el Raval", 41.38, 2.17, 33,
             "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1, 1, 0),
            # Fuera de servicio: no entra en el sitemap, pero su URL puede
            # estar indexada y tiene que seguir resolviendo.
            (3, "NWS", "", "KOFF", "Offline Station", 40.0, -75.0, 20,
             "America/New_York", "US", "", "", 0, 0, 0),
            (4, "AEMET", "", "HIDDEN", "Estacion oculta", 40.0, -3.0, 10,
             "Europe/Madrid", "ES", "", "Madrid", 1, 0, 0),
            # Dos estaciones de la misma red con el mismo nombre y un id que
            # slugifica igual: el desempate por hash tiene que separarlas.
            (5, "ECCC", "A", "AB.1", "Tetrahedron", 49.5, -123.5, 900,
             "America/Vancouver", "CA", "", "", 1, 0, 0),
            (6, "ECCC", "B", "AB-1", "Tetrahedron", 49.6, -123.6, 910,
             "America/Vancouver", "CA", "", "", 1, 0, 0),
        ),
    )
    connection.execute(
        "INSERT INTO station_sensors VALUES (1,1,1,1,1,1,1,0,0)"
    )
    connection.execute(
        "INSERT INTO station_visibility_overrides(station_pk, hidden) VALUES (4, 1)"
    )
    connection.commit()
    connection.close()


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch) -> Path:
    database = tmp_path / "stations.sqlite"
    _catalog(database)
    build_url_slugs(database)
    monkeypatch.setattr(data_files, "STATIONS_DB_PATH", str(database))
    return database


# =====================================================================
# Cálculo del slug
# =====================================================================

def test_slug_is_name_plus_identifier() -> None:
    assert candidate_url_slug("BARCELONA  DRASSANES", "0201X") == "barcelona-drassanes-0201x"
    assert candidate_url_slug("Barcelona - Zona Universitària", "X8") == "barcelona-zona-universitaria-x8"


def test_colliding_stations_get_a_stable_suffix() -> None:
    rows = [
        {"station_pk": 5, "provider": "ECCC", "network_code": "A", "station_id": "AB.1", "name": "Tetrahedron"},
        {"station_pk": 6, "provider": "ECCC", "network_code": "B", "station_id": "AB-1", "name": "Tetrahedron"},
    ]
    slugs = url_slug_map(rows)
    assert slugs[5] != slugs[6]
    assert all(slug.startswith("tetrahedron-ab-1-") for slug in slugs.values())
    # Estable entre ejecuciones: el sufijo sale de un hash de la identidad.
    assert url_slug_map(rows) == slugs


def test_slug_of_a_different_network_is_untouched_by_collisions() -> None:
    """Una colisión dentro de una red no puede reescribir la URL de otra."""
    rows = [
        {"station_pk": 1, "provider": "AEMET", "network_code": "", "station_id": "0201X", "name": "Barcelona Drassanes"},
        {"station_pk": 5, "provider": "ECCC", "network_code": "A", "station_id": "AB.1", "name": "Tetrahedron"},
        {"station_pk": 6, "provider": "ECCC", "network_code": "B", "station_id": "AB-1", "name": "Tetrahedron"},
    ]
    assert url_slug_map(rows)[1] == "barcelona-drassanes-0201x"


# =====================================================================
# Tabla de resolución
# =====================================================================

def test_table_marks_publishable_and_merely_resolvable(catalog: Path) -> None:
    connection = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = {
        row["url_slug"]: row["indexable"]
        for row in connection.execute("SELECT url_slug, indexable FROM station_url_slugs")
    }
    connection.close()

    assert rows["barcelona-drassanes-0201x"] == 1
    assert rows["barcelona-el-raval-x4"] == 1
    # Apagada y oculta: resuelven, pero no se publican.
    assert rows["offline-station-koff"] == 0
    assert rows["estacion-oculta-hidden"] == 0


def test_rebuilding_the_table_is_idempotent(catalog: Path) -> None:
    first = build_url_slugs(catalog)
    second = build_url_slugs(catalog)
    assert first == second


# =====================================================================
# Backend
# =====================================================================

def test_find_by_url_slug_returns_the_catalog_record(catalog: Path) -> None:
    record = stations.find_by_url_slug("barcelona-drassanes-0201x")
    assert record is not None
    assert record["provider"] == "AEMET"
    assert record["station_id"] == "0201X"
    assert record["url_slug"] == "barcelona-drassanes-0201x"
    assert record["indexable"] is True
    assert record["catalog_country"] == "ES"
    assert record["sensors"]["thermometer"] is True


def test_offline_station_still_resolves_but_is_not_indexable(catalog: Path) -> None:
    record = stations.find_by_url_slug("offline-station-koff")
    assert record is not None
    assert record["indexable"] is False


def test_unknown_slug_is_none(catalog: Path) -> None:
    assert stations.find_by_url_slug("no-existe-nada") is None
    assert stations.find_by_url_slug("") is None


def test_url_slug_for_completes_the_round_trip(catalog: Path) -> None:
    slug = stations.url_slug_for("AEMET", "0201X")
    assert slug == "barcelona-drassanes-0201x"
    assert stations.find_by_url_slug(slug)["station_id"] == "0201X"


def test_url_slugs_for_resolves_a_batch(catalog: Path) -> None:
    """El ranking pide cuarenta slugs de golpe; no puede ser cuarenta consultas."""
    slugs = stations.url_slugs_for(
        [("AEMET", "0201X"), ("METEOCAT", "X4"), ("IEM", "NO-EXISTE"), ("", "")]
    )
    assert slugs == {
        ("AEMET", "0201X"): "barcelona-drassanes-0201x",
        ("METEOCAT", "X4"): "barcelona-el-raval-x4",
    }
    assert stations.url_slugs_for([]) == {}


def test_indexable_catalog_paginates(catalog: Path) -> None:
    assert stations.indexable_url_slug_count() == 4
    page = stations.indexable_url_slugs(offset=0, limit=2)
    assert len(page) == 2
    assert {row["url_slug"] for row in page}.isdisjoint(
        {row["url_slug"] for row in stations.indexable_url_slugs(offset=2, limit=2)}
    )
    # El país llega sin normalizar: es el que decide los idiomas de la ficha.
    assert stations.find_by_url_slug(page[0]["url_slug"]) is not None


def test_nearby_excludes_itself_and_orders_by_distance(catalog: Path) -> None:
    nearby = stations.indexable_stations_near(41.375, 2.177, exclude=("AEMET", "0201X"))
    assert [row["url_slug"] for row in nearby][:1] == ["barcelona-el-raval-x4"]
    assert all(row["provider"] != "AEMET" or row["station_id"] != "0201X" for row in nearby)
    assert nearby == sorted(nearby, key=lambda row: row["distance_km"])


# =====================================================================
# Endpoints
# =====================================================================

def test_endpoint_resolves_and_reports_404(catalog: Path) -> None:
    client = TestClient(create_app())

    response = client.get("/v1/stations/by-url-slug/barcelona-drassanes-0201x")
    assert response.status_code == 200
    body = response.json()
    assert body["station_id"] == "0201X"
    assert body["url_slug"] == "barcelona-drassanes-0201x"
    assert body["indexable"] is True

    missing = client.get("/v1/stations/by-url-slug/no-existe")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "station_not_found"


def test_indexable_and_nearby_endpoints(catalog: Path) -> None:
    client = TestClient(create_app())

    catalogue = client.get("/v1/stations/indexable?limit=2").json()
    assert catalogue["total"] == 4
    assert catalogue["count"] == 2
    assert all("url_slug" in row for row in catalogue["stations"])

    nearby = client.get(
        "/v1/stations/indexable-near"
        "?lat=41.375&lon=2.177&exclude_provider=AEMET&exclude_station_id=0201X&limit=3"
    ).json()
    assert nearby["stations"][0]["url_slug"] == "barcelona-el-raval-x4"
    assert nearby["stations"][0]["distance_km"] > 0

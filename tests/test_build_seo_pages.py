from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.build_seo_pages import build_pages, load_stations


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
            has_historical INTEGER NOT NULL DEFAULT 0,
            online INTEGER
        );
        CREATE TABLE station_sensors (
            station_pk INTEGER PRIMARY KEY,
            thermometer INTEGER,
            hygrometer INTEGER,
            barometer INTEGER,
            anemometer INTEGER,
            wind_vane INTEGER,
            rain_gauge INTEGER,
            pyranometer INTEGER,
            uv INTEGER
        );
        CREATE TABLE station_visibility_overrides (
            station_pk INTEGER PRIMARY KEY,
            hidden INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    rows = (
        (1, "AEMET", "", "0201X", "BARCELONA  DRASSANES", 41.375, 2.177, 6, "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1, None),
        (2, "METEOCAT", "XEMA", "X4", "Barcelona - el Raval", 41.38, 2.17, 33, "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1, None),
        (3, "AEMET", "", "HIDDEN", "Estacion oculta", 40.0, -3.0, 10, "Europe/Madrid", "ES", "", "Madrid", 0, None),
        (4, "AEMET", "", "NOCOORD", "Sin coordenadas", None, None, 10, "Europe/Madrid", "ES", "", "", 0, None),
        (5, "METEOFRANCE", "", "75107005", "TOUR EIFFEL", 48.858333, 2.2945, 33, "Europe/Paris", "", "", "", 1, None),
        (6, "NWS", "", "OFFLINE", "Offline station", 40.0, -75.0, 20, "America/New_York", "", "", "", 0, 0),
        (7, "IEM", "ASOS", "KXYZ", "Excluded IEM", 40.0, -74.0, 20, "America/New_York", "US", "", "", 0, 1),
        (8, "NWS", "", "KNYC", "Central Park", 40.7789, -73.9692, 47, "America/New_York", "US", "New York", "New York", 0, 1),
    )
    connection.executemany(
        "INSERT INTO stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.executemany(
        "INSERT INTO station_sensors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (1, 1, 1, 1, 1, 1, 1, 0, 0),
            (2, 1, 1, 0, 0, 0, 1, 1, 1),
            (5, 1, 1, 1, 1, 1, 1, 0, 0),
            (6, 1, 1, 1, 1, 1, 1, 0, 0),
            (7, 1, 1, 1, 1, 1, 1, 0, 0),
            (8, 1, 1, 1, 1, 1, 1, 0, 0),
        ),
    )
    connection.execute(
        "INSERT INTO station_visibility_overrides(station_pk, hidden) VALUES (3, 1)"
    )
    connection.commit()
    connection.close()


def test_builds_directories_that_link_to_the_observation_routes(tmp_path: Path):
    """Los índices estáticos ya solo enlazan; las fichas las sirve SvelteKit."""
    database = tmp_path / "stations.sqlite"
    output = tmp_path / "static"
    _catalog(database)

    summary = build_pages(
        database=database,
        output=output,
        providers=("AEMET", "METEOCAT"),
    )

    assert summary == {
        "stations": 2,
        "providers": 2,
        "cities": 1,
        "languages": 6,
        "pages": 30,
        "sitemap_urls": 30,
    }

    # Ni una sola ficha de estación en disco: eran 300.000 ficheros que ahora
    # responde el frontend con los datos ya renderizados.
    assert not (output / "es" / "estaciones" / "aemet").exists()
    assert not list(output.rglob("*-0201x.html"))

    provider_index = (output / "es" / "estaciones" / "aemet.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/es/observation/barcelona-drassanes-0201x"' in provider_index
    assert "barcelona-drassanes-0201x.html" not in provider_index
    assert '<link rel="canonical" href="https://www.meteolabx.com/es/estaciones/aemet.html">' in provider_index

    english_index = (output / "en" / "weather-stations" / "aemet.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/en/observation/barcelona-drassanes-0201x"' in english_index

    city_page = (output / "es" / "tiempo" / "barcelona.html").read_text(encoding="utf-8")
    assert "<h1>Tiempo y estaciones meteorológicas en Barcelona</h1>" in city_page
    assert 'href="/es/observation/barcelona-el-raval-x4"' in city_page
    assert "Datos observados, no predicción" in city_page
    assert 'hreflang="en" href="https://www.meteolabx.com/en/weather/barcelona.html"' in city_page

    city_directory = (output / "ca" / "temps.html").read_text(encoding="utf-8")
    assert "Temps i estacions per ciutat" in city_directory
    assert "/ca/temps/barcelona.html" in city_directory

    # El script que copiaba valores del iframe de Streamlit se fue con las fichas.
    assert not (output / "seo-observation.js").exists()
    assert "seo-observation.js" not in provider_index


def test_sitemap_keeps_only_the_pages_that_remain_static(tmp_path: Path):
    """``sitemap.xml`` y ``robots.txt`` los publica ahora el frontend."""
    database = tmp_path / "stations.sqlite"
    output = tmp_path / "static"
    _catalog(database)

    build_pages(database=database, output=output, providers=("AEMET", "METEOCAT"))

    assert not (output / "sitemap.xml").exists()
    assert not (output / "robots.txt").exists()
    assert not list(output.glob("sitemap-*.xml"))

    sitemap = (output / "directories-sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<url>") == 30
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in sitemap
    assert "https://www.meteolabx.com/es/estaciones/aemet.html" in sitemap
    assert "https://www.meteolabx.com/fr/stations-meteo/aemet.html" in sitemap
    assert "https://www.meteolabx.com/es/tiempo/barcelona.html" in sitemap
    assert 'hreflang="x-default" href="https://www.meteolabx.com/es/estaciones.html"' in sitemap
    # Las fichas viajan en el sitemap del frontend, no en este.
    assert "/observation/" not in sitemap
    assert "https://meteolabx.com" not in sitemap


def test_stale_station_pages_are_removed_on_rebuild(tmp_path: Path):
    """Un despliegue anterior dejó fichas en disco; hay que barrerlas.

    Si sobrevivieran, seguirían sirviéndose tal cual desde el paquete de
    Streamlit y competirían con la URL nueva por el mismo contenido.
    """
    database = tmp_path / "stations.sqlite"
    output = tmp_path / "static"
    _catalog(database)

    stale = output / "es" / "estaciones" / "aemet" / "barcelona-drassanes-0201x.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("ficha antigua", encoding="utf-8")
    stale_sitemap = output / "sitemap-1.xml"
    stale_sitemap.write_text("<urlset/>", encoding="utf-8")

    build_pages(database=database, output=output, providers=("AEMET", "METEOCAT"))

    assert not stale.exists()
    assert not stale_sitemap.exists()


def test_excludes_hidden_and_coordinate_less_stations(tmp_path: Path):
    database = tmp_path / "stations.sqlite"
    _catalog(database)

    stations = load_stations(database, ("AEMET", "METEOCAT"))

    assert [(station.provider, station.station_id) for station in stations] == [
        ("AEMET", "0201X"),
        ("METEOCAT", "X4"),
    ]


def test_localizes_only_the_languages_of_each_country(tmp_path: Path):
    database = tmp_path / "stations.sqlite"
    output = tmp_path / "static"
    _catalog(database)

    stations = load_stations(database, ("METEOFRANCE", "NWS", "IEM", "WINDY"))
    assert [(station.provider, station.station_id, station.country) for station in stations] == [
        ("METEOFRANCE", "75107005", "FR"),
        ("NWS", "KNYC", "US"),
    ]

    summary = build_pages(database=database, output=output, providers=("METEOFRANCE",))
    assert summary["stations"] == 1
    assert summary["providers"] == 1
    assert summary["cities"] == 1

    french_index = (output / "fr" / "stations-meteo" / "meteofrance.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/fr/observation/tour-eiffel-75107005"' in french_index

    paris_page = (output / "fr" / "meteo" / "paris.html").read_text(encoding="utf-8")
    assert "Tour Eiffel" in paris_page
    assert 'href="/fr/observation/tour-eiffel-75107005"' in paris_page

    # Una estación francesa no se publica en catalán ni en italiano.
    assert not (output / "it" / "stazioni-meteo" / "meteofrance.html").exists()
    assert not (output / "ca" / "estacions" / "meteofrance.html").exists()

    us_output = tmp_path / "us-static"
    build_pages(database=database, output=us_output, providers=("NWS",))
    english_index = (us_output / "en" / "weather-stations" / "nws.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/en/observation/central-park-knyc"' in english_index
    assert (us_output / "es" / "estaciones" / "nws.html").exists()
    assert not (us_output / "fr" / "stations-meteo" / "nws.html").exists()

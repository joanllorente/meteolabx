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
            has_historical INTEGER NOT NULL DEFAULT 0
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
        (1, "AEMET", "", "0201X", "BARCELONA  DRASSANES", 41.375, 2.177, 6, "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1),
        (2, "METEOCAT", "XEMA", "X4", "Barcelona - el Raval", 41.38, 2.17, 33, "Europe/Madrid", "ES", "Catalunya", "Barcelona", 1),
        (3, "AEMET", "", "HIDDEN", "Estacion oculta", 40.0, -3.0, 10, "Europe/Madrid", "ES", "", "Madrid", 0),
        (4, "AEMET", "", "NOCOORD", "Sin coordenadas", None, None, 10, "Europe/Madrid", "ES", "", "", 0),
    )
    connection.executemany(
        "INSERT INTO stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.executemany(
        "INSERT INTO station_sensors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (1, 1, 1, 1, 1, 1, 1, 0, 0),
            (2, 1, 1, 0, 0, 0, 1, 1, 1),
        ),
    )
    connection.execute(
        "INSERT INTO station_visibility_overrides(station_pk, hidden) VALUES (3, 1)"
    )
    connection.commit()
    connection.close()


def test_builds_indexable_station_pages_and_sitemap(tmp_path: Path):
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
        "pages": 42,
        "sitemap_urls": 43,
    }
    station_page = (
        output
        / "es"
        / "estaciones"
        / "aemet"
        / "barcelona-drassanes-0201x.html"
    )
    page = station_page.read_text(encoding="utf-8")
    assert "<h1>Barcelona Drassanes</h1>" in page
    assert "Estación meteorológica Barcelona Drassanes" in page
    assert '<link rel="canonical" href="https://www.meteolabx.com/es/estaciones/aemet/barcelona-drassanes-0201x.html">' in page
    assert "temperatura, humedad, presión atmosférica" in page
    assert "<h2 id=\"current\">Tiempo actual en Barcelona Drassanes</h2>" in page
    assert "<h2 id=\"history\">Histórico meteorológico de Barcelona Drassanes</h2>" in page
    assert "tab=historico" in page
    assert "/es/estaciones/meteocat/barcelona-el-raval-x4.html" in page
    assert page.count('rel="alternate" hreflang=') == 7
    assert 'hreflang="en" href="https://www.meteolabx.com/en/weather-stations/aemet/barcelona-drassanes-0201x.html"' in page
    assert 'hreflang="x-default" href="https://www.meteolabx.com/es/estaciones/aemet/barcelona-drassanes-0201x.html"' in page

    english_page = (
        output
        / "en"
        / "weather-stations"
        / "aemet"
        / "barcelona-drassanes-0201x.html"
    ).read_text(encoding="utf-8")
    assert '<html lang="en">' in english_page
    assert "<h2 id=\"profile\">Station profile</h2>" in english_page
    assert "temperature, humidity, atmospheric pressure" in english_page
    assert "lang=en" in english_page
    assert '<link rel="canonical" href="https://www.meteolabx.com/en/weather-stations/aemet/barcelona-drassanes-0201x.html">' in english_page
    assert "Historical weather data for Barcelona Drassanes" in english_page

    city_page = (output / "es" / "tiempo" / "barcelona.html").read_text(
        encoding="utf-8"
    )
    assert "<h1>Tiempo y estaciones meteorológicas en Barcelona</h1>" in city_page
    assert "Barcelona Drassanes" in city_page
    assert "Barcelona - el Raval" in city_page
    assert "Datos observados, no predicción" in city_page
    assert '<link rel="canonical" href="https://www.meteolabx.com/es/tiempo/barcelona.html">' in city_page
    assert 'hreflang="en" href="https://www.meteolabx.com/en/weather/barcelona.html"' in city_page

    city_directory = (output / "ca" / "temps.html").read_text(encoding="utf-8")
    assert "Temps i estacions per ciutat" in city_directory
    assert "/ca/temps/barcelona.html" in city_directory

    catalan_page = (
        output
        / "ca"
        / "estacions"
        / "meteocat"
        / "barcelona-el-raval-x4.html"
    ).read_text(encoding="utf-8")
    assert '<html lang="ca">' in catalan_page
    assert "Fitxa de l&#x27;estació" in catalan_page

    sitemap = (output / "sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<url>") == 43
    assert sitemap.count("<xhtml:link") == 294
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in sitemap
    assert "https://www.meteolabx.com/es/estaciones/aemet.html" in sitemap
    assert "https://www.meteolabx.com/fr/stations-meteo/aemet.html" in sitemap
    assert "https://www.meteolabx.com/es/tiempo/barcelona.html" in sitemap
    assert "https://www.meteolabx.com/pt/tempo/barcelona.html" in sitemap
    assert 'hreflang="x-default" href="https://www.meteolabx.com/es/estaciones.html"' in sitemap
    assert "https://meteolabx.com" not in sitemap
    assert (output / "robots.txt").read_text(encoding="utf-8").endswith(
        "Sitemap: https://www.meteolabx.com/sitemap.xml\n"
    )


def test_excludes_hidden_and_coordinate_less_stations(tmp_path: Path):
    database = tmp_path / "stations.sqlite"
    _catalog(database)

    stations = load_stations(database, ("AEMET", "METEOCAT"))

    assert [(station.provider, station.station_id) for station in stations] == [
        ("AEMET", "0201X"),
        ("METEOCAT", "X4"),
    ]

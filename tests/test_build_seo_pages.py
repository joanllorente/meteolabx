from __future__ import annotations

import sqlite3
from pathlib import Path

import scripts.build_seo_pages as seo_builder
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
    assert "Observación interactiva de Barcelona Drassanes" not in page
    assert "Consulta las últimas observaciones disponibles" not in page
    assert '<div class="observation-grid"' in page
    assert page.count('data-observation-slot="') == 6
    assert page.count('class="obs-extremes"') == 6
    assert '<iframe class="observation-loader"' in page
    assert '<iframe class="live-frame"' not in page
    assert "e=AEMET~barcelona-drassanes&amp;sid=0201X&amp;tab=observacion&amp;lang=es&amp;embed=seo" in page
    assert "Datos avanzados" not in page
    assert 'data-maximum-label="Máx."' in page
    assert 'data-minimum-label="Mín."' in page
    assert "Abrir panel completo" in page
    assert "tab=observacion&amp;lang=es&amp;from=seo" in page
    assert page.count("from=seo") == 2
    assert "tab=historico&amp;lang=es&amp;from=seo" not in page
    assert '<link rel="stylesheet" href="/seo-pages.css?v=1">' in page
    assert '<script src="/seo-observation.js?v=1" defer></script>' in page
    assert "syncObservation" not in page
    assert "syncObservation" in (output / "seo-observation.js").read_text(encoding="utf-8")
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


def test_builds_localized_international_station_and_excludes_disallowed_networks(tmp_path: Path):
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
    assert summary["pages"] == 18

    french_page = (
        output / "fr" / "stations-meteo" / "meteofrance" / "tour-eiffel-75107005.html"
    ).read_text(encoding="utf-8")
    assert "Tour Eiffel, Paris" in french_page
    assert "France" in french_page
    assert "sid=75107005" in french_page

    paris_page = (output / "fr" / "meteo" / "paris.html").read_text(encoding="utf-8")
    assert "Tour Eiffel" in paris_page
    assert not (output / "it" / "stazioni-meteo" / "meteofrance" / "tour-eiffel-75107005.html").exists()
    assert not (output / "ca" / "estacions" / "meteofrance" / "tour-eiffel-75107005.html").exists()

    us_output = tmp_path / "us-static"
    us_summary = build_pages(database=database, output=us_output, providers=("NWS",))
    assert us_summary["stations"] == 1
    assert (us_output / "en" / "weather-stations" / "nws" / "central-park-knyc.html").exists()
    assert (us_output / "es" / "estaciones" / "nws" / "central-park-knyc.html").exists()
    assert not (us_output / "fr" / "stations-meteo" / "nws" / "central-park-knyc.html").exists()
    assert not (us_output / "ca" / "estacions" / "nws" / "central-park-knyc.html").exists()
    assert not (us_output / "it" / "stazioni-meteo" / "nws" / "central-park-knyc.html").exists()
    assert not (us_output / "pt" / "estacoes-meteorologicas" / "nws" / "central-park-knyc.html").exists()


def test_splits_large_sitemap_into_an_index(tmp_path: Path, monkeypatch):
    database = tmp_path / "stations.sqlite"
    output = tmp_path / "static"
    _catalog(database)
    monkeypatch.setattr(seo_builder, "SITEMAP_URL_LIMIT", 10)

    summary = build_pages(database=database, output=output, providers=("METEOFRANCE",))

    sitemap_index = (output / "sitemap.xml").read_text(encoding="utf-8")
    assert summary["sitemap_urls"] == 19
    assert "<sitemapindex" in sitemap_index
    assert sitemap_index.count("<sitemap>") == 2
    for index in range(1, 3):
        child = (output / f"sitemap-{index}.xml").read_text(encoding="utf-8")
        assert child.count("<url>") <= 10

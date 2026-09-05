#!/usr/bin/env python3
"""Genera los índices HTML indexables de estaciones públicas.

Escribe directorios, índices por red y páginas de ciudad en el directorio
estático del paquete Streamlit, de modo que rutas como
``/en/weather-stations/aemet.html`` se sirven como HTML completo, sin
ejecutar JavaScript ni abrir una sesión de Streamlit. Cada página publica
canonical propio, alternates ``hreflang`` para los seis idiomas de la
aplicación y datos estructurados localizados.

Las **fichas de estación** ya no salen de aquí: las sirve el frontend
SvelteKit en ``/{idioma}/observation/{slug}`` con la observación renderizada
en servidor. Este script solo produce los enlaces hacia ellas, y su sitemap
—``directories-sitemap.xml``— cubre lo que sigue siendo estático; el índice
``sitemap.xml`` lo publica el frontend.

El catálogo incluye redes públicas españolas e internacionales. Las estaciones
marcadas como inactivas se excluyen para evitar páginas de poco valor, al igual
que las redes IEM, Windy y Netatmo, que no forman parte del catálogo SEO.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import html
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.seo_pages_i18n import DEFAULT_LANGUAGE, LANGUAGES, LanguageSpec
from utils.station_slug import slugify
from utils.station_url import url_slug_map


SITE_URL = "https://www.meteolabx.com"
# Segmento de las fichas en el frontend nuevo. Vive también en
# ``web/src/lib/seo/ownership.js``; si cambia, cambia en los dos sitios.
OBSERVATION_SEGMENT = "observation"
# Las fichas de estación ya no son ficheros: las publica SvelteKit y su
# sitemap. Aquí solo quedan los directorios, los índices de red y las
# ciudades, que siguen siendo HTML estático.
DIRECTORY_SITEMAP_NAME = "directories-sitemap.xml"
DEFAULT_PROVIDERS = (
    "AEMET",
    "METEOCAT",
    "EUSKALMET",
    "METEOGALICIA",
    "POEM",
    "METEOFRANCE",
    "FROST",
    "NWS",
    "METOFFICE",
    "METEOHUB_IT",
    "IPMA",
    "GEOSPHERE",
    "SMHI",
    "ECCC",
    "CLIMANTARTIDE",
)
EXCLUDED_SEO_PROVIDERS = frozenset({"IEM", "NETATMO", "WINDY"})
PROVIDER_LABELS = {
    "AEMET": "AEMET",
    "METEOCAT": "Meteocat",
    "EUSKALMET": "Euskalmet",
    "METEOGALICIA": "MeteoGalicia",
    "POEM": "Puertos del Estado",
    "METEOFRANCE": "Météo-France",
    "FROST": "Frost (MET Norway)",
    "NWS": "National Weather Service",
    "METOFFICE": "Met Office",
    "METEOHUB_IT": "MeteoHub Italia",
    "IPMA": "IPMA",
    "GEOSPHERE": "GeoSphere Austria",
    "SMHI": "SMHI",
    "ECCC": "Environment Canada",
    "CLIMANTARTIDE": "ClimAntartide",
}
PROVIDER_COUNTRIES = {
    "AEMET": "ES", "METEOCAT": "ES", "EUSKALMET": "ES",
    "METEOGALICIA": "ES", "POEM": "ES", "METEOFRANCE": "FR",
    "FROST": "NO", "NWS": "US", "METOFFICE": "GB",
    "METEOHUB_IT": "IT", "IPMA": "PT", "GEOSPHERE": "AT",
    "SMHI": "SE", "ECCC": "CA", "CLIMANTARTIDE": "AQ",
}
LANGUAGES_BY_COUNTRY = {
    "ES": ("es", "ca", "en", "fr", "it", "pt"),
    "FR": ("fr", "en", "es"),
    "CA": ("en", "fr", "es"),
    "IT": ("it", "en", "es"),
    "PT": ("pt", "en", "es"),
    "US": ("en", "es"),
    "GB": ("en", "es"),
    "NO": ("en", "es"),
    "AT": ("en", "es"),
    "SE": ("en", "es"),
    "AQ": ("en", "es"),
}
SEO_STYLESHEET = """
:root{color-scheme:dark;--bg:#0e1117;--card:#171b24;--line:#2a3240;--text:#f5f7fb;--muted:#a9b4c4;--blue:#5da8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.58}a{color:var(--blue)}
header,main,footer{width:min(1040px,calc(100% - 32px));margin:auto}header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:24px 0}.brand{color:var(--text);text-decoration:none;font-size:1.25rem;font-weight:800}.header-links{display:flex;align-items:center;gap:18px}.header-links>a{text-decoration:none}.languages{display:flex;gap:3px;padding-left:8px;border-left:1px solid var(--line)}.languages a{padding:2px 5px;color:var(--muted);font-size:.75rem;text-decoration:none}.languages a[aria-current=page]{color:var(--text);font-weight:800}
.breadcrumbs{color:var(--muted);font-size:.9rem;margin:18px 0}.breadcrumbs a{color:var(--muted)}h1{line-height:1.14;font-size:clamp(2rem,5vw,3.25rem);margin:.35em 0}h2{margin-top:2rem;line-height:1.25}.lede{color:var(--muted);font-size:1.12rem;max-width:760px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:24px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}.label{display:block;color:var(--muted);font-size:.82rem;margin-bottom:3px}.value{font-weight:700}.cta{display:inline-block;padding:12px 18px;border-radius:12px;background:#2384ff;color:#fff;font-weight:750;text-decoration:none;margin:10px 0}.cta.secondary{background:transparent;color:var(--blue);border:1px solid var(--line)}.actions{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 8px}
.live-panel{margin:24px 0 34px}.observation-status{color:var(--muted);margin:12px 0;min-height:1.4em}.observation-status.error{color:#ef7373}.observation-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:18px 0}.observation-card{min-height:132px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:17px}.observation-card .obs-label{color:var(--muted);font-size:.78rem;font-weight:750;letter-spacing:.035em;text-transform:uppercase}.observation-card .obs-value{display:block;margin-top:7px;font-size:1.65rem;line-height:1.15;font-weight:800}.observation-card .obs-detail{color:var(--muted);font-size:.84rem;line-height:1.38;margin-top:9px}.observation-card .obs-extremes{color:var(--text);font-size:.8rem;font-weight:650;margin-top:8px}.observation-loader{position:fixed;left:-10000px;top:0;width:1280px;height:1200px;border:0;opacity:.01;pointer-events:none}.fallback{color:var(--muted);font-size:.9rem}ul.links{padding-left:1.2rem;columns:2;column-gap:32px}ul.links li{break-inside:avoid;margin:.5rem 0}footer{color:var(--muted);border-top:1px solid var(--line);margin-top:52px;padding:26px 0 42px;font-size:.9rem}
@media(prefers-color-scheme:light){:root{color-scheme:light;--bg:#f7f9fc;--card:#fff;--line:#dbe2ec;--text:#101620;--muted:#596779;--blue:#176fce}}@media(max-width:760px){.header-links>a{display:none}ul.links{columns:1}.observation-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.actions .cta{width:100%;text-align:center}}@media(max-width:430px){.observation-grid{grid-template-columns:1fr}}
""".strip()
SENSOR_KEYS = (
    "thermometer",
    "hygrometer",
    "barometer",
    "anemometer",
    "wind_vane",
    "rain_gauge",
    "pyranometer",
    "uv",
)


@dataclass(frozen=True)
class StationPage:
    station_pk: int
    provider: str
    network_code: str
    station_id: str
    name: str
    latitude: float | None
    longitude: float | None
    elevation_m: float | None
    timezone: str
    country: str
    region: str
    locality: str
    has_historical: bool
    sensor_keys: tuple[str, ...]
    url_slug: str

    @property
    def provider_slug(self) -> str:
        return slugify(self.provider)

    @property
    def provider_label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def language_codes(self) -> tuple[str, ...]:
        return LANGUAGES_BY_COUNTRY.get(self.country, ("en", "es"))

    def path(self, language: LanguageSpec) -> str:
        """Ruta pública de la ficha, ya servida por el frontend SvelteKit.

        Antes era ``/{idioma}/{directorio}/{red}/{slug}.html``. Esa URL sigue
        respondiendo, pero con un 301 hacia esta: el slug no cambia, así que
        la traducción es directa y el frontend la resuelve sin consultar nada.
        """
        return f"/{language.code}/{OBSERVATION_SEGMENT}/{self.url_slug}"

    def canonical_url(self, language: LanguageSpec) -> str:
        return f"{SITE_URL}{self.path(language)}"

    def location_label(self, language: LanguageSpec) -> str:
        localized = STATION_LOCATION_NAMES.get(
            (self.provider.upper(), self.station_id.upper()), {}
        ).get(language.code)
        if localized:
            return localized
        parts: list[str] = []
        for value in (
            self.locality,
            self.region,
            _country_label(self.country, language.code),
        ):
            clean = str(value or "").strip()
            if clean and clean not in parts:
                parts.append(clean)
        return ", ".join(parts)


@dataclass(frozen=True)
class CityPage:
    slug: str
    name: str
    latitude: float
    longitude: float
    radius_km: float

    def path(self, language: LanguageSpec) -> str:
        return f"/{language.code}/{language.city_slug}/{self.slug}.html"

    def canonical_url(self, language: LanguageSpec) -> str:
        return f"{SITE_URL}{self.path(language)}"


CITIES = (
    CityPage("barcelona", "Barcelona", 41.3874, 2.1686, 20.0),
    CityPage("madrid", "Madrid", 40.4168, -3.7038, 25.0),
    CityPage("valencia", "Valencia", 39.4699, -0.3763, 25.0),
    CityPage("sevilla", "Sevilla", 37.3891, -5.9845, 25.0),
    CityPage("zaragoza", "Zaragoza", 41.6488, -0.8891, 25.0),
    CityPage("malaga", "Málaga", 36.7213, -4.4214, 25.0),
    CityPage("bilbao", "Bilbao", 43.2630, -2.9350, 20.0),
    CityPage("a-coruna", "A Coruña", 43.3623, -8.4115, 20.0),
    CityPage("vigo", "Vigo", 42.2406, -8.7207, 20.0),
    CityPage("palma", "Palma", 39.5696, 2.6502, 25.0),
    CityPage("paris", "Paris", 48.8566, 2.3522, 30.0),
    CityPage("london", "London", 51.5074, -0.1278, 35.0),
    CityPage("rome", "Roma", 41.9028, 12.4964, 30.0),
    CityPage("lisbon", "Lisboa", 38.7223, -9.1393, 30.0),
    CityPage("oslo", "Oslo", 59.9139, 10.7522, 30.0),
    CityPage("stockholm", "Stockholm", 59.3293, 18.0686, 30.0),
    CityPage("vienna", "Wien", 48.2082, 16.3738, 30.0),
    CityPage("new-york", "New York", 40.7128, -74.0060, 40.0),
    CityPage("washington-dc", "Washington, D.C.", 38.9072, -77.0369, 40.0),
    CityPage("toronto", "Toronto", 43.6532, -79.3832, 40.0),
    CityPage("montreal", "Montréal", 45.5017, -73.5673, 40.0),
    CityPage("vancouver", "Vancouver", 49.2827, -123.1207, 40.0),
)

STATION_SEARCH_NAMES: dict[tuple[str, str], dict[str, str]] = {
    ("METEOCAT", "D5"): {
        "es": "Observatorio Fabra",
        "en": "Fabra Observatory",
        "fr": "Observatoire Fabra",
        "it": "Osservatorio Fabra",
        "pt": "Observatório Fabra",
    },
    ("METEOFRANCE", "75107005"): {
        "es": "Torre Eiffel, París",
        "ca": "Torre Eiffel, París",
        "en": "Eiffel Tower, Paris",
        "fr": "Tour Eiffel, Paris",
        "it": "Torre Eiffel, Parigi",
        "pt": "Torre Eiffel, Paris",
    },
}
STATION_LOCATION_NAMES: dict[tuple[str, str], dict[str, str]] = {
    ("METEOFRANCE", "75107005"): {
        "es": "París, Francia",
        "ca": "París, França",
        "en": "Paris, France",
        "fr": "Paris, France",
        "it": "Parigi, Francia",
        "pt": "Paris, França",
    },
}


def _directory_path(language: LanguageSpec) -> str:
    return f"/{language.code}/{language.directory_slug}.html"


def _provider_path(provider: str, language: LanguageSpec) -> str:
    return f"/{language.code}/{language.directory_slug}/{slugify(provider)}.html"


def _city_directory_path(language: LanguageSpec) -> str:
    return f"/{language.code}/{language.city_slug}.html"


def _station_search_name(station: StationPage, language: LanguageSpec) -> str:
    aliases = STATION_SEARCH_NAMES.get(
        (station.provider.upper(), station.station_id.upper()),
        {},
    )
    return aliases.get(language.code, station.name)


@lru_cache(maxsize=64)
def _country_label(country_code: str, language_code: str) -> str:
    code = str(country_code or "").strip().upper()
    if not code:
        return ""
    try:
        from babel import Locale

        return str(Locale.parse(language_code).territories.get(code) or code)
    except Exception:  # pragma: no cover - Babel es dependencia runtime
        return code


def _parse_providers(raw: str | None) -> tuple[str, ...]:
    providers = tuple(
        token.strip().upper()
        for token in str(raw or "").split(",")
        if token.strip()
    )
    selected = providers or DEFAULT_PROVIDERS
    return tuple(provider for provider in selected if provider not in EXCLUDED_SEO_PROVIDERS)


def _display_name(value: object) -> str:
    """Limpia espacios y suaviza inventarios escritos enteros en mayúsculas."""
    clean = " ".join(str(value or "").split())
    if clean and clean == clean.upper() and any(character.isalpha() for character in clean):
        return clean.title()
    return clean


def _default_output_dir() -> Path:
    """Dónde se publican los directorios, los índices y las ciudades.

    Los sirve el frontend SvelteKit desde sus estáticos: son las últimas
    páginas que quedaban en Streamlit y, al retirarlo, sin este destino se
    convertirían en 404 —unas doscientas URLs indexadas—.

    Se generan aquí y viajan en el repositorio, como el visor de Predicción:
    el frontend vive en otro servicio y no ve el catálogo SQLite, así que no
    puede construirlas en su propio despliegue.
    """
    return Path(__file__).resolve().parents[1] / "web" / "static"


def load_stations(database: Path, providers: Sequence[str]) -> list[StationPage]:
    if not database.is_file():
        raise FileNotFoundError(f"No existe el catálogo SQLite: {database}")
    providers = tuple(
        str(provider).strip().upper()
        for provider in providers
        if str(provider).strip().upper() not in EXCLUDED_SEO_PROVIDERS
    )
    if not providers:
        return []

    placeholders = ",".join("?" for _ in providers)
    query = f"""
        SELECT s.station_pk, s.provider, s.network_code, s.station_id, s.name,
               s.latitude, s.longitude, s.elevation_m, s.timezone, s.country,
               s.region, s.locality, s.has_historical, s.online,
               ss.thermometer, ss.hygrometer, ss.barometer, ss.anemometer,
               ss.wind_vane, ss.rain_gauge, ss.pyranometer, ss.uv
        FROM stations s
        LEFT JOIN station_sensors ss USING(station_pk)
        LEFT JOIN station_visibility_overrides svo USING(station_pk)
        WHERE s.provider IN ({placeholders})
          AND COALESCE(svo.hidden, 0) = 0
          AND COALESCE(s.online, 1) = 1
          AND s.name IS NOT NULL AND TRIM(s.name) <> ''
          AND s.latitude IS NOT NULL AND s.longitude IS NOT NULL
        ORDER BY s.provider, s.name COLLATE NOCASE, s.station_id COLLATE NOCASE
    """
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, tuple(providers)).fetchall()
    finally:
        connection.close()

    url_slugs = url_slug_map(rows)
    return [
        StationPage(
            station_pk=int(row["station_pk"]),
            provider=str(row["provider"]),
            network_code=str(row["network_code"] or ""),
            station_id=str(row["station_id"]),
            name=_display_name(row["name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            elevation_m=float(row["elevation_m"]) if row["elevation_m"] is not None else None,
            timezone=str(row["timezone"] or ""),
            country=str(row["country"] or PROVIDER_COUNTRIES.get(str(row["provider"]), "")).upper(),
            region=str(row["region"] or ""),
            locality=str(row["locality"] or ""),
            has_historical=bool(row["has_historical"]),
            sensor_keys=tuple(key for key in SENSOR_KEYS if row[key] == 1),
            url_slug=url_slugs[int(row["station_pk"])],
        )
        for row in rows
    ]


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    latitude_1 = math.radians(lat1)
    latitude_2 = math.radians(lat2)
    dlat = latitude_2 - latitude_1
    dlon = math.radians(lon2 - lon1)
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(value)))


def _stations_for_city(
    city: CityPage,
    stations: Sequence[StationPage],
) -> list[tuple[StationPage, float]]:
    matches = [
        (
            station,
            _distance_km(
                city.latitude,
                city.longitude,
                station.latitude,
                station.longitude,
            ),
        )
        for station in stations
        if station.latitude is not None and station.longitude is not None
    ]
    matches = [item for item in matches if item[1] <= city.radius_km]
    matches.sort(key=lambda item: (item[1], item[0].name.casefold()))
    return matches


def _json_script(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def _station_alternates(station: StationPage) -> dict[str, str]:
    return {
        code: station.canonical_url(language)
        for code, language in LANGUAGES.items()
        if code in station.language_codes
    }


def _provider_alternates(
    provider: str,
    stations: Sequence[StationPage],
) -> dict[str, str]:
    available_codes = {code for station in stations for code in station.language_codes}
    return {
        code: f"{SITE_URL}{_provider_path(provider, language)}"
        for code, language in LANGUAGES.items()
        if code in available_codes
    }


def _directory_alternates() -> dict[str, str]:
    return {
        code: f"{SITE_URL}{_directory_path(language)}"
        for code, language in LANGUAGES.items()
    }


def _city_alternates(
    city: CityPage,
    matches: Sequence[tuple[StationPage, float]],
) -> dict[str, str]:
    available_codes = {
        code for station, _distance in matches for code in station.language_codes
    }
    return {
        code: city.canonical_url(language)
        for code, language in LANGUAGES.items()
        if code in available_codes
    }


def _city_directory_alternates(
    city_matches: Mapping[CityPage, Sequence[tuple[StationPage, float]]],
) -> dict[str, str]:
    available_codes = {
        code
        for matches in city_matches.values()
        for station, _distance in matches
        for code in station.language_codes
    }
    return {
        code: f"{SITE_URL}{_city_directory_path(language)}"
        for code, language in LANGUAGES.items()
        if code in available_codes
    }


def _alternate_tags(alternates: Mapping[str, str]) -> str:
    tags = [
        f'<link rel="alternate" hreflang="{code}" href="{html.escape(url, quote=True)}">'
        for code, url in alternates.items()
    ]
    tags.append(
        '<link rel="alternate" hreflang="x-default" '
        f'href="{html.escape(alternates[DEFAULT_LANGUAGE], quote=True)}">'
    )
    return "\n  ".join(tags)


def _language_navigation(
    language: LanguageSpec,
    alternates: Mapping[str, str],
) -> str:
    links = []
    for code, url in alternates.items():
        current = ' aria-current="page"' if code == language.code else ""
        links.append(
            f'<a href="{html.escape(url, quote=True)}" hreflang="{code}" lang="{code}"'
            f'{current}>{code.upper()}</a>'
        )
    return '<div class="languages" aria-label="Language">' + "".join(links) + "</div>"


def _page_shell_inline_legacy(
    *,
    language: LanguageSpec,
    title: str,
    description: str,
    canonical_url: str,
    alternates: Mapping[str, str],
    body: str,
    structured_data: Sequence[object],
) -> str:
    safe_title = html.escape(title)
    safe_description = html.escape(description, quote=True)
    safe_canonical = html.escape(canonical_url, quote=True)
    json_ld = "\n".join(
        f'<script type="application/ld+json">{_json_script(item)}</script>'
        for item in structured_data
    )
    og_alternates = "\n  ".join(
        f'<meta property="og:locale:alternate" content="{other.og_locale}">'
        for other in LANGUAGES.values()
        if other.code != language.code
    )
    return f"""<!doctype html>
<html lang="{language.code}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <meta name="description" content="{safe_description}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <link rel="canonical" href="{safe_canonical}">
  {_alternate_tags(alternates)}
  <link rel="icon" href="/favicon-32x32.png" sizes="32x32" type="image/png">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="MeteoLabX">
  <meta property="og:locale" content="{language.og_locale}">
  {og_alternates}
  <meta property="og:url" content="{safe_canonical}">
  <meta property="og:title" content="{safe_title}">
  <meta property="og:description" content="{safe_description}">
  <meta property="og:image" content="{SITE_URL}/og-image.png?v=12">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{safe_title}">
  <meta name="twitter:description" content="{safe_description}">
  <meta name="twitter:image" content="{SITE_URL}/og-image.png?v=12">
  {json_ld}
  <style>
    :root {{ color-scheme:dark; --bg:#0e1117; --card:#171b24; --line:#2a3240;
      --text:#f5f7fb; --muted:#a9b4c4; --blue:#5da8ff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,
      BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.58; }}
    a {{ color:var(--blue); }}
    header,main,footer {{ width:min(1040px,calc(100% - 32px)); margin:auto; }}
    header {{ display:flex; align-items:center; justify-content:space-between; gap:20px; padding:24px 0; }}
    .brand {{ color:var(--text); text-decoration:none; font-size:1.25rem; font-weight:800; }}
    .header-links {{ display:flex; align-items:center; gap:18px; }}
    .header-links>a {{ text-decoration:none; }}
    .languages {{ display:flex; gap:3px; padding-left:8px; border-left:1px solid var(--line); }}
    .languages a {{ padding:2px 5px; color:var(--muted); font-size:.75rem; text-decoration:none; }}
    .languages a[aria-current="page"] {{ color:var(--text); font-weight:800; }}
    .breadcrumbs {{ color:var(--muted); font-size:.9rem; margin:18px 0; }}
    .breadcrumbs a {{ color:var(--muted); }}
    h1 {{ line-height:1.14; font-size:clamp(2rem,5vw,3.25rem); margin:.35em 0; }}
    h2 {{ margin-top:2rem; line-height:1.25; }}
    .lede {{ color:var(--muted); font-size:1.12rem; max-width:760px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin:24px 0; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:18px; }}
    .label {{ display:block; color:var(--muted); font-size:.82rem; margin-bottom:3px; }}
    .value {{ font-weight:700; }}
    .cta {{ display:inline-block; padding:12px 18px; border-radius:12px; background:#2384ff;
      color:white; font-weight:750; text-decoration:none; margin:10px 0; }}
    .cta.secondary {{ background:transparent; color:var(--blue); border:1px solid var(--line); }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; margin:14px 0 8px; }}
    .live-panel {{ margin:24px 0 34px; }}
    .observation-status {{ color:var(--muted); margin:12px 0; min-height:1.4em; }}
    .observation-status.error {{ color:#ef7373; }}
    .observation-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px;
      margin:18px 0; }}
    .observation-card {{ min-height:132px; background:var(--card); border:1px solid var(--line);
      border-radius:16px; padding:17px; }}
    .observation-card .obs-label {{ color:var(--muted); font-size:.78rem; font-weight:750;
      letter-spacing:.035em; text-transform:uppercase; }}
    .observation-card .obs-value {{ display:block; margin-top:7px; font-size:1.65rem;
      line-height:1.15; font-weight:800; }}
    .observation-card .obs-detail {{ color:var(--muted); font-size:.84rem; line-height:1.38;
      margin-top:9px; }}
    .observation-card .obs-extremes {{ color:var(--text); font-size:.8rem; font-weight:650;
      margin-top:8px; }}
    .observation-loader {{ position:fixed; left:-10000px; top:0; width:1280px; height:1200px;
      border:0; opacity:.01; pointer-events:none; }}
    .fallback {{ color:var(--muted); font-size:.9rem; }}
    ul.links {{ padding-left:1.2rem; columns:2; column-gap:32px; }}
    ul.links li {{ break-inside:avoid; margin:.5rem 0; }}
    footer {{ color:var(--muted); border-top:1px solid var(--line); margin-top:52px; padding:26px 0 42px; font-size:.9rem; }}
    @media (prefers-color-scheme:light) {{ :root {{ color-scheme:light; --bg:#f7f9fc;
      --card:#fff; --line:#dbe2ec; --text:#101620; --muted:#596779; --blue:#176fce; }} }}
    @media (max-width:760px) {{ .header-links>a {{ display:none; }} ul.links {{ columns:1; }}
      .observation-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .actions .cta {{ width:100%; text-align:center; }} }}
    @media (max-width:430px) {{ .observation-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header><a class="brand" href="/">MeteoLabX</a><div class="header-links">
    <a href="{_city_directory_path(language)}">{html.escape(language.t('cities'))}</a>
    <a href="{_directory_path(language)}">{html.escape(language.t('stations'))}</a>
    <a href="/forecast">AROME</a>
    <a href="/">{html.escape(language.t('panel'))}</a>
    {_language_navigation(language, alternates)}
  </div></header>
  <main>{body}</main>
  <footer>{html.escape(language.t('footer'))}</footer>
</body>
</html>
"""


def _page_shell(
    *,
    language: LanguageSpec,
    title: str,
    description: str,
    canonical_url: str,
    alternates: Mapping[str, str],
    body: str,
    structured_data: Sequence[object],
) -> str:
    safe_title = html.escape(title)
    safe_description = html.escape(description, quote=True)
    safe_canonical = html.escape(canonical_url, quote=True)
    json_ld = "".join(
        f'<script type="application/ld+json">{_json_script(item)}</script>'
        for item in structured_data
    )
    return f"""<!doctype html><html lang="{language.code}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title><meta name="description" content="{safe_description}">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{safe_canonical}">{_alternate_tags(alternates)}
<link rel="icon" href="/favicon-32x32.png" sizes="32x32" type="image/png">
<link rel="stylesheet" href="/seo-pages.css?v=1">
<meta property="og:type" content="website"><meta property="og:site_name" content="MeteoLabX">
<meta property="og:locale" content="{language.og_locale}"><meta property="og:url" content="{safe_canonical}">
<meta property="og:title" content="{safe_title}"><meta property="og:description" content="{safe_description}">
<meta property="og:image" content="{SITE_URL}/og-image.png?v=12"><meta name="twitter:card" content="summary_large_image">
{json_ld}</head><body><header><a class="brand" href="/">MeteoLabX</a><div class="header-links">
<a href="{_city_directory_path(language)}">{html.escape(language.t('cities'))}</a>
<a href="{_directory_path(language)}">{html.escape(language.t('stations'))}</a>
<a href="/forecast">AROME</a>
<a href="/">{html.escape(language.t('panel'))}</a>{_language_navigation(language, alternates)}</div></header>
<main>{body}</main><footer>{html.escape(language.t('footer'))}</footer></body></html>"""


def _provider_index_html(
    provider: str,
    stations: Sequence[StationPage],
    language: LanguageSpec,
) -> str:
    label = PROVIDER_LABELS.get(provider, provider)
    canonical = f"{SITE_URL}{_provider_path(provider, language)}"
    alternates = _provider_alternates(provider, stations)
    title = language.t("provider_title", provider=label)
    description = language.t(
        "provider_description", count=len(stations), provider=label
    )
    items = "".join(
        f'<li><a href="{html.escape(station.path(language), quote=True)}">{html.escape(station.name)}</a>'
        f'<span class="label">{html.escape(station.location_label(language) or station.station_id)}</span></li>'
        for station in stations
    )
    directory_path = _directory_path(language)
    body = f"""
    <div class="breadcrumbs"><a href="{directory_path}">{html.escape(language.t('stations'))}</a> / {html.escape(label)}</div>
    <h1>{html.escape(title.removesuffix(' | MeteoLabX'))}</h1>
    <p class="lede">{html.escape(language.t('provider_lede', count=len(stations), provider=label))}</p>
    <ul class="links">{items}</ul>
    """
    return _page_shell(
        language=language, title=title, description=description,
        canonical_url=canonical, alternates=alternates, body=body,
        structured_data=({"@context":"https://schema.org","@type":"CollectionPage","name":title.removesuffix(" | MeteoLabX"),"url":canonical,"inLanguage":language.code,"numberOfItems":len(stations)},),
    )


def _directory_html(
    by_provider: Mapping[str, Sequence[StationPage]],
    language: LanguageSpec,
) -> str:
    total = sum(len(stations) for stations in by_provider.values())
    canonical = f"{SITE_URL}{_directory_path(language)}"
    alternates = _directory_alternates()
    cards = "".join(
        f'<article class="card"><span class="label">{html.escape(language.t("public_network"))}</span>'
        f'<h2><a href="{_provider_path(provider, language)}">{html.escape(PROVIDER_LABELS.get(provider, provider))}</a></h2>'
        f'<p>{html.escape(language.t("stations_indexed", count=len(stations)))}</p></article>'
        for provider, stations in by_provider.items()
    )
    title = language.t("directory_title")
    body = f"""
    <div class="breadcrumbs">{html.escape(language.t('stations'))}</div>
    <h1>{html.escape(language.t('directory_heading'))}</h1>
    <p class="lede">{html.escape(language.t('directory_lede', count=total))}</p>
    <div class="grid">{cards}</div>
    """
    return _page_shell(
        language=language, title=title,
        description=language.t("directory_description", count=total),
        canonical_url=canonical, alternates=alternates, body=body,
        structured_data=({"@context":"https://schema.org","@type":"CollectionPage","name":language.t("directory_heading"),"url":canonical,"inLanguage":language.code,"numberOfItems":total},),
    )


def _city_html(
    city: CityPage,
    matches: Sequence[tuple[StationPage, float]],
    language: LanguageSpec,
) -> str:
    representative, representative_distance = matches[0]
    provider_names = sorted(
        {station.provider_label for station, _distance in matches},
        key=str.casefold,
    )
    networks = ", ".join(provider_names)
    canonical = city.canonical_url(language)
    alternates = _city_alternates(city, matches)
    city_directory_path = _city_directory_path(language)
    title = language.t("city_title", city=city.name)
    description = language.t(
        "city_description",
        city=city.name,
        count=len(matches),
        networks=networks,
    )
    station_items = "".join(
        f'<li><a href="{html.escape(station.path(language), quote=True)}">{html.escape(station.name)}</a> '
        f'<span class="label">{html.escape(language.t("distance_to_center", distance=f"{distance:.1f}"))} · {html.escape(station.provider_label)}</span></li>'
        for station, distance in matches
    )
    body = f"""
    <div class="breadcrumbs"><a href="{city_directory_path}">{html.escape(language.t('cities'))}</a> / {html.escape(city.name)}</div>
    <h1>{html.escape(language.t('city_heading', city=city.name))}</h1>
    <p class="lede">{html.escape(language.t('city_lede', city=city.name, count=len(matches)))}</p>
    <div class="grid">
      <div class="card"><span class="label">{html.escape(language.t('representative_station'))}</span><span class="value"><a href="{html.escape(representative.path(language), quote=True)}">{html.escape(representative.name)}</a></span><span class="label">{html.escape(language.t('distance_to_center', distance=f'{representative_distance:.1f}'))}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('station_count'))}</span><span class="value">{len(matches)}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('networks'))}</span><span class="value">{html.escape(networks)}</span></div>
    </div>
    <h2>{html.escape(language.t('city_stations', city=city.name))}</h2>
    <ul class="links">{station_items}</ul>
    <section aria-labelledby="observations"><h2 id="observations">{html.escape(language.t('observations_title'))}</h2>
      <p>{html.escape(language.t('observations_text'))}</p></section>
    """
    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "inLanguage": language.code,
        "itemListElement": [
            {"@type":"ListItem","position":1,"name":language.t("cities"),"item":f"{SITE_URL}{city_directory_path}"},
            {"@type":"ListItem","position":2,"name":city.name,"item":canonical},
        ],
    }
    collection = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": language.t("city_heading", city=city.name),
        "description": description,
        "url": canonical,
        "inLanguage": language.code,
        "numberOfItems": len(matches),
        "about": {
            "@type": "Place",
            "name": city.name,
            "geo": {
                "@type": "GeoCoordinates",
                "latitude": city.latitude,
                "longitude": city.longitude,
            },
        },
    }
    return _page_shell(
        language=language,
        title=title,
        description=description,
        canonical_url=canonical,
        alternates=alternates,
        body=body,
        structured_data=(breadcrumb, collection),
    )


def _city_directory_html(
    city_matches: Mapping[CityPage, Sequence[tuple[StationPage, float]]],
    language: LanguageSpec,
) -> str:
    canonical = f"{SITE_URL}{_city_directory_path(language)}"
    alternates = _city_directory_alternates(city_matches)
    cards = "".join(
        f'<article class="card"><span class="label">{html.escape(language.t("city_indexed", count=len(matches)))}</span>'
        f'<h2><a href="{html.escape(city.path(language), quote=True)}">{html.escape(city.name)}</a></h2></article>'
        for city, matches in city_matches.items()
    )
    title = language.t("city_directory_title")
    body = f"""
    <div class="breadcrumbs">{html.escape(language.t('cities'))}</div>
    <h1>{html.escape(language.t('city_directory_heading'))}</h1>
    <p class="lede">{html.escape(language.t('city_directory_lede'))}</p>
    <div class="grid">{cards}</div>
    """
    return _page_shell(
        language=language,
        title=title,
        description=language.t("city_directory_description", count=len(city_matches)),
        canonical_url=canonical,
        alternates=alternates,
        body=body,
        structured_data=({
            "@context":"https://schema.org",
            "@type":"CollectionPage",
            "name":language.t("city_directory_heading"),
            "url":canonical,
            "inLanguage":language.code,
            "numberOfItems":len(city_matches),
        },),
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _xml_alternate_links(alternates: Mapping[str, str]) -> str:
    links = [
        f'<xhtml:link rel="alternate" hreflang="{code}" href="{xml_escape(url)}" />'
        for code, url in alternates.items()
    ]
    links.append(
        '<xhtml:link rel="alternate" hreflang="x-default" '
        f'href="{xml_escape(alternates[DEFAULT_LANGUAGE])}" />'
    )
    return "".join(links)


def _sitemap(alternate_groups: Sequence[Mapping[str, str]]) -> str:
    # Sin las URLs sueltas del sitio: la portada y el visor los publica
    # sitemap-static.xml, en el frontend.
    entries: list[str] = []
    for alternates in alternate_groups:
        alternate_links = _xml_alternate_links(alternates)
        for url in alternates.values():
            entries.append(
                f"  <url><loc>{xml_escape(url)}</loc>{alternate_links}</url>"
            )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )


def _write_sitemaps(
    output: Path,
    alternate_groups: Sequence[Mapping[str, str]],
) -> int:
    """Sitemap de los directorios, los índices de red y las ciudades.

    Ya no lleva fichas de estación: esas las publica el frontend nuevo en
    ``/sitemap-observation-N.xml``. Lo que queda cabe de sobra en un solo
    fichero, así que se conserva el formato con alternates ``hreflang``, que
    es el que Google lleva leyendo desde el principio.

    El nombre tampoco es ``sitemap.xml``: ese lo sirve ahora el frontend, y su
    índice enlaza este fichero para que estas páginas no se caigan del mapa.
    """
    for stale in (*output.glob("sitemap-*.xml"), output / "sitemap.xml"):
        if stale.is_file():
            stale.unlink()
    _write_text(output / DIRECTORY_SITEMAP_NAME, _sitemap(alternate_groups))
    return sum(len(group) for group in alternate_groups)


def build_pages(
    *,
    database: Path,
    output: Path,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / "seo-pages.css", SEO_STYLESHEET + "\n")
    stations = load_stations(database, providers)
    by_provider = {
        provider: [station for station in stations if station.provider == provider]
        for provider in providers
    }
    by_provider = {provider: rows for provider, rows in by_provider.items() if rows}
    city_matches = {
        city: matches
        for city in CITIES
        if (matches := _stations_for_city(city, stations))
    }

    for language in LANGUAGES.values():
        localized_by_provider = {
            provider: [
                station
                for station in provider_stations
                if language.code in station.language_codes
            ]
            for provider, provider_stations in by_provider.items()
        }
        localized_by_provider = {
            provider: rows for provider, rows in localized_by_provider.items() if rows
        }
        localized_city_matches = {
            city: [
                item for item in matches if language.code in item[0].language_codes
            ]
            for city, matches in city_matches.items()
        }
        localized_city_matches = {
            city: matches for city, matches in localized_city_matches.items() if matches
        }
        generated_root = output / language.code / language.directory_slug
        if generated_root.exists():
            shutil.rmtree(generated_root)
        generated_root.mkdir(parents=True, exist_ok=True)
        city_root = output / language.code / language.city_slug
        if city_root.exists():
            shutil.rmtree(city_root)
        city_root.mkdir(parents=True, exist_ok=True)
        _write_text(
            output / language.code / f"{language.directory_slug}.html",
            _directory_html(localized_by_provider, language),
        )
        if localized_city_matches:
            _write_text(
                output / language.code / f"{language.city_slug}.html",
                _city_directory_html(localized_city_matches, language),
            )
            for city, matches in localized_city_matches.items():
                _write_text(
                    city_root / f"{city.slug}.html",
                    _city_html(city, matches, language),
                )
        for provider, provider_stations in localized_by_provider.items():
            # Solo el índice de la red. Las fichas las sirve el frontend
            # SvelteKit desde /{idioma}/observation/{slug}, con los datos ya
            # renderizados en vez de un iframe que copia el DOM de Streamlit.
            _write_text(
                generated_root / f"{slugify(provider)}.html",
                _provider_index_html(provider, provider_stations, language),
            )

    # Las URLs de las fichas ya no salen de aquí: las publica el sitemap del
    # frontend, que las genera desde el catálogo indexable del backend.
    alternate_groups: list[Mapping[str, str]] = [_directory_alternates()]
    alternate_groups.extend(
        _provider_alternates(provider, provider_stations)
        for provider, provider_stations in by_provider.items()
    )
    if city_matches:
        alternate_groups.append(_city_directory_alternates(city_matches))
        alternate_groups.extend(
            _city_alternates(city, matches) for city, matches in city_matches.items()
        )
    sitemap_url_count = _write_sitemaps(output, alternate_groups)
    localized_pages = sum(len(group) for group in alternate_groups)
    return {
        "stations": len(stations),
        "providers": len(by_provider),
        "cities": len(city_matches),
        "languages": len(LANGUAGES),
        "pages": localized_pages,
        "sitemap_urls": sitemap_url_count,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/stations.sqlite"))
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Directorio static de salida; por defecto, el del paquete Streamlit.",
    )
    parser.add_argument(
        "--providers",
        default=os.getenv("METEOLABX_SEO_PROVIDERS", ""),
        help="Proveedores separados por comas.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output = (args.output or _default_output_dir()).resolve()
    try:
        summary = build_pages(
            database=args.database.resolve(),
            output=output,
            providers=_parse_providers(args.providers),
        )
    except Exception as exc:  # noqa: BLE001 - mensaje claro para el deploy
        print(f"[build_seo_pages] ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "[build_seo_pages] "
        f"{summary['pages']} páginas ({summary['stations']} estaciones, "
        f"{summary['providers']} redes, {summary['cities']} ciudades, "
        f"{summary['languages']} idiomas) en {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

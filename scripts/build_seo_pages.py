#!/usr/bin/env python3
"""Genera páginas HTML indexables y multilingües para estaciones públicas.

Las páginas se escriben en el directorio estático del paquete Streamlit para
que rutas como ``/en/weather-stations/aemet/barcelona-drassanes-0201x.html``
se sirvan como HTML completo, sin ejecutar JavaScript ni abrir una sesión de
Streamlit. Cada página publica canonical propio, alternates ``hreflang`` para
los seis idiomas de la aplicación y datos estructurados localizados.

El catálogo incluye redes públicas españolas e internacionales. Las estaciones
marcadas como inactivas se excluyen para evitar páginas de poco valor, al igual
que las redes IEM, Windy y Netatmo, que no forman parte del catálogo SEO.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
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


SITE_URL = "https://www.meteolabx.com"
STATIC_SITEMAP_URLS = (f"{SITE_URL}/", f"{SITE_URL}/forecast")
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
SITEMAP_URL_LIMIT = 50_000
SEO_STYLESHEET = """
:root{color-scheme:dark;--bg:#0e1117;--card:#171b24;--line:#2a3240;--text:#f5f7fb;--muted:#a9b4c4;--blue:#5da8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.58}a{color:var(--blue)}
header,main,footer{width:min(1040px,calc(100% - 32px));margin:auto}header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:24px 0}.brand{color:var(--text);text-decoration:none;font-size:1.25rem;font-weight:800}.header-links{display:flex;align-items:center;gap:18px}.header-links>a{text-decoration:none}.languages{display:flex;gap:3px;padding-left:8px;border-left:1px solid var(--line)}.languages a{padding:2px 5px;color:var(--muted);font-size:.75rem;text-decoration:none}.languages a[aria-current=page]{color:var(--text);font-weight:800}
.breadcrumbs{color:var(--muted);font-size:.9rem;margin:18px 0}.breadcrumbs a{color:var(--muted)}h1{line-height:1.14;font-size:clamp(2rem,5vw,3.25rem);margin:.35em 0}h2{margin-top:2rem;line-height:1.25}.lede{color:var(--muted);font-size:1.12rem;max-width:760px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:24px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}.label{display:block;color:var(--muted);font-size:.82rem;margin-bottom:3px}.value{font-weight:700}.cta{display:inline-block;padding:12px 18px;border-radius:12px;background:#2384ff;color:#fff;font-weight:750;text-decoration:none;margin:10px 0}.cta.secondary{background:transparent;color:var(--blue);border:1px solid var(--line)}.actions{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 8px}
.live-panel{margin:24px 0 34px}.observation-status{color:var(--muted);margin:12px 0;min-height:1.4em}.observation-status.error{color:#ef7373}.observation-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:18px 0}.observation-card{min-height:132px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:17px}.observation-card .obs-label{color:var(--muted);font-size:.78rem;font-weight:750;letter-spacing:.035em;text-transform:uppercase}.observation-card .obs-value{display:block;margin-top:7px;font-size:1.65rem;line-height:1.15;font-weight:800}.observation-card .obs-detail{color:var(--muted);font-size:.84rem;line-height:1.38;margin-top:9px}.observation-card .obs-extremes{color:var(--text);font-size:.8rem;font-weight:650;margin-top:8px}.observation-loader{position:fixed;left:-10000px;top:0;width:1280px;height:1200px;border:0;opacity:.01;pointer-events:none}.fallback{color:var(--muted);font-size:.9rem}ul.links{padding-left:1.2rem;columns:2;column-gap:32px}ul.links li{break-inside:avoid;margin:.5rem 0}footer{color:var(--muted);border-top:1px solid var(--line);margin-top:52px;padding:26px 0 42px;font-size:.9rem}
@media(prefers-color-scheme:light){:root{color-scheme:light;--bg:#f7f9fc;--card:#fff;--line:#dbe2ec;--text:#101620;--muted:#596779;--blue:#176fce}}@media(max-width:760px){.header-links>a{display:none}ul.links{columns:1}.observation-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.actions .cta{width:100%;text-align:center}}@media(max-width:430px){.observation-grid{grid-template-columns:1fr}}
""".strip()
SEO_OBSERVATION_SCRIPT = r"""
document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('.live-panel').forEach(section=>{
const pageView={provider:section.dataset.seoProvider||'',station_id:section.dataset.seoStationId||'',name:section.dataset.seoStationName||'',language:section.dataset.seoLanguage||''};
if(pageView.provider&&pageView.station_id&&!section.dataset.seoViewSent){section.dataset.seoViewSent='1';fetch('/v1/stats/seo-view',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pageView),keepalive:true}).catch(()=>{})}
const frame=section.querySelector('.observation-loader'),status=section.querySelector('[data-observation-status]'),slots=Array.from(section.querySelectorAll('[data-observation-slot]'));
const clean=value=>String(value||'').replace(/\s+/g,' ').trim();
const syncObservation=()=>{try{const doc=frame.contentDocument;if(!doc)return false;const grid=Array.from(doc.querySelectorAll('.grid.grid-3')).find(item=>item.querySelectorAll(':scope > .card .card-value').length>=6);if(!grid){const alert=doc.querySelector('[data-testid="stAlert"]');if(alert){status.textContent=clean(alert.innerText).slice(0,260);status.classList.add('error')}return false}
Array.from(grid.querySelectorAll(':scope > .card')).slice(0,6).forEach((source,index)=>{const target=slots[index];if(!target)return;const value=clean(source.querySelector('.card-value')?.innerText),detail=clean(source.querySelector('.subtitle')?.innerText),unit=clean(source.querySelector('.unit')?.innerText),side=clean(source.querySelector('.side-col')?.innerText),extremes=side.replace(',','.').match(/-?\d+(?:\.\d+)?/g)||[],current=value.replace(',','.').match(/-?\d+(?:\.\d+)?/);if(current&&extremes.length){const number=Number(current[0]);if(Number.isFinite(number)&&number>Number(extremes[0]))extremes[0]=current[0];if(extremes.length>=2&&Number.isFinite(number)&&number<Number(extremes[1]))extremes[1]=current[0]}target.querySelector('.obs-value').textContent=value&&!value.startsWith('—')?value:'—';target.querySelector('.obs-detail').textContent=detail;const withUnit=number=>unit?`${number} ${unit}`:number;target.querySelector('.obs-extremes').textContent=extremes.length>=2?`${section.dataset.maximumLabel} ${withUnit(extremes[0])} · ${section.dataset.minimumLabel} ${withUnit(extremes[1])}`:extremes.length===1?`${section.dataset.maximumLabel} ${withUnit(extremes[0])}`:''});
const meta=doc.querySelector('.meta .mlbx-live-age')?.closest('.meta');status.textContent=clean(meta?.innerText)||section.dataset.updatedLabel;status.classList.remove('error');return true}catch(error){return false}};
frame.addEventListener('load',syncObservation);let attempts=0;const timer=window.setInterval(()=>{attempts+=1;if(syncObservation()||attempts>=120)window.clearInterval(timer)},250);window.setInterval(syncObservation,60000);syncObservation()}));
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
        return (
            f"/{language.code}/{language.directory_slug}/"
            f"{self.provider_slug}/{self.url_slug}.html"
        )

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

    def app_url(
        self,
        language: LanguageSpec,
        *,
        tab: str = "observacion",
        from_seo: bool = False,
    ) -> str:
        # ``lang`` se consume una sola vez por la app y luego se limpia de la
        # barra de direcciones; ``e`` mantiene el deep link existente.
        params = {
                "e": f"{self.provider}~{slugify(self.name)}",
                "sid": self.station_id,
                "tab": tab,
                "lang": language.code,
            }
        if from_seo:
            params["from"] = "seo"
        query = urlencode(params)
        return f"{SITE_URL}/?{query}"

    def embedded_app_path(self, language: LanguageSpec) -> str:
        query = urlencode(
            {
                "e": f"{self.provider}~{slugify(self.name)}",
                "sid": self.station_id,
                "tab": "observacion",
                "lang": language.code,
                "embed": "seo",
            }
        )
        return f"/?{query}"


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


def _streamlit_static_dir() -> Path:
    import streamlit

    return Path(streamlit.__file__).resolve().parent / "static"


def _unique_url_slugs(rows: Sequence[sqlite3.Row]) -> dict[int, str]:
    """Construye slugs legibles y estables, resolviendo colisiones raras."""
    candidates: dict[int, str] = {}
    owners: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        name_slug = slugify(row["name"])[:88] or "station"
        identity_slug = slugify(row["station_id"])[:36] or str(row["station_pk"])
        candidate = f"{name_slug}-{identity_slug}"
        owners.setdefault((str(row["provider"]), candidate), []).append(row)

    for (provider, candidate), matches in owners.items():
        if len(matches) == 1:
            candidates[int(matches[0]["station_pk"])] = candidate
            continue
        for row in matches:
            identity = f"{provider}|{row['network_code']}|{row['station_id']}".encode()
            suffix = hashlib.sha1(identity).hexdigest()[:8]
            candidates[int(row["station_pk"])] = f"{candidate}-{suffix}"
    return candidates


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

    url_slugs = _unique_url_slugs(rows)
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


def _nearest_stations(
    station: StationPage,
    stations: Sequence[StationPage],
    *,
    limit: int = 6,
) -> list[tuple[StationPage, float]]:
    if station.latitude is None or station.longitude is None:
        return []
    results: list[tuple[StationPage, float]] = []
    for candidate in stations:
        if candidate.station_pk == station.station_pk:
            continue
        if candidate.latitude is None or candidate.longitude is None:
            continue
        distance = _distance_km(
            station.latitude,
            station.longitude,
            candidate.latitude,
            candidate.longitude,
        )
        results.append((candidate, distance))
    results.sort(key=lambda item: (item[1], item[0].name.casefold()))
    return results[:limit]


def _nearest_station_map(
    stations: Sequence[StationPage],
    *,
    limit: int = 6,
) -> dict[int, list[tuple[StationPage, float]]]:
    """Find nearby stations with a spatial grid instead of an O(n²) scan."""
    buckets: dict[tuple[int, int], list[StationPage]] = defaultdict(list)
    for station in stations:
        if station.latitude is None or station.longitude is None:
            continue
        buckets[(math.floor(station.latitude), math.floor(station.longitude))].append(station)

    result: dict[int, list[tuple[StationPage, float]]] = {}
    for station in stations:
        if station.latitude is None or station.longitude is None:
            result[station.station_pk] = []
            continue
        origin_lat = math.floor(station.latitude)
        origin_lon = math.floor(station.longitude)
        candidates: dict[int, StationPage] = {}
        for ring in range(0, 4):
            for lat_cell in range(origin_lat - ring, origin_lat + ring + 1):
                if lat_cell < -90 or lat_cell > 90:
                    continue
                for lon_cell in range(origin_lon - ring, origin_lon + ring + 1):
                    if ring and (
                        lat_cell not in {origin_lat - ring, origin_lat + ring}
                        and lon_cell not in {origin_lon - ring, origin_lon + ring}
                    ):
                        continue
                    wrapped_lon = ((lon_cell + 180) % 360) - 180
                    for candidate in buckets.get((lat_cell, wrapped_lon), ()):
                        if candidate.station_pk != station.station_pk:
                            candidates[candidate.station_pk] = candidate
            if len(candidates) >= limit:
                break

        distances = [
            (
                candidate,
                _distance_km(
                    station.latitude,
                    station.longitude,
                    candidate.latitude,
                    candidate.longitude,
                ),
            )
            for candidate in candidates.values()
            if candidate.latitude is not None and candidate.longitude is not None
        ]
        distances.sort(key=lambda item: (item[1], item[0].name.casefold()))
        result[station.station_pk] = distances[:limit]
    return result


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
{json_ld}<script src="/seo-observation.js?v=1" defer></script></head><body><header><a class="brand" href="/">MeteoLabX</a><div class="header-links">
<a href="{_city_directory_path(language)}">{html.escape(language.t('cities'))}</a>
<a href="{_directory_path(language)}">{html.escape(language.t('stations'))}</a>
<a href="/forecast">AROME</a>
<a href="/">{html.escape(language.t('panel'))}</a>{_language_navigation(language, alternates)}</div></header>
<main>{body}</main><footer>{html.escape(language.t('footer'))}</footer></body></html>"""


def _station_html(
    station: StationPage,
    language: LanguageSpec,
    nearby: Sequence[tuple[StationPage, float]],
) -> str:
    search_name = _station_search_name(station, language)
    location = station.location_label(language) or language.t("fallback_location")
    title = language.t(
        "station_title", name=search_name, provider=station.provider_label
    )
    description = language.t(
        "station_description",
        name=search_name,
        provider=station.provider_label,
        location=location,
    )
    coordinates = f"{station.latitude:.5f}, {station.longitude:.5f}"
    elevation = (
        f"{station.elevation_m:.0f} m"
        if station.elevation_m is not None
        else language.t("not_available")
    )
    sensors = ", ".join(language.sensors[key] for key in station.sensor_keys)
    sensors = sensors or language.t("sensor_unknown")
    history = language.t("available" if station.has_historical else "current_only")
    panel_url = station.app_url(language, from_seo=True)
    embedded_url = station.embedded_app_path(language)
    observation_labels = (
        language.t("obs_temperature"),
        language.t("obs_humidity"),
        language.t("obs_dew_point"),
        language.t("obs_pressure"),
        language.t("obs_wind"),
        language.t("obs_precipitation"),
    )
    observation_cards = "".join(
        f"""<div class="observation-card" data-observation-slot="{index}">
          <span class="obs-label">{html.escape(label)}</span>
          <span class="obs-value">—</span>
          <div class="obs-detail"></div>
          <div class="obs-extremes"></div>
        </div>"""
        for index, label in enumerate(observation_labels)
    )
    history_section = ""
    if station.has_historical:
        history_section = f"""
    <section aria-labelledby="history"><h2 id="history">{html.escape(language.t('history_title', name=search_name))}</h2>
      <p>{html.escape(language.t('history_text'))}</p>
      <a class="cta" href="{html.escape(station.app_url(language, tab='historico'), quote=True)}">{html.escape(language.t('history_cta'))}</a></section>
        """
    nearby_items = "".join(
        f'<li><a href="{html.escape(other.path(language), quote=True)}">{html.escape(other.name)}</a> '
        f'<span class="label">{distance:.1f} km · {html.escape(other.provider_label)}</span></li>'
        for other, distance in nearby
    )
    directory_path = _directory_path(language)
    provider_path = _provider_path(station.provider, language)
    canonical = station.canonical_url(language)
    alternates = _station_alternates(station)
    body = f"""
    <div class="breadcrumbs"><a href="{directory_path}">{html.escape(language.t('stations'))}</a> / <a href="{provider_path}">{html.escape(station.provider_label)}</a> / {html.escape(station.name)}</div>
    <p class="label">{html.escape(language.t('station_type'))} · {html.escape(station.provider_label)}</p>
    <h1>{html.escape(station.name)}</h1>
    <p class="lede">{html.escape(language.t('station_lede', name=search_name, provider=station.provider_label, location=location))}</p>
    <section class="live-panel" aria-label="{html.escape(language.t('current_title', name=search_name), quote=True)}"
      data-seo-provider="{html.escape(station.provider, quote=True)}"
      data-seo-station-id="{html.escape(station.station_id, quote=True)}"
      data-seo-station-name="{html.escape(station.name, quote=True)}"
      data-seo-language="{html.escape(language.code, quote=True)}"
      data-maximum-label="{html.escape(language.t('maximum'), quote=True)}"
      data-minimum-label="{html.escape(language.t('minimum'), quote=True)}"
      data-updated-label="{html.escape(language.t('observation_updated'), quote=True)}">
      <div class="observation-status" data-observation-status>{html.escape(language.t('live_panel_loading'))}</div>
      <div class="observation-grid" aria-live="polite">
        {observation_cards}
      </div>
      <iframe class="observation-loader" src="{html.escape(embedded_url, quote=True)}"
        title="" tabindex="-1" aria-hidden="true" loading="eager"
        referrerpolicy="same-origin"></iframe>
      <script>
      (() => {{
        const section = document.currentScript.closest('.live-panel');
        const frame = section.querySelector('.observation-loader');
        const status = section.querySelector('[data-observation-status]');
        const slots = Array.from(section.querySelectorAll('[data-observation-slot]'));
        const maximumLabel = {json.dumps(language.t('maximum'), ensure_ascii=False)};
        const minimumLabel = {json.dumps(language.t('minimum'), ensure_ascii=False)};

        const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();

        const syncObservation = () => {{
          try {{
            const doc = frame.contentDocument;
            if (!doc) return false;
            const primaryGrid = Array.from(doc.querySelectorAll('.grid.grid-3')).find(
              grid => grid.querySelectorAll(':scope > .card .card-value').length >= 6
            );
            if (!primaryGrid) {{
              const alert = doc.querySelector('[data-testid="stAlert"]');
              if (alert) {{
                status.textContent = clean(alert.innerText).slice(0, 260);
                status.classList.add('error');
              }}
              return false;
            }}

            const sourceCards = Array.from(primaryGrid.querySelectorAll(':scope > .card')).slice(0, 6);
            sourceCards.forEach((source, index) => {{
              const target = slots[index];
              if (!target) return;
              const value = clean(source.querySelector('.card-value')?.innerText);
              const detail = clean(source.querySelector('.subtitle')?.innerText);
              const unit = clean(source.querySelector('.unit')?.innerText);
              const side = clean(source.querySelector('.side-col')?.innerText);
              const extremes = side.replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/g) || [];
              const currentMatch = value.replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
              if (currentMatch && extremes.length) {{
                const currentNumber = Number(currentMatch[0]);
                if (Number.isFinite(currentNumber) && currentNumber > Number(extremes[0])) {{
                  extremes[0] = currentMatch[0];
                }}
                if (extremes.length >= 2 && Number.isFinite(currentNumber) && currentNumber < Number(extremes[1])) {{
                  extremes[1] = currentMatch[0];
                }}
              }}
              target.querySelector('.obs-value').textContent = value && !value.startsWith('—') ? value : '—';
              target.querySelector('.obs-detail').textContent = detail;
              const withUnit = number => unit ? `${{number}} ${{unit}}` : number;
              let extremesText = '';
              if (extremes.length >= 2) {{
                extremesText = `${{maximumLabel}} ${{withUnit(extremes[0])}} · ${{minimumLabel}} ${{withUnit(extremes[1])}}`;
              }} else if (extremes.length === 1) {{
                extremesText = `${{maximumLabel}} ${{withUnit(extremes[0])}}`;
              }}
              target.querySelector('.obs-extremes').textContent = extremesText;
            }});

            const meta = doc.querySelector('.meta .mlbx-live-age')?.closest('.meta');
            status.textContent = clean(meta?.innerText) || {json.dumps(language.t('observation_updated'), ensure_ascii=False)};
            status.classList.remove('error');

            return true;
          }} catch (error) {{
            return false;
          }}
        }};

        frame.addEventListener('load', syncObservation);
        let attempts = 0;
        const initialTimer = window.setInterval(() => {{
          attempts += 1;
          if (syncObservation() || attempts >= 120) window.clearInterval(initialTimer);
        }}, 250);
        window.setInterval(syncObservation, 60000);
        syncObservation();
      }})();
      </script>
      <div class="actions">
        <a class="cta" href="{html.escape(panel_url, quote=True)}" target="_top">{html.escape(language.t('open_full_panel'))}</a>
      </div>
      <noscript><p><a href="{html.escape(panel_url, quote=True)}">{html.escape(language.t('open_full_panel'))}</a></p></noscript>
    </section>
    <section aria-labelledby="profile"><h2 id="profile">{html.escape(language.t('station_sheet'))}</h2><div class="grid">
      <div class="card"><span class="label">{html.escape(language.t('network'))}</span><span class="value">{html.escape(station.provider_label)}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('identifier'))}</span><span class="value">{html.escape(station.station_id)}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('location'))}</span><span class="value">{html.escape(location)}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('altitude'))}</span><span class="value">{elevation}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('coordinates'))}</span><span class="value">{coordinates}</span></div>
      <div class="card"><span class="label">{html.escape(language.t('historical'))}</span><span class="value">{html.escape(history)}</span></div>
    </div></section>
    <section aria-labelledby="variables"><h2 id="variables">{html.escape(language.t('variables'))}</h2>
      <p>{html.escape(language.t('sensor_text', sensors=sensors))}</p></section>
    {history_section}
    <section aria-labelledby="nearby"><h2 id="nearby">{html.escape(language.t('nearby'))}</h2><ul class="links">{nearby_items}</ul></section>
    """
    body = re.sub(r"\s*<script>\s*\(\(\) => \{.*?</script>", "", body, flags=re.DOTALL)
    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "inLanguage": language.code,
        "itemListElement": [
            {"@type":"ListItem","position":1,"name":language.t("stations"),"item":f"{SITE_URL}{directory_path}"},
            {"@type":"ListItem","position":2,"name":station.provider_label,"item":f"{SITE_URL}{provider_path}"},
            {"@type":"ListItem","position":3,"name":station.name,"item":canonical},
        ],
    }
    place: dict[str, object] = {
        "@context":"https://schema.org", "@type":"Place",
        "name":f"{language.t('station_type')} {station.name}",
        "url":canonical, "identifier":station.station_id, "inLanguage":language.code,
        "geo": {
            "@type":"GeoCoordinates", "latitude":station.latitude,
            "longitude":station.longitude,
            **({"elevation":station.elevation_m} if station.elevation_m is not None else {}),
        },
    }
    if search_name != station.name:
        place["alternateName"] = search_name
    return _page_shell(
        language=language, title=title, description=description,
        canonical_url=canonical, alternates=alternates, body=body,
        structured_data=(breadcrumb, place),
    )


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
    entries = [
        f"  <url><loc>{xml_escape(url)}</loc></url>" for url in STATIC_SITEMAP_URLS
    ]
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


def _plain_sitemap(urls: Sequence[str]) -> str:
    entries = "\n".join(
        f"  <url><loc>{xml_escape(url)}</loc></url>" for url in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )


def _sitemap_index(names: Sequence[str]) -> str:
    entries = "\n".join(
        f"  <sitemap><loc>{SITE_URL}/{xml_escape(name)}</loc></sitemap>"
        for name in names
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</sitemapindex>\n"
    )


def _write_sitemaps(
    output: Path,
    alternate_groups: Sequence[Mapping[str, str]],
) -> int:
    sitemap_url_count = len(STATIC_SITEMAP_URLS) + sum(
        len(group) for group in alternate_groups
    )
    for stale in output.glob("sitemap-*.xml"):
        stale.unlink()
    if sitemap_url_count <= SITEMAP_URL_LIMIT:
        _write_text(output / "sitemap.xml", _sitemap(alternate_groups))
        return sitemap_url_count

    urls = list(STATIC_SITEMAP_URLS)
    urls.extend(url for group in alternate_groups for url in group.values())
    names: list[str] = []
    for index, offset in enumerate(range(0, len(urls), SITEMAP_URL_LIMIT), start=1):
        name = f"sitemap-{index}.xml"
        names.append(name)
        _write_text(
            output / name,
            _plain_sitemap(urls[offset : offset + SITEMAP_URL_LIMIT]),
        )
    _write_text(output / "sitemap.xml", _sitemap_index(names))
    return sitemap_url_count


def build_pages(
    *,
    database: Path,
    output: Path,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / "seo-pages.css", SEO_STYLESHEET + "\n")
    _write_text(output / "seo-observation.js", SEO_OBSERVATION_SCRIPT + "\n")
    stations = load_stations(database, providers)
    by_provider = {
        provider: [station for station in stations if station.provider == provider]
        for provider in providers
    }
    by_provider = {provider: rows for provider, rows in by_provider.items() if rows}
    nearby_by_station = _nearest_station_map(stations)
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
            provider_root = generated_root / slugify(provider)
            _write_text(
                generated_root / f"{slugify(provider)}.html",
                _provider_index_html(provider, provider_stations, language),
            )
            for station in provider_stations:
                _write_text(
                    provider_root / f"{station.url_slug}.html",
                    _station_html(
                        station,
                        language,
                        [
                            item
                            for item in nearby_by_station[station.station_pk]
                            if language.code in item[0].language_codes
                        ],
                    ),
                )

    alternate_groups: list[Mapping[str, str]] = [_directory_alternates()]
    alternate_groups.extend(
        _provider_alternates(provider, provider_stations)
        for provider, provider_stations in by_provider.items()
    )
    alternate_groups.extend(_station_alternates(station) for station in stations)
    if city_matches:
        alternate_groups.append(_city_directory_alternates(city_matches))
        alternate_groups.extend(
            _city_alternates(city, matches) for city, matches in city_matches.items()
        )
    sitemap_url_count = _write_sitemaps(output, alternate_groups)
    _write_text(
        output / "robots.txt",
        "User-agent: *\nAllow: /\n\nSitemap: https://www.meteolabx.com/sitemap.xml\n",
    )
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
    output = (args.output or _streamlit_static_dir()).resolve()
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

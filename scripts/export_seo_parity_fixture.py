#!/usr/bin/env python3
"""Congela la salida del generador SEO para poder compararla con el frontend.

El frontend SvelteKit reproduce en JavaScript los títulos, descripciones y
datos estructurados que hoy escribe ``scripts/build_seo_pages.py``. Que
ambos digan lo mismo no puede quedar en la confianza: este script vuelca
casos representativos —España, Francia, Estados Unidos, Antártida, nombres
en mayúsculas, alias localizados— y ``web/tests/seo-meta.test.mjs`` verifica
que el JavaScript los reproduce carácter a carácter.

    python scripts/export_seo_parity_fixture.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_seo_pages import (
    SITE_URL,
    _station_alternates,
    StationPage,
    _station_search_name,
    _display_name,
)
from scripts.seo_pages_i18n import LANGUAGES

DEFAULT_OUTPUT = REPO_ROOT / "web" / "tests" / "fixtures" / "seo-parity.generated.json"

# Cada caso ejercita una rama distinta del generador: país por proveedor
# frente a país del catálogo, nombre en mayúsculas, alias de búsqueda
# localizado, altitud ausente y localidad/región presentes.
CASES: tuple[dict, ...] = (
    dict(
        station_pk=1, provider="AEMET", network_code="", station_id="0201X",
        name="BARCELONA  DRASSANES", latitude=41.374998, longitude=2.173886,
        elevation_m=11.0, timezone="Europe/Madrid", country="ES",
        region="", locality="", has_historical=True,
        sensor_keys=("thermometer", "hygrometer", "barometer"),
        url_slug="barcelona-drassanes-0201x",
    ),
    dict(
        station_pk=2, provider="METEOCAT", network_code="1", station_id="D5",
        name="Barcelona - Observatori Fabra", latitude=41.4184, longitude=2.1239,
        elevation_m=411.0, timezone="Europe/Madrid", country="ES",
        region="Catalunya", locality="Barcelona", has_historical=True,
        sensor_keys=("thermometer", "anemometer"),
        url_slug="barcelona-observatori-fabra-d5",
    ),
    dict(
        station_pk=3, provider="METEOFRANCE", network_code="", station_id="75107005",
        name="TOUR EIFFEL", latitude=48.8584, longitude=2.2945,
        elevation_m=None, timezone="Europe/Paris", country="FR",
        region="Île-de-France", locality="Paris", has_historical=False,
        sensor_keys=(), url_slug="tour-eiffel-75107005",
    ),
    dict(
        station_pk=4, provider="NWS", network_code="", station_id="KNYC",
        name="Central Park", latitude=40.7789, longitude=-73.9692,
        elevation_m=48.0, timezone="America/New_York", country="US",
        region="New York", locality="New York", has_historical=True,
        sensor_keys=("thermometer",), url_slug="central-park-knyc",
    ),
    dict(
        station_pk=5, provider="CLIMANTARTIDE", network_code="", station_id="MZS",
        name="Mario Zucchelli", latitude=-74.6961, longitude=164.1122,
        elevation_m=15.0, timezone="Antarctica/McMurdo", country="AQ",
        region="", locality="", has_historical=False,
        sensor_keys=("thermometer", "anemometer", "wind_vane"),
        url_slug="mario-zucchelli-mzs",
    ),
)


def build_payload() -> list[dict]:
    cases = []
    for raw in CASES:
        fields = dict(raw)
        fields["name"] = _display_name(fields["name"])
        station = StationPage(**fields)
        languages = {}
        for code in station.language_codes:
            language = LANGUAGES[code]
            search_name = _station_search_name(station, language)
            location = station.location_label(language) or language.t("fallback_location")
            languages[code] = {
                "title": language.t(
                    "station_title", name=search_name, provider=station.provider_label
                ),
                "description": language.t(
                    "station_description",
                    name=search_name,
                    provider=station.provider_label,
                    location=location,
                ),
                "lede": language.t(
                    "station_lede",
                    name=search_name,
                    provider=station.provider_label,
                    location=location,
                ),
                "location": location,
                "search_name": search_name,
                "sensors": ", ".join(
                    language.sensors[key] for key in station.sensor_keys
                ) or language.t("sensor_unknown"),
                "place_name": f"{language.t('station_type')} {station.name}",
            }
        cases.append(
            {
                # La estación tal cual la devuelve /v1/stations/by-url-slug,
                # con el nombre SIN normalizar: normalizarlo es cosa del front.
                "station": {
                    "provider": station.provider,
                    "station_id": station.station_id,
                    "name": raw["name"],
                    "lat": station.latitude,
                    "lon": station.longitude,
                    "elevation": station.elevation_m,
                    "catalog_country": raw["country"],
                    "region": station.region,
                    "locality": station.locality,
                    "has_historical": station.has_historical,
                    "sensors": {key: True for key in station.sensor_keys},
                },
                "url_slug": station.url_slug,
                "display_name": station.name,
                "language_codes": list(station.language_codes),
                # El orden de los <link rel="alternate"> lo marca el
                # diccionario LANGUAGES, no el del país.
                "alternate_order": list(_station_alternates(station)),
                "languages": languages,
            }
        )
    return cases


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"site_url": SITE_URL, "cases": build_payload()},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[export_seo_parity_fixture] {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

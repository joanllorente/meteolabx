#!/usr/bin/env python3
"""Exporta a JSON los textos SEO que hoy generan las fichas estáticas.

El frontend SvelteKit tiene que emitir el MISMO ``<title>``, la misma
``description`` y los mismos datos estructurados que las páginas que Google
ya tiene indexadas. Traducir esos textos a mano en JavaScript garantizaría
que un día se separen; exportarlos desde ``scripts/seo_pages_i18n`` mantiene
una sola fuente de verdad.

El resultado se versiona en el repo: el build del front no necesita Python.

    python scripts/export_seo_i18n.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_seo_pages import (
    LANGUAGES_BY_COUNTRY,
    PROVIDER_COUNTRIES,
    PROVIDER_LABELS,
    SITE_URL,
    STATION_LOCATION_NAMES,
    STATION_SEARCH_NAMES,
    _country_label,
)
from scripts.seo_pages_i18n import DEFAULT_LANGUAGE, LANGUAGES

DEFAULT_OUTPUT = REPO_ROOT / "web" / "src" / "lib" / "seo" / "seo-i18n.generated.js"


def _country_labels() -> dict[str, dict[str, str]]:
    """Nombre de cada país en los seis idiomas, resuelto por Babel.

    Se exporta en vez de resolverlo en el navegador con ``Intl.DisplayNames``
    porque la descripción de cada ficha lleva el país dentro: si las dos
    librerías discrepan en una sola traducción, cambia un texto indexado.
    """
    codes = sorted(set(PROVIDER_COUNTRIES.values()) | set(LANGUAGES_BY_COUNTRY))
    return {
        code: {
            language: _country_label(code, language) for language in LANGUAGES
        }
        for code in codes
    }


def build_payload() -> dict[str, object]:
    return {
        "site_url": SITE_URL,
        "default_language": DEFAULT_LANGUAGE,
        # El orden importa: es el que llevan hoy los <link rel="alternate">
        # y el JSON se serializa con las claves ordenadas.
        "language_order": list(LANGUAGES),
        "languages": {code: asdict(spec) for code, spec in LANGUAGES.items()},
        "provider_labels": PROVIDER_LABELS,
        "provider_countries": PROVIDER_COUNTRIES,
        "languages_by_country": {
            country: list(codes) for country, codes in LANGUAGES_BY_COUNTRY.items()
        },
        "country_labels": _country_labels(),
        "station_search_names": {
            f"{provider}|{station_id}": names
            for (provider, station_id), names in STATION_SEARCH_NAMES.items()
        },
        "station_location_names": {
            f"{provider}|{station_id}": names
            for (provider, station_id), names in STATION_LOCATION_NAMES.items()
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_payload(), ensure_ascii=False, indent=2, sort_keys=True)
    # Módulo ES en vez de .json: así lo importan igual Vite y `node --test`,
    # sin arrastrar el `with { type: "json" }` que Node exige a los JSON.
    args.output.write_text(
        "// GENERADO por scripts/export_seo_i18n.py — no editar a mano.\n"
        "// Los textos viven en scripts/seo_pages_i18n.py, que es lo que\n"
        "// generó las paginas que Google ya tiene indexadas.\n"
        f"export default {payload};\n",
        encoding="utf-8",
    )
    print(f"[export_seo_i18n] {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

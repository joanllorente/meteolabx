#!/usr/bin/env python3
"""Exporta a JavaScript los textos de aplicación que comparten los dos frontends.

El pie de página —versión, «Novedades», «Privacidad»— y los filtros del mapa.
Son textos ya traducidos a los seis idiomas en ``locales/*.json``; copiarlos a
mano al frontend nuevo garantizaría que un día digan cosas distintas según por
dónde entres.

    python scripts/export_app_i18n.py
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

LOCALES_DIR = REPO_ROOT / "locales"
DEFAULT_OUTPUT = REPO_ROOT / "web" / "src" / "lib" / "i18n" / "app-i18n.generated.js"
# El visor de Predicción es un proyecto Vite aparte, y desde que comparte la
# barra superior con el resto de la web necesita los nombres de las pestañas.
FORECAST_OUTPUT = (
    REPO_ROOT / "prototype-svelte" / "src" / "lib" / "tabs-i18n.generated.js"
)
FORECAST_APP_OUTPUT = (
    REPO_ROOT / "prototype-svelte" / "src" / "lib" / "app-i18n.generated.js"
)
LANGUAGES = ("es", "ca", "en", "fr", "it", "pt")

# Las versiones que el modal de novedades enseña, de la más reciente a la más
# antigua. El orden es el del propio modal en Streamlit.
# La serie 1 se retiró con la 2.0.0: sus notas hablaban de una interfaz
# que ya no existe.
RELEASES = ("200",)


def _app_version() -> str:
    """Versión que muestra el pie. Vive en ``meteolabx.py`` como APP_VERSION."""
    source = (REPO_ROOT / "meteolabx.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith("APP_VERSION"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("No se encontró APP_VERSION en meteolabx.py")


def build_payload() -> dict[str, object]:
    footers: dict[str, dict] = {}
    for language in LANGUAGES:
        data = json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))
        footer = dict(data.get("footer", {}))
        footer["close"] = data.get("common", {}).get("close", "")
        footers[language] = footer
    # Claves del mapa que usa el panel de filtros del frontend nuevo.
    map_keys = (
        "historical_only", "hide_historical_only", "hide_manual", "hide_pws",
        "hide_historical_only_help", "hide_manual_help", "hide_pws_help",
        "sensor_filter", "sensor_filter_clear", "sensor_filter_caption",
        "country_filter", "country_all", "providers", "station_type",
        "temp_field_legend", "wind_field_legend", "precip_field_legend",
        "no_stations", "visible_stations", "select_station_hint",
    )
    # Del ranking basta con el interruptor de la Antártida: el resto de sus
    # textos son nuevos y viven en el propio frontend.
    ranking_keys = ("exclude_antarctica",)

    # Títulos, ejes y ayudas de los gráficos de tendencias. Los textos de las
    # ayudas son párrafos largos de meteorología escritos con cuidado; se
    # exportan enteros en vez de reescribirlos.
    trends_sections = ("charts", "tooltips")

    # Pestaña de Histórico: rótulos del selector, títulos de tarjeta y avisos.
    # Las métricas se traducen en el backend, que es quien arma la tabla; esto
    # es lo que rodea a los datos.
    historical_sections = (
        "actions", "cards", "inputs", "warnings", "info",
        "caption", "sections", "summary", "chart", "errors", "spinner",
    )

    # Calibración de sensores: rótulos y nombres de los siete offsets. Son los
    # mismos textos que enseña la barra lateral actual, con la misma redacción
    # —«Rango permitido: {min} a {max} {unit}»—, así que se exportan en vez de
    # reescribirlos.
    calibration_keys = (
        "title", "description", "requires_connection", "close",
        "range_help", "save", "saved", "unsaved",
    )

    unit_keys = ("title", "description", "close")

    maps: dict[str, dict] = {}
    rankings: dict[str, dict] = {}
    calibrations: dict[str, dict] = {}
    units: dict[str, dict] = {}
    trends: dict[str, dict] = {}
    historical: dict[str, dict] = {}
    observations: dict[str, dict] = {}
    for language in LANGUAGES:
        data = json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))
        section = data.get("map", {})
        maps[language] = {key: section[key] for key in map_keys if key in section}
        calibration_section = data.get("sidebar", {}).get("calibration", {})
        calibrations[language] = {
            key: calibration_section[key]
            for key in calibration_keys
            if key in calibration_section
        }
        calibrations[language]["fields"] = calibration_section.get("fields", {})
        unit_section = data.get("sidebar", {}).get("units", {})
        units[language] = {
            key: unit_section[key]
            for key in unit_keys
            if key in unit_section
        }
        units[language]["fields"] = unit_section.get("fields", {})

        ranking_section = data.get("ranking", {})
        rankings[language] = {
            key: ranking_section[key] for key in ranking_keys if key in ranking_section
        }
        trends_section = data.get("trends", {})
        trends[language] = {
            section: trends_section.get(section, {}) for section in trends_sections
        }
        historical_section = data.get("historical", {})
        historical[language] = {
            section: historical_section.get(section, {})
            for section in historical_sections
        }
        historical[language]["units"] = historical_section.get("units", {})

        # Los avisos fisiológicos deben decir exactamente lo mismo en los dos
        # frontends. Se exportan desde los locales que consume Streamlit, no se
        # mantienen copias abreviadas en Svelte.
        basic_cards = data.get("observation", {}).get("cards", {}).get("basic", {})
        observations[language] = {
            "temperature": {
                "heat_alert": basic_cards.get("temperature", {}).get("heat_alert", {}),
                # La etiqueta corta que acompaña al número desde los 40 °C de
                # índice de calor. El aviso largo solo aparece a partir de 45;
                # sin esta etiqueta, un índice de 40 —que ya es un riesgo— no
                # decía nada en la tarjeta.
                "heat_risk": basic_cards.get("temperature", {}).get("heat_risk", {}),
            },
            "dew_point": {
                "wet_bulb_alert": basic_cards.get("dew_point", {}).get("wet_bulb_alert", {}),
                "wet_bulb_risk": basic_cards.get("dew_point", {}).get("wet_bulb_risk", {}),
            },
            # Estados cualitativos que acompañan a un número: «Poco nuboso»
            # junto al 62 % de claridad, «Crepúsculo civil» cuando el sol ya
            # no da para medirla.
            "clarity": (
                data.get("observation", {})
                .get("cards", {})
                .get("dynamic", {})
                .get("clarity", {})
            ),
            "sky": (
                data.get("observation", {})
                .get("cards", {})
                .get("radiation", {})
                .get("sky_clarity", {})
            ),
        }

    # Escalas de color de los tres campos interpolados. Se exportan desde el
    # backend, que es quien pinta los PNG: una leyenda con cortes distintos a
    # los del ráster miente sobre lo que se está viendo.
    from server.services.precipitation_field import COLOR_STOPS as PRECIP_STOPS
    from server.services.temperature_field import COLOR_STOPS as TEMP_STOPS
    from server.services.wind_field import COLOR_STOPS as WIND_STOPS

    scales = {
        name: [[float(value), list(rgb)] for value, rgb in stops]
        for name, stops in (
            ("temperature", TEMP_STOPS),
            ("wind", WIND_STOPS),
            ("precipitation", PRECIP_STOPS),
        )
    }

    return {
        "field_scales": scales,
        "app_version": _app_version(),
        "releases": list(RELEASES),
        "footer": footers,
        "map": maps,
        "ranking": rankings,
        "calibration": calibrations,
        "units": units,
        "trends": trends,
        "historical": historical,
        "observation": observations,
    }


def build_tabs() -> dict[str, dict]:
    """Nombres de las seis pestañas en los seis idiomas."""
    return {
        language: json.loads(
            (LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8")
        ).get("tabs", {})
        for language in LANGUAGES
    }


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(
        "// GENERADO por scripts/export_app_i18n.py — no editar a mano.\n"
        "// Los textos viven en locales/*.json, que es lo que lee la app actual.\n"
        f"export default {body};\n",
        encoding="utf-8",
    )
    print(f"[export_app_i18n] {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--forecast-output", type=Path, default=FORECAST_OUTPUT)
    parser.add_argument(
        "--forecast-app-output", type=Path, default=FORECAST_APP_OUTPUT
    )
    args = parser.parse_args(argv)
    payload = build_payload()
    _write(args.output, payload)
    _write(args.forecast_app_output, payload)
    _write(args.forecast_output, build_tabs())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

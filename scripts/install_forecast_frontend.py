#!/usr/bin/env python3
"""Publica el build Svelte de Predicción en el servicio que lo sirve.

Un único destino: ``web/static/forecast``, en el servicio SvelteKit, que es
quien responde a ``/forecast``. El visor es un SPA estático que solo necesita
``/v1``.

Antes se publicaba también dentro del paquete de Streamlit
(``<streamlit>/static/forecast``), que era como se servía la página en la
versión anterior. Ese destino obligaba a tener Streamlit instalado para
desplegar el visor, y ya no lo sirve nadie.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "static" / "forecast_app"
WEB_STATIC_DIR = REPO_ROOT / "web" / "static" / "forecast"


def install_forecast_web(target: Path | None = None) -> Path:
    """Copia el visor al servicio SvelteKit con rutas absolutas.

    Vite lo compila con ``base: './'``, que resuelve los assets relativos a la
    carpeta. Sirviéndolo en ``/forecast`` eso obliga a la barra final: sin
    ella, ``./assets/...`` apunta a la raíz del sitio y el visor carga en
    blanco. Reescribir el prefijo a ``/forecast/`` hace que las dos formas de
    la URL funcionen.
    """
    if not (BUILD_DIR / "forecast.html").is_file():
        raise FileNotFoundError(
            "Falta static/forecast_app/forecast.html. "
            "Ejecuta `npm run build:forecast` en prototype-svelte."
        )
    destination = target or WEB_STATIC_DIR
    if destination.is_dir():
        shutil.rmtree(destination)
    shutil.copytree(BUILD_DIR, destination)

    page = (destination / "forecast.html").read_text(encoding="utf-8")
    (destination / "forecast.html").unlink()
    (destination / "index.html").write_text(
        page.replace('="./', '="/forecast/'), encoding="utf-8"
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        type=Path,
        help="Destino alternativo (útil para pruebas).",
    )
    # Se acepta y se ignora: el build de prototype-svelte lo pasa en su
    # `postbuild`, y ahora instalar solo en la web es lo único que se hace.
    parser.add_argument("--web-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    print(f"[forecast] Visor publicado en {install_forecast_web(args.target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

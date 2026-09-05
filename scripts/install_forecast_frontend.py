#!/usr/bin/env python3
"""Publica el build Svelte de Predicción en los frontends que lo sirven.

Hay dos destinos mientras dure la migración:

- ``<streamlit>/static/forecast``, el de siempre;
- ``web/static/forecast``, en el servicio SvelteKit, que es quien responde
  hoy a ``/forecast``. Ahí el visor deja de depender de Streamlit: es un SPA
  estático que solo necesita ``/v1``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "static" / "forecast_app"
WEB_STATIC_DIR = REPO_ROOT / "web" / "static" / "forecast"


def streamlit_static_dir() -> Path:
    import streamlit

    return Path(streamlit.__file__).resolve().parent / "static"


def install_forecast_frontend(target_static_dir: Path | None = None) -> Path:
    """Publica el artefacto en ``<streamlit>/static/forecast``.

    Vite conserva ``forecast.html`` porque es el nombre del entrypoint. También
    instalamos una copia como ``index.html`` para que el servidor pueda resolver
    la URL pública limpia ``/forecast`` (o su redirección canónica ``/forecast/``).

    Los ``assets`` del build anterior se retiran. La copia solo añade, así que
    sin esta limpieza el destino acumula un bundle por build —sesenta y uno en
    una instalación local, trece megas— y, lo que importa más, deja servible un
    frontend viejo si el ``forecast.html`` se queda a medio actualizar.
    """
    if not (BUILD_DIR / "forecast.html").is_file():
        raise FileNotFoundError(
            "Falta static/forecast_app/forecast.html. "
            "Ejecuta `npm run build:forecast` en prototype-svelte."
        )

    target = (target_static_dir or streamlit_static_dir()) / "forecast"
    assets = target / "assets"
    if assets.is_dir():
        vigentes = {path.name for path in (BUILD_DIR / "assets").iterdir()}
        for path in assets.iterdir():
            if path.is_file() and path.name not in vigentes:
                path.unlink()
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUILD_DIR, target, dirs_exist_ok=True)
    shutil.copy2(target / "forecast.html", target / "index.html")
    return target


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
        "--target-static-dir",
        type=Path,
        help="Directorio static alternativo (útil para pruebas).",
    )
    parser.add_argument("--web-only", action="store_true", help="Instalar solo en SvelteKit, sin depender de Streamlit.")
    args = parser.parse_args()
    if args.web_only:
        print(f"[forecast] Visor publicado en {install_forecast_web()}")
        return 0
    target = install_forecast_frontend(args.target_static_dir)
    print(f"[forecast] Frontend Svelte instalado en {target}")
    if args.target_static_dir is None:
        web_target = install_forecast_web()
        print(f"[forecast] Visor publicado en {web_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

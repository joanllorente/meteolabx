#!/usr/bin/env python3
"""Instala el build Svelte de Predicción en el frontend público de Streamlit."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "static" / "forecast_app"


def streamlit_static_dir() -> Path:
    import streamlit

    return Path(streamlit.__file__).resolve().parent / "static"


def install_forecast_frontend(target_static_dir: Path | None = None) -> Path:
    """Publica el artefacto en ``<streamlit>/static/forecast``.

    Vite conserva ``forecast.html`` porque es el nombre del entrypoint. También
    instalamos una copia como ``index.html`` para que el servidor pueda resolver
    la URL pública limpia ``/forecast`` (o su redirección canónica ``/forecast/``).
    """
    if not (BUILD_DIR / "forecast.html").is_file():
        raise FileNotFoundError(
            "Falta static/forecast_app/forecast.html. "
            "Ejecuta `npm run build:forecast` en prototype-svelte."
        )

    target = (target_static_dir or streamlit_static_dir()) / "forecast"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUILD_DIR, target, dirs_exist_ok=True)
    shutil.copy2(target / "forecast.html", target / "index.html")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target-static-dir",
        type=Path,
        help="Directorio static alternativo (útil para pruebas).",
    )
    args = parser.parse_args()
    target = install_forecast_frontend(args.target_static_dir)
    print(f"[forecast] Frontend Svelte instalado en {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

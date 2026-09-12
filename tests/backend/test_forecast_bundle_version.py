"""El visor de Predicción se sirve compilado, y hay que reconstruirlo.

`/forecast` no es una página de SvelteKit: es un SPA aparte que vive compilado
en ``web/static/forecast`` y se publica con ``npm run build:forecast``. Su
número de versión y su lista de novedades viajan DENTRO del paquete, así que
subir ``VERSION`` no los cambia: hay que volver a compilar y copiar.

Al publicar la 2.0.3 no se hizo, y la pestaña siguió enseñando «2.0.2» y las
novedades de la versión anterior mientras el resto del sitio ya iba por la
nueva. Nada lo detectó porque ningún test miraba el paquete publicado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_DIR = REPO_ROOT / "web" / "static" / "forecast" / "assets"


def _published_bundles() -> list[Path]:
    return sorted(BUNDLE_DIR.glob("forecast-*.js"))


def test_the_published_viewer_carries_the_current_version() -> None:
    from version import app_version

    bundles = _published_bundles()
    assert bundles, f"no hay visor publicado en {BUNDLE_DIR}"

    esperada = app_version()
    encontrado = any(
        f'"{esperada}"' in bundle.read_text(encoding="utf-8", errors="ignore")
        for bundle in bundles
    )
    assert encontrado, (
        f"el visor publicado no lleva la versión {esperada}. "
        "Reconstrúyelo: cd prototype-svelte && npm run build:forecast"
    )


def test_only_one_viewer_is_published() -> None:
    """Un build viejo sin borrar se queda servido y pesando."""
    bundles = _published_bundles()
    assert len(bundles) == 1, (
        "hay más de un paquete del visor publicado: "
        + ", ".join(b.name for b in bundles)
    )


def test_the_index_points_at_the_published_bundle() -> None:
    """El `index.html` referencia el paquete por su hash: si no coincide, la
    pestaña carga un fichero que ya no existe y sale en blanco."""
    indice = (REPO_ROOT / "web" / "static" / "forecast" / "index.html").read_text(
        encoding="utf-8"
    )
    for bundle in _published_bundles():
        assert bundle.name in indice, f"{bundle.name} no lo referencia el index.html"

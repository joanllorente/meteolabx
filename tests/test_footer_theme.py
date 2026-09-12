"""La versión se declara en un sitio y todos la leen de ahí.

Antes el número vivía en ``meteolabx.py`` como ``APP_VERSION`` y se copiaba a
mano a ``server/__init__.py`` y al exportador de textos. Tres copias que había
que mover juntas: al publicar la 2.0.2 se quedaron dos al día y este test, que
fijaba el número literal, siguió exigiendo la 2.0.1 sin que nadie lo notara.

Ahora compara las fuentes entre sí, así que no puede desfasarse: si alguien
cambia ``VERSION`` y se olvida del resto, salta.
"""

from pathlib import Path


def test_every_component_announces_the_same_version() -> None:
    raiz = Path(__file__).resolve().parents[1]
    declarada = (raiz / "VERSION").read_text(encoding="utf-8").strip()

    from version import APP_VERSION

    assert APP_VERSION == declarada

    import server

    assert server.__version__ == declarada, "el backend anuncia otra versión"

    from scripts.export_app_i18n import _app_version

    assert _app_version() == declarada, "la web recibiría otra versión"


def test_the_version_belongs_to_the_published_series() -> None:
    """La serie 2 es la que abre pestaña en «Novedades»."""
    raiz = Path(__file__).resolve().parents[1]
    declarada = (raiz / "VERSION").read_text(encoding="utf-8").strip()

    assert declarada.startswith("2.")
    # Tres números, sin sufijos: es lo que espera el selector de versiones.
    assert len(declarada.split(".")) == 3
    assert all(parte.isdigit() for parte in declarada.split("."))


def test_the_release_notes_cover_the_current_version() -> None:
    """Publicar sin nota deja la pestaña de Novedades vacía."""
    import json

    raiz = Path(__file__).resolve().parents[1]
    declarada = (raiz / "VERSION").read_text(encoding="utf-8").strip()
    clave = declarada.replace(".", "")

    from scripts.export_app_i18n import RELEASES

    assert clave in RELEASES, f"falta la {declarada} en RELEASES"

    textos = json.loads((raiz / "locales" / "es.json").read_text(encoding="utf-8"))["footer"]
    assert any(
        f"release_{clave}_{tipo}" in textos for tipo in ("improvements", "fixes")
    ), f"la {declarada} no tiene nota en locales/es.json"

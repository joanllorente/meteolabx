"""
Contrato de warnings estructurados: cada código estable que emite el
backend (``domain.observation_warnings``) DEBE tener una plantilla i18n en
los tres idiomas, y esa plantilla debe formatear sin errores con los
``params`` que el builder produce.

Es la red que evita el desfase "código nuevo en el backend / clave que
falta en ``locales/*.json``" (que se vería como ``warnings.<code>`` crudo
en la UI).
"""

import json
from pathlib import Path

import pytest

from domain import observation_warnings as ow
from utils.i18n import get_supported_languages

_LOCALES = Path(__file__).resolve().parent.parent / "locales"
_LANGS = tuple(get_supported_languages())

# Cada código + un ejemplo representativo de sus params.
_SAMPLES = {
    ow.DATA_AGE: ow.data_age("WU", 120.0),
    ow.MISSING_ELEVATION: ow.missing_elevation(),
}


def _catalog(lang: str) -> dict:
    with (_LOCALES / f"{lang}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def test_builders_emit_code_and_params():
    assert ow.data_age("WU", 119.6) == {
        "code": "data_age", "params": {"provider": "WU", "minutes": 120},
    }
    assert ow.missing_elevation() == {"code": "missing_elevation", "params": {}}


@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.parametrize("code,warning", list(_SAMPLES.items()))
def test_every_code_has_template_in_every_language(lang, code, warning):
    catalog = _catalog(lang)
    template = catalog.get("warnings", {}).get(code)
    assert isinstance(template, str) and template, (
        f"falta warnings.{code} en {lang}.json"
    )
    # Formatea sin KeyError: la plantilla solo usa params que el builder da.
    rendered = template.format(**warning["params"])
    assert "{" not in rendered  # no quedan placeholders sin sustituir

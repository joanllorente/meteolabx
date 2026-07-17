import json
import string
from pathlib import Path

from components.sidebar import LOCAL_STORAGE_BOOTSTRAP_KEYS
from config import LS_LANGUAGE
from utils.i18n import get_supported_languages, match_supported_browser_language


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"


def _catalog(language: str) -> dict:
    return json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))


def _leaf_values(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from _leaf_values(child, child_path)
        return
    if isinstance(value, list):
        yield path, ("list", len(value))
        for index, child in enumerate(value):
            yield from _leaf_values(child, f"{path}[{index}]")
        return
    yield path, value


def _format_fields(text: str) -> set[str]:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(text)
        if field_name is not None
    }


def test_portuguese_catalog_has_the_same_shape_and_format_fields_as_spanish():
    reference = dict(_leaf_values(_catalog("es")))
    assert "pt" in get_supported_languages()
    translated = dict(_leaf_values(_catalog("pt")))
    assert translated.keys() == reference.keys()

    for path, source_value in reference.items():
        translated_value = translated[path]
        if isinstance(source_value, str):
            assert isinstance(translated_value, str)
            if source_value:
                assert translated_value, path
            assert _format_fields(translated_value) == _format_fields(source_value), path
        else:
            assert translated_value == source_value, path


def test_browser_language_matching_accepts_regional_variants():
    assert match_supported_browser_language(["en-US", "es-ES"]) == "en"
    assert match_supported_browser_language(["pt-BR", "en-US"]) == "pt"
    assert match_supported_browser_language("pt-PT") == "pt"
    assert match_supported_browser_language(["de-DE", "fr-FR"]) == "fr"
    assert match_supported_browser_language(["de-DE"]) is None


def test_language_preference_is_included_in_browser_storage_bootstrap():
    assert LS_LANGUAGE == "meteolabx_language"
    assert LS_LANGUAGE in LOCAL_STORAGE_BOOTSTRAP_KEYS

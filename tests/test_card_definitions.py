import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_spanish_erythemal_dose_definition_includes_skin_phototypes():
    text = (ROOT / "locales" / "definiciones.es.txt").read_text(encoding="utf-8")

    assert "Dosis eritemática" in text
    assert "1 SED equivale a 100 J/m²" in text
    assert "Fototipo I" in text
    assert "Fototipo VI" in text


def test_translated_erythemal_dose_definitions_include_skin_phototypes():
    for lang in ("en", "fr"):
        data = json.loads(
            (ROOT / "locales" / f"card_definitions.{lang}.json").read_text(
                encoding="utf-8"
            )
        )
        definition = data["dosis eritematica"]

        assert "SED" in definition
        assert "100 J/m²" in definition
        assert "Phototype I" in definition or "phototype I" in definition
        assert "Phototype VI" in definition or "phototype VI" in definition

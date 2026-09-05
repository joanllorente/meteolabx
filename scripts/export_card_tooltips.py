#!/usr/bin/env python3
"""Exporta a JavaScript las definiciones que explican cada tarjeta.

Son los textos que la app actual enseña al posarse en el interrogante de una
tarjeta: qué es la temperatura de bulbo húmedo, cómo se calcula el heat index,
qué mide un piranómetro. Están escritos con cuidado y traducidos a seis
idiomas; copiarlos al frontend nuevo a mano garantizaría que un día digan
cosas distintas según por dónde entres.

El corpus vive en ``locales/definiciones.es.txt`` —un formato de texto con
bloques y sub-entradas— y en ``locales/card_definitions.{ca,en,fr,it,pt}.json``.

    python scripts/export_card_tooltips.py
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = REPO_ROOT / "locales"
DEFAULT_OUTPUT = REPO_ROOT / "web" / "src" / "lib" / "i18n" / "card-tooltips.generated.js"
LANGUAGES = ("es", "ca", "en", "fr", "it", "pt")


def normalize(text: str) -> str:
    """Clave de búsqueda: sin acentos, sin signos y en minúsculas.

    Las tarjetas se buscan por su título, y el título lleva tildes, unidades
    entre paréntesis y mayúsculas según el idioma. Normalizar las dos partes
    —la clave del catálogo y el título— es lo que hace que se encuentren.
    """
    lowered = str(text or "").strip().lower()
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", stripped)).strip()


def parse_es_definitions(path: Path) -> Dict[str, str]:
    """Lee el corpus en español: bloques separados por líneas de ``=``.

    Cada bloque abre con ``- Título: definición`` y puede llevar sub-entradas
    indentadas (``\\t- Sensación térmica: …``) que se anexan al mismo texto.
    """
    definitions: Dict[str, str] = {}
    if not path.exists():
        return definitions

    key = ""
    parts: list[str] = []

    def flush() -> None:
        if key:
            definitions[key] = "\n".join(part for part in parts if part)

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if set(stripped) <= {"=", "-"}:
            flush()
            key, parts = "", []
            continue

        is_child = raw.startswith("\t- ") or raw.startswith("    - ")
        if not is_child and stripped.startswith("- "):
            flush()
            payload = stripped[2:].strip()
            label, _, description = payload.partition(":")
            key = normalize(label if _ else payload)
            parts = [description.strip()] if description.strip() else []
            continue

        if is_child and key:
            child = stripped[2:].strip()
            if child:
                parts.append(f"- {child}")
            continue

        if not key and ":" in stripped:
            label, _, description = stripped.partition(":")
            key = normalize(label)
            parts = [description.strip()] if description.strip() else []
            continue

        if key:
            parts.append(stripped)

    flush()
    return definitions


def build_payload() -> Dict[str, Dict[str, str]]:
    payload = {"es": parse_es_definitions(LOCALES_DIR / "definiciones.es.txt")}
    for language in LANGUAGES:
        if language == "es":
            continue
        path = LOCALES_DIR / f"card_definitions.{language}.json"
        if not path.exists():
            payload[language] = {}
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        payload[language] = {
            normalize(key): str(value) for key, value in raw.items() if str(key).strip()
        }
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    payload = build_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    args.output.write_text(
        "// GENERADO por scripts/export_card_tooltips.py — no editar a mano.\n"
        "// Las definiciones viven en locales/, que es lo que lee la app actual.\n"
        f"export default {body};\n",
        encoding="utf-8",
    )
    counts = ", ".join(f"{lang}: {len(payload[lang])}" for lang in LANGUAGES)
    print(f"[export_card_tooltips] {args.output} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

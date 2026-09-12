"""
Traducción de la interfaz, sin Streamlit.

Esto vivía en ``utils/i18n.py``, que importa Streamlit para guardar el idioma
activo en ``st.session_state``. El backend traduce las etiquetas de los
climogramas con esas mismas funciones, así que arrastraba el paquete entero por
esta vía —y, peor, heredaba un estado GLOBAL AL PROCESO: dos peticiones en
idiomas distintos se pisaban, y hubo que poner un candado en el router de
climatología para serializarlas.

Aquí el idioma activo vive en un ``ContextVar``, que es lo que corresponde a un
dato ambiental por petición: cada tarea de asyncio y cada hilo ven el suyo, sin
candados y sin pisarse. ``utils/i18n.py`` sigue existiendo como envoltorio para
la interfaz de Streamlit, y delega en este módulo.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

DEFAULT_LANG = "es"
SUPPORTED_LANGUAGES = {
    "es": "Español",
    "ca": "Català",
    "en": "English",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
}

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

# Idioma activo. Por petición, no por proceso.
_LANGUAGE: ContextVar[str] = ContextVar("meteolabx_language", default=DEFAULT_LANG)


def normalize_lang(lang: Optional[str]) -> str:
    value = str(lang or "").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANG


def get_language() -> str:
    """Idioma activo en este contexto."""
    return _LANGUAGE.get()


def set_language(lang: str) -> str:
    """Fija el idioma del contexto actual y devuelve el normalizado."""
    normalized = normalize_lang(lang)
    _LANGUAGE.set(normalized)
    return normalized


@contextmanager
def language_scope(lang: Optional[str]) -> Iterator[str]:
    """Traduce dentro del bloque en ``lang`` y restaura al salir.

    Es lo que sustituye al candado del router de climatología: en vez de
    serializar las peticiones para que no se pisen el idioma global, cada una
    trabaja en el suyo.
    """
    normalized = normalize_lang(lang)
    testigo = _LANGUAGE.set(normalized)
    try:
        yield normalized
    finally:
        _LANGUAGE.reset(testigo)


@lru_cache(maxsize=32)
def _load_catalog_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    del mtime_ns  # parte de la clave: al cambiar el fichero, entra uno nuevo
    with Path(path_str).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_catalog(lang: str) -> dict[str, Any]:
    """Catálogo del idioma. El resultado se comparte: NO modificarlo."""
    path = _LOCALES_DIR / f"{normalize_lang(lang)}.json"
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    return _load_catalog_cached(str(path), mtime_ns)


def _lookup_key(payload: dict[str, Any], key: str) -> Optional[str]:
    current: Any = payload
    for part in str(key).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def _lookup_list(payload: dict[str, Any], key: str) -> Optional[list[str]]:
    current: Any = payload
    for part in str(key).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return [str(item) for item in current] if isinstance(current, list) else None


def t(key: str, default: Optional[str] = None, *, lang: Optional[str] = None, **kwargs: Any) -> str:
    """Texto traducido. Sin ``lang``, el del contexto activo."""
    idioma = normalize_lang(lang) if lang else get_language()
    text = (
        _lookup_key(load_catalog(idioma), key)
        or _lookup_key(load_catalog(DEFAULT_LANG), key)
        or default
        or key
    )
    try:
        return str(text).format(**kwargs)
    except Exception:  # noqa: BLE001 — un formato roto no puede tumbar la página
        return str(text)


def t_list(key: str, *, lang: Optional[str] = None) -> list[str]:
    """Claves cuyo valor es una lista JSON (las novedades del pie, por ejemplo)."""
    idioma = normalize_lang(lang) if lang else get_language()
    return (
        _lookup_list(load_catalog(idioma), key)
        or _lookup_list(load_catalog(DEFAULT_LANG), key)
        or []
    )


def month_name(month: int, short: bool = False, lang: Optional[str] = None) -> str:
    month_int = int(month)
    key = f"months.{'short' if short else 'long'}.{month_int}"
    idioma = normalize_lang(lang) if lang else get_language()
    return str(
        _lookup_key(load_catalog(idioma), key)
        or _lookup_key(load_catalog(DEFAULT_LANG), key)
        or month_int
    )


def get_supported_languages() -> list[str]:
    return list(SUPPORTED_LANGUAGES.keys())


def get_language_label(lang: str) -> str:
    return SUPPORTED_LANGUAGES.get(normalize_lang(lang), SUPPORTED_LANGUAGES[DEFAULT_LANG])


def match_supported_browser_language(languages: Iterable[str] | str | None) -> Optional[str]:
    """Primer idioma del navegador que la interfaz sabe servir.

    Los navegadores mandan etiquetas regionales —``en-US``, ``pt-BR``— y los
    catálogos van por idioma base.
    """
    if isinstance(languages, str):
        candidates = [languages]
    else:
        try:
            candidates = list(languages or [])
        except TypeError:
            candidates = []
    for candidate in candidates:
        base = str(candidate or "").strip().lower().replace("_", "-").split("-", 1)[0]
        if base in SUPPORTED_LANGUAGES:
            return base
    return None

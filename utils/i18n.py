"""
Utilidades sencillas de internacionalización para la UI de MeteoLabX.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import streamlit as st


DEFAULT_LANG = "es"
SUPPORTED_LANGUAGES = {
    "es": "Español",
    "en": "English",
    "fr": "Français",
}

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


def _normalize_lang(lang: Optional[str]) -> str:
    value = str(lang or "").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANG


def _lookup_key(payload: dict[str, Any], key: str) -> Optional[str]:
    current: Any = payload
    for part in str(key).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


@st.cache_data(show_spinner=False)
def _load_catalog_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    del mtime_ns  # parte de la clave de caché para invalidar al cambiar el archivo
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_catalog(lang: str) -> dict[str, Any]:
    normalized = _normalize_lang(lang)
    path = _LOCALES_DIR / f"{normalized}.json"
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    return _load_catalog_cached(str(path), mtime_ns)


def init_language() -> str:
    raw_query_lang = str(st.query_params.get("lang", "")).strip().lower()
    raw_session_lang = str(st.session_state.get("lang", "")).strip().lower()

    query_lang = raw_query_lang if raw_query_lang in SUPPORTED_LANGUAGES else None
    session_lang = raw_session_lang if raw_session_lang in SUPPORTED_LANGUAGES else None

    lang = query_lang or session_lang or DEFAULT_LANG

    st.session_state["lang"] = lang
    return lang


def get_language() -> str:
    raw_query_lang = str(st.query_params.get("lang", "")).strip().lower()
    if raw_query_lang in SUPPORTED_LANGUAGES:
        if st.session_state.get("lang") != raw_query_lang:
            st.session_state["lang"] = raw_query_lang
        return raw_query_lang

    raw_session_lang = str(st.session_state.get("lang", "")).strip().lower()
    if raw_session_lang in SUPPORTED_LANGUAGES:
        return raw_session_lang

    return init_language()


def set_language(lang: str) -> str:
    normalized = _normalize_lang(lang)
    st.session_state["lang"] = normalized
    try:
        if str(st.query_params.get("lang", "")).strip().lower() != normalized:
            st.query_params["lang"] = normalized
    except Exception:
        pass
    return normalized


def get_supported_languages() -> list[str]:
    return list(SUPPORTED_LANGUAGES.keys())


def get_language_label(lang: str) -> str:
    return SUPPORTED_LANGUAGES.get(_normalize_lang(lang), SUPPORTED_LANGUAGES[DEFAULT_LANG])


def t(key: str, default: Optional[str] = None, **kwargs: Any) -> str:
    lang = get_language()
    text = (
        _lookup_key(load_catalog(lang), key)
        or _lookup_key(load_catalog(DEFAULT_LANG), key)
        or default
        or key
    )
    try:
        return str(text).format(**kwargs)
    except Exception:
        return str(text)


def month_name(month: int, short: bool = False, lang: Optional[str] = None) -> str:
    month_int = int(month)
    key = f"months.{'short' if short else 'long'}.{month_int}"
    normalized = _normalize_lang(lang) if lang else get_language()
    text = (
        _lookup_key(load_catalog(normalized), key)
        or _lookup_key(load_catalog(DEFAULT_LANG), key)
        or str(month_int)
    )
    return str(text)

from pathlib import Path
from types import SimpleNamespace

from utils import browser_sync
from utils.state_keys import BROWSER_LANGUAGES


def test_browser_sync_does_not_pollute_public_url_with_context_params():
    """
    Garantiza que el módulo browser_sync no añade parámetros legacy
    (_tz, _vw, _cs, _mlx_boot) a la URL. La limpieza positiva de esos
    parámetros antiguos se hace ahora desde ``meteolabx.py`` dentro del
    iframe combinado de PWA (``_inject_pwa_metadata``), por eso el
    ``searchParams.delete(key)`` ya no vive aquí.
    """
    source = Path("utils/browser_sync.py").read_text(encoding="utf-8")

    assert 'searchParams.set("_tz"' not in source
    assert 'searchParams.set("_vw"' not in source
    assert 'searchParams.set("_cs"' not in source
    assert 'searchParams.set("_mlx_boot"' not in source
    assert "location.replace" not in source


def test_legacy_query_param_cleanup_still_present_in_pwa_iframe():
    """
    La limpieza de los query params legacy (_tz, _vw, _cs, _mlx_boot) se
    consolidó en ``inject_pwa_metadata`` (components/web_injectors.py, antes
    inline en meteolabx.py) tras unir las inyecciones JS de arranque.
    Verificamos que sigue ahí para que sesiones antiguas con esos
    parámetros en la URL los pierdan tras cargar.
    """
    source = Path("components/web_injectors.py").read_text(encoding="utf-8")

    assert 'searchParams.delete' in source
    assert '"_tz"' in source
    assert '"_vw"' in source
    assert '"_cs"' in source
    assert '"_mlx_boot"' in source


def test_browser_context_reports_and_hydrates_preferred_languages(monkeypatch):
    frontend = Path("components/browser_context_frontend/index.html").read_text(
        encoding="utf-8"
    )
    assert "window.navigator.languages" in frontend
    assert "langs: browserLanguages" in frontend

    fake_st = SimpleNamespace(session_state={})
    monkeypatch.setattr(browser_sync, "st", fake_st)

    browser_sync.hydrate_browser_context_live(
        lambda **_kwargs: {
            "tz": "America/New_York",
            "vw": 1440,
            "cs": "light",
            "lang": "en-US",
            "langs": ["en-US", "es-US"],
        }
    )

    assert fake_st.session_state[BROWSER_LANGUAGES] == ["en-US", "es-US"]

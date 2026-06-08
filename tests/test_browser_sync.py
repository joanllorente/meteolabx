from pathlib import Path


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
    consolidó en ``_inject_pwa_metadata`` (meteolabx.py) tras unir las
    inyecciones JS de arranque. Verificamos que sigue ahí para que
    sesiones antiguas con esos parámetros en la URL los pierdan tras
    cargar.
    """
    source = Path("meteolabx.py").read_text(encoding="utf-8")

    assert 'searchParams.delete' in source
    assert '"_tz"' in source
    assert '"_vw"' in source
    assert '"_cs"' in source
    assert '"_mlx_boot"' in source

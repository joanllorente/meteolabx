from pathlib import Path


def test_browser_sync_does_not_pollute_public_url_with_context_params():
    source = Path("utils/browser_sync.py").read_text(encoding="utf-8")

    assert 'searchParams.set("_tz"' not in source
    assert 'searchParams.set("_vw"' not in source
    assert 'searchParams.set("_cs"' not in source
    assert 'searchParams.set("_mlx_boot"' not in source
    assert "location.replace" not in source
    assert 'searchParams.delete(key)' in source

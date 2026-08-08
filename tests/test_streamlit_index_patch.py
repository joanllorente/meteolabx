from pathlib import Path

from scripts.patch_streamlit_index import patch


def test_streamlit_index_patch_adds_one_idempotent_boot_splash(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text(
        '<!doctype html><html lang="en"><head><title>Streamlit</title></head>'
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )

    assert patch(index) is True
    first = index.read_text(encoding="utf-8")
    assert first.count('id="mlx-boot-splash"') == 1
    assert first.count('id="mlx-critical-layout"') == 1
    assert '[data-testid="stMainBlockContainer"]' in first
    assert 'padding-top: 8.5rem !important' in first
    assert 'st-key-mlx_local_storage_bootstrap' in first
    assert first.index('id="mlx-boot-splash"') < first.index('id="root"')

    assert patch(index) is False
    second = index.read_text(encoding="utf-8")
    assert second.count('id="mlx-boot-splash"') == 1
    assert second.count('id="mlx-critical-layout"') == 1

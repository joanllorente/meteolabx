from pathlib import Path
import json


def test_footer_panels_use_active_theme_tokens():
    source = (
        Path(__file__).resolve().parents[1] / "meteolabx.py"
    ).read_text(encoding="utf-8")

    assert '"background:var(--panel);border:1px solid var(--border);"' in source
    assert '"color:var(--text) !important;box-shadow:var(--shadow);' in source
    assert '"{color:var(--text) !important;}"' in source
    assert "background:rgba(219, 235, 255, 0.96)" not in source


def test_whats_new_uses_one_modal_opened_from_header_and_footer():
    root = Path(__file__).resolve().parents[1]
    source = (root / "meteolabx.py").read_text(encoding="utf-8")
    header_source = (root / "components" / "app_header.py").read_text(
        encoding="utf-8"
    )

    assert 'app_version=APP_VERSION' in source
    assert 'class="header-version" data-mlbx-open-whats-new' in header_source
    assert "class='mlb-footer-action' data-mlbx-open-whats-new" in source
    assert "data-mlbx-whats-new-modal aria-hidden='true'" in source
    assert "class='mlx-wn-close' data-mlbx-close-whats-new" in source
    assert "function openWhatsNewModal(trigger)" in source
    assert "function closeWhatsNewModal()" in source
    assert 'event.key === "Escape"' in source
    assert 'doc.querySelectorAll(".mlx-wn-dialog-content, .mlb-whats-new-panel")' in source
    assert 'doc.removeEventListener("click", host.__mlbxWhatsNewTabsHandler, true)' in source
    assert ".header h1 a{" in source


def test_release_130_is_current_and_localized():
    root = Path(__file__).resolve().parents[1]
    source = (root / "meteolabx.py").read_text(encoding="utf-8")
    server_source = (root / "server" / "__init__.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = "1.3.0"' in source
    assert '__version__ = "1.3.0"' in server_source
    assert "data-mlbx-whats-new-version='130' aria-selected='true'>1.3.0" in source
    assert 'const versionTab = target.closest("[data-mlbx-whats-new-version]")' in source
    assert 'sessionStorage.setItem("mlbx-whats-new-version", version)' in source
    assert 'button.classList.toggle("is-active", active);' in source
    assert "selectWhatsNewVersion(buttonVersion);" in source
    assert 'pane.classList.toggle("is-active"' in source

    portuguese_names = {
        "es": "portugués",
        "ca": "portuguès",
        "en": "portuguese",
        "fr": "portugais",
        "it": "portoghese",
        "pt": "português",
    }
    for language, portuguese_name in portuguese_names.items():
        payload = json.loads((root / "locales" / f"{language}.json").read_text())
        footer = payload["footer"]
        assert len(footer["release_130_improvements"]) == 7
        assert portuguese_name in footer["release_130_improvements"][-1].lower()
        assert len(footer["release_130_fixes"]) == 2
        assert all(footer["release_130_fixes"])

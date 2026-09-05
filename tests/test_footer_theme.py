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


def test_release_200_is_current_and_localized():
    """La 2.0.0 es la única nota: la serie 1 hablaba de una interfaz retirada."""
    root = Path(__file__).resolve().parents[1]
    source = (root / "meteolabx.py").read_text(encoding="utf-8")
    server_source = (root / "server" / "__init__.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = "2.0.0"' in source
    assert "APP_BUILD = app_build_id()" in source
    assert '__version__ = "2.0.0"' in server_source
    assert "mlx-wn-pane-200 is-active" in source
    assert "Build {html.escape(APP_BUILD)}" in source
    assert ".mlx-wn-build{" in source

    notas = {
        "es": "Nuevo gráfico de viento en Histórico.",
        "ca": "Nou gràfic de vent a Històric.",
        "en": "New wind chart in Historical.",
        "fr": "Nouveau graphique de vent dans Historique.",
        "it": "Nuovo grafico del vento nello Storico.",
        "pt": "Novo gráfico de vento no Histórico.",
    }
    for idioma, esperado in notas.items():
        datos = json.loads((root / "locales" / f"{idioma}.json").read_text(encoding="utf-8"))
        footer = datos["footer"]
        assert esperado in footer["release_200_improvements"], idioma
        assert footer["release_200_fixes"], f"{idioma}: la 2.0.0 sin correcciones"
        # Ninguna nota de la serie 1 debe quedar suelta en los locales.
        assert not [clave for clave in footer if clave.startswith("release_1")], idioma


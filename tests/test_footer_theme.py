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


def test_release_135_is_current_and_localized():
    root = Path(__file__).resolve().parents[1]
    source = (root / "meteolabx.py").read_text(encoding="utf-8")
    server_source = (root / "server" / "__init__.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = "1.3.5"' in source
    assert "APP_BUILD = app_build_id()" in source
    assert '__version__ = "1.3.5"' in server_source
    assert "data-mlbx-whats-new-version='130' aria-selected='true'>1.3.5" in source
    assert "Build {html.escape(APP_BUILD)}" in source
    assert ".mlx-wn-build{" in source
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
    release_132_notes = {
        "es": "Optimización en la conexión a estaciones Meteocat.",
        "ca": "Optimització de la connexió a les estacions de Meteocat.",
        "en": "Optimized connections to Meteocat stations.",
        "fr": "Optimisation de la connexion aux stations Meteocat.",
        "it": "Ottimizzazione della connessione alle stazioni Meteocat.",
        "pt": "Otimização da ligação às estações Meteocat.",
    }
    release_133_notes = {
        "es": "Navegación entre pestañas más fluida y carga del mapa optimizada.",
        "ca": "Navegació entre pestanyes més fluida i càrrega del mapa optimitzada.",
        "en": "Smoother tab navigation and optimized map loading.",
        "fr": "Navigation plus fluide entre les onglets et chargement de la carte optimisé.",
        "it": "Navigazione più fluida tra le schede e caricamento della mappa ottimizzato.",
        "pt": "Navegação mais fluida entre separadores e carregamento do mapa otimizado.",
    }
    release_134_notes = {
        "es": "Añadido control de calidad de las observaciones para ECCC, SMHI, Frost, MeteoGalicia, Meteocat y NWS.",
        "ca": "Afegit el control de qualitat de les observacions per a ECCC, SMHI, Frost, MeteoGalicia, Meteocat i NWS.",
        "en": "Added observation quality control for ECCC, SMHI, Frost, MeteoGalicia, Meteocat, and NWS.",
        "fr": "Ajout du contrôle qualité des observations pour ECCC, SMHI, Frost, MeteoGalicia, Meteocat et NWS.",
        "it": "Aggiunto il controllo qualità delle osservazioni per ECCC, SMHI, Frost, MeteoGalicia, Meteocat e NWS.",
        "pt": "Adicionado o controlo de qualidade das observações para ECCC, SMHI, Frost, MeteoGalicia, Meteocat e NWS.",
    }
    release_134_fixes = {
        "es": "Corregidos los problemas de conexión de las estaciones NETATMO.",
        "ca": "Corregits els problemes de connexió de les estacions NETATMO.",
        "en": "Fixed NETATMO station connection issues.",
        "fr": "Correction des problèmes de connexion aux stations NETATMO.",
        "it": "Corretti i problemi di connessione alle stazioni NETATMO.",
        "pt": "Corrigidos os problemas de ligação às estações NETATMO.",
    }
    release_135_notes = {
        "es": [
            "Mejorada la interpolación del mapa global de temperatura.",
            "Arranque más estable y rápido.",
            "Navegación entre pestañas optimizada en dispositivos móviles.",
        ],
        "ca": [
            "Millorada la interpolació del mapa global de temperatura.",
            "Arrencada més estable i ràpida.",
            "Navegació entre pestanyes optimitzada en dispositius mòbils.",
        ],
        "en": [
            "Improved interpolation on the global temperature map.",
            "More stable and faster startup.",
            "Optimized tab navigation on mobile devices.",
        ],
        "fr": [
            "Amélioration de l’interpolation de la carte mondiale des températures.",
            "Démarrage plus stable et plus rapide.",
            "Navigation entre les onglets optimisée sur les appareils mobiles.",
        ],
        "it": [
            "Migliorata l'interpolazione della mappa globale delle temperature.",
            "Avvio più stabile e veloce.",
            "Navigazione tra le schede ottimizzata sui dispositivi mobili.",
        ],
        "pt": [
            "Melhorada a interpolação do mapa global de temperatura.",
            "Arranque mais estável e rápido.",
            "Navegação entre separadores otimizada em dispositivos móveis.",
        ],
    }
    release_135_fixes = {
        "es": [
            "Corregido un problema visual en el mapa de estaciones.",
            "Corregido un error que impedía que el gráfico de precipitación representara correctamente los datos acumulados.",
        ],
        "ca": [
            "Corregit un problema visual al mapa d'estacions.",
            "Corregit un error que impedia que el gràfic de precipitació representés correctament les dades acumulades.",
        ],
        "en": [
            "Fixed a visual issue on the station map.",
            "Fixed an issue that prevented the precipitation chart from correctly displaying accumulated data.",
        ],
        "fr": [
            "Correction d’un problème visuel sur la carte des stations.",
            "Correction d’une erreur empêchant le graphique des précipitations d’afficher correctement les données cumulées.",
        ],
        "it": [
            "Corretto un problema visivo nella mappa delle stazioni.",
            "Corretto un errore che impediva al grafico delle precipitazioni di mostrare correttamente i dati accumulati.",
        ],
        "pt": [
            "Corrigido um problema visual no mapa de estações.",
            "Corrigido um erro que impedia o gráfico de precipitação de apresentar corretamente os dados acumulados.",
        ],
    }
    for language, portuguese_name in portuguese_names.items():
        payload = json.loads((root / "locales" / f"{language}.json").read_text())
        footer = payload["footer"]
        assert footer["release_135_improvements"] == release_135_notes[language]
        assert footer["release_135_fixes"] == release_135_fixes[language]
        assert footer["release_134_improvements"] == [release_134_notes[language]]
        assert footer["release_134_fixes"] == [release_134_fixes[language]]
        assert footer["release_133_improvements"] == [release_133_notes[language]]
        assert footer["release_132_improvements"] == [release_132_notes[language]]
        assert len(footer["release_130_improvements"]) == 7
        assert portuguese_name in footer["release_130_improvements"][-1].lower()
        assert len(footer["release_130_fixes"]) == 2
        assert all(footer["release_130_fixes"])

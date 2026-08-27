"""Regresiones de la pestaña de predicción AROME."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("es", "en", "ca", "fr", "it", "pt")


def test_forecast_tab_is_registered_with_beta_badge_and_shareable_slug():
    source = (ROOT / "meteolabx.py").read_text(encoding="utf-8")

    assert '"forecast": "tabs.forecast"' in source
    assert '"forecast": "prediccion"' in source
    assert 'TAB_OPTIONS = ["observation", "trends", "historical", "map", "forecast", "ranking"]' in source
    assert 'forecast_tab_position = TAB_OPTIONS.index("forecast") + 1' in source
    assert 'content: "Beta"' in source
    assert 'METEOLABX_FORECAST_URL' in source
    assert '"/forecast"' in source
    assert "const isLocal = ['localhost', '127.0.0.1'].includes(host.location.hostname)" in source
    assert "'/forecast/forecast.html?v=20260825-54'" in source
    assert 'target = \'_blank\'' in source
    assert "trackedUrl.searchParams.set('from', 'streamlit')" in source
    assert "mlbx-forecast-external-link" in source
    assert 'elif tab_id == "forecast":' in source
    assert 'render_forecast_tab(_build_forecast_tab_context())' in source


def test_forecast_copy_exists_in_every_supported_language():
    required = {
        "section_title",
        "beta",
        "intro",
        "model_description",
        "shear_title",
        "shear_description",
        "ship_title",
        "ship_description",
        "status",
    }

    for language in LANGUAGES:
        payload = json.loads(
            (ROOT / "locales" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert payload["tabs"]["forecast"]
        assert required <= payload["forecast"].keys()


def test_svelte_forecast_is_a_standalone_entrypoint():
    forecast_html = (ROOT / "prototype-svelte" / "forecast.html").read_text(encoding="utf-8")
    forecast_app = (ROOT / "prototype-svelte" / "src" / "ForecastApp.svelte").read_text(
        encoding="utf-8"
    )
    vite_config = (ROOT / "prototype-svelte" / "vite.config.js").read_text(encoding="utf-8")

    assert 'id="forecast-app"' in forecast_html
    assert "ForecastView" in forecast_app
    assert "forecast.streamlit" in forecast_app
    assert "forecast.direct" in forecast_app
    assert "fetch('/v1/stats/section'" in forecast_app
    assert "window.history.replaceState" in forecast_app
    assert "const forecastHome = isLocal" in forecast_app
    assert "ObservationView" not in forecast_app
    assert "TrendsView" not in forecast_app
    assert "forecast: resolve(import.meta.dirname, 'forecast.html')" in vite_config


def test_forecast_map_selector_is_grouped_by_weather_type():
    products = (ROOT / "prototype-svelte" / "src" / "data" / "forecastProducts.js").read_text(
        encoding="utf-8"
    )
    view = (ROOT / "prototype-svelte" / "src" / "views" / "ForecastView.svelte").read_text(
        encoding="utf-8"
    )

    for category in (
        "Temperatura",
        "Precipitación",
        "Dinámica atmosférica",
        "Convección",
        "Humedad",
        "Nubosidad",
        "Radiación",
    ):
        assert f"label: '{category}'" in products

    assert "Temperatura a 500 hPa" in products
    assert "Temperatura a 850 hPa" in products
    assert "Precipitación en 1 hora" in products
    assert "Viento por niveles" in products
    assert "Cizalladura efectiva (EBWD)" in products
    assert "MU-ECAPE" in products
    assert "MUCAPE + MULI" in products
    assert "SHIP" in products
    assert "'wind-level'" in products
    assert "TOTAL PRECIPITATION · superficie · PT1H" in products
    assert "'precip-1h'" in products
    assert "initialProductIds" in products
    assert "item.kind === 'derived'" in view
    assert "mlx-logo.png" in view
    assert "Mapa visualizado" in view
    assert "Selector de mapas" in view
    assert "fetchAromeCatalog" in view
    assert "fetchAromeFrame" in view
    assert "getCachedAromeFrame" in view
    assert "}, 350);" not in view
    assert "ForecastGrid" in view
    assert "resetKey" in view
    assert (ROOT / "data" / "ne_50m_admin_1_states_provinces.geojson").exists()
    assert "timeZone: 'UTC'" in view
    assert "{valid.time} UTC" in view
    assert "CEST" not in view
    assert "Europe/Madrid" not in view
    assert "min-height:clamp(620px,64vh,780px)" in view
    assert "Nivel vertical del viento" in view
    assert "m AGL" in view
    assert "hPa" in view
    assert "windLevelKind" in view
    assert "runCatalogs" in view
    assert "selectedRun" in view
    assert "Progreso de cada RUN" in view
    assert "run-progress-list" in view
    assert "runProgress(item).toLocaleString('es-ES')" in view
    assert "productProgress(item)" in view
    assert "product-status {availability.state}" in view
    assert "availability.state === 'complete' ? '✓'" in view
    assert "class:ready={hourIsReady(hour)}" in view
    assert "UTC pendiente" in view

    api = (ROOT / "prototype-svelte" / "src" / "services" / "forecastApi.js").read_text(
        encoding="utf-8"
    )
    assert "FORECAST_DATA_REVISION" in api
    assert "forecast-fields-v16" in api
    assert "FRAME_CACHE_MAX_BYTES = 192 * 1024 * 1024" in api
    assert "shareFrameGeometry" in api
    assert "frameCacheBytes" in api
    assert "params.set('run', run)" in api
    production_start = (ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")
    assert "-m scripts.forecast_worker" in production_start
    assert "--isolate-tasks" in production_start
    assert 'METEOLABX_FORECAST_WORKERS:-6' in production_start
    # A 0 no hay tope fijo de perfiles convectivos: se intentan tantos como
    # workers y frena la memoria libre del momento, que es lo que sirve igual
    # en una máquina de 8 GB que en una de 24.
    assert 'METEOLABX_FORECAST_HEAVY_WORKERS:-0' in production_start
    assert "--watch" in production_start
    assert "METEOLABX_FORECAST_WORKER_INTERVAL_S:-60" in production_start
    assert "has_overlay" in api
    assert "togglePlayback" in view
    assert "nextReadyHourIndex" in view
    assert "hourIndex = nextIndex" in view

    grid = (ROOT / "prototype-svelte" / "src" / "components" / "ForecastGrid.svelte").read_text(
        encoding="utf-8"
    )
    assert "region-boundary" in grid
    assert "vector-overlay" in grid
    assert "vector-effect:non-scaling-stroke" in grid
    assert "precipitationPalette" in grid
    assert "Math.log1p" in grid
    assert "makeStreamlinePaths" in grid
    assert "integrateStream" in grid
    # Los glifos se calculan sobre el encuadre asentado, no sobre el gesto en
    # curso: integrarlos en cada pointermove bloqueaba el hilo principal.
    assert "baseSeedStep / viewZoom" in grid
    assert "visibleSourceBounds()" in grid
    assert "class=\"streamline\"" in grid
    assert "class=\"stream-particle\"" in grid
    assert "stream-direction" in grid
    assert "streamline-halo" in grid
    assert "prefers-reduced-motion" in grid
    assert "clusteredVector" in grid
    assert "visibleSourceBounds" in grid
    assert "scale(${(1 / zoom).toFixed(5)})" in grid
    assert "makeContourPaths" in grid
    assert "scalar-contour" in grid
    assert "zoomWithWheel" in grid
    assert "ondblclick" in grid


def test_every_selected_forecast_product_has_a_technical_guide():
    products_path = ROOT / "prototype-svelte" / "src" / "data" / "forecastProducts.js"
    guides_path = ROOT / "prototype-svelte" / "src" / "data" / "forecastProductGuides.js"
    view_path = ROOT / "prototype-svelte" / "src" / "views" / "ForecastView.svelte"
    forecast_app_path = ROOT / "prototype-svelte" / "src" / "ForecastApp.svelte"
    math_path = ROOT / "prototype-svelte" / "src" / "components" / "MathFormula.svelte"

    products = products_path.read_text(encoding="utf-8")
    guides = guides_path.read_text(encoding="utf-8")
    view = view_path.read_text(encoding="utf-8")
    forecast_app = forecast_app_path.read_text(encoding="utf-8")
    math = math_path.read_text(encoding="utf-8")

    selected_block = products.split("const initialProductIds = [", 1)[1].split("];", 1)[0]
    selected_ids = [
        line.strip().strip(",").strip("'")
        for line in selected_block.splitlines()
        if line.strip().startswith("'")
    ]
    # Los mapas publicados; sube al añadir uno nuevo al selector.
    assert len(selected_ids) == 23
    for product_id in selected_ids:
        assert f"'{product_id}': {{" in guides or f"  {product_id}: {{" in guides

    for heading in ("Qué representa", "Interpretación", "Cálculo", "Base documental"):
        assert heading in view
    assert ".explanation-overview{display:grid;grid-template-columns:1fr;" in view
    assert 'class="calculation-layout"' not in view
    assert 'class="formula-stack"' not in view
    assert ".technical-sources{display:grid;grid-template-columns:1fr;" in view
    assert "forecastProductGuides[id]" in products
    assert "guide.equations" in view
    assert "MathFormula" in view
    assert "katex.renderToString" in math
    assert "Rejilla nativa conectada" not in view
    assert 'title="Restablecer encuadre"' not in view
    assert 'title="Descargar imagen"' not in view
    assert "Máximo en el dominio" not in view
    assert "domain-maximum" not in view
    assert "Catálogo AROME sincronizado" not in view
    assert "catalog-note" not in view
    assert 'class="heat ' not in view
    assert "radial-gradient" not in forecast_app
    assert "MODELO ACTIVO" not in forecast_app
    assert "model-chip" not in forecast_app
    assert "let selectedProduct = $state(null)" in view
    assert "Ningún mapa seleccionado" in view
    assert "empty-forecast" in view
    assert "map-watermark" in view
    assert view.count("Predicción numérica") >= 3
    assert "Mapa conceptual" not in view
    assert "catalunya" not in view
    assert "map-badge" not in view
    assert "Vista conceptual" not in view

    # Las fichas deben revelar las decisiones importantes de los diagnósticos MLX.
    assert "No llama a params.cape de SHARPpy" in guides
    assert "sharppy.sharptab.params.ship" in guides
    assert "sin corrección de temperatura virtual" in guides
    assert "SHIP no usa EBWD" in guides
    assert "No es Bunkers ni Corfidi" in guides
    assert "Météo-France · API ciblée modèles" in guides


def test_forecast_build_can_be_installed_into_streamlit_static_dir(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/install_forecast_frontend.py",
            "--target-static-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    installed = tmp_path / "forecast"
    assert (installed / "forecast.html").is_file()
    assert (installed / "index.html").is_file()
    assert (installed / "index.html").read_bytes() == (installed / "forecast.html").read_bytes()
    assert (installed / "assets").is_dir()
    assert "Frontend Svelte instalado" in result.stdout


def test_production_streamlit_runner_registers_clean_forecast_route_first():
    runner = (ROOT / "scripts" / "run_streamlit.py").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")
    local_start = (ROOT / "scripts" / "run_app.sh").read_text(encoding="utf-8")

    assert 're.compile(r"^/forecast/?$")' in runner
    assert 're.compile(r"^/v1/stats/section$")' in runner
    assert "ForecastStatsProxyHandler" in runner
    assert "app.wildcard_router.rules.insert(0, route)" in runner
    assert 'Content-Type", "text/html; charset=UTF-8' in runner
    assert "decompress_response=False" in runner
    assert '"Content-Encoding"' in runner
    assert '"Accept-Encoding"' in runner
    assert 'scripts/run_streamlit.py meteolabx.py' in start
    assert 'scripts/run_streamlit.py meteolabx.py' in local_start


def test_the_api_accepts_every_published_product():
    """El patrón del endpoint sale del catálogo, no de una lista paralela.

    Escrito a mano se quedaba atrás al añadir un mapa: la API respondía 422 y
    su detalle es una lista de objetos, que el visor mostraba como
    «[object Object]» sin decir qué pasaba.
    """
    import re

    from server.routers.forecast import FORECAST_PRODUCT_PATTERN
    from server.services.forecast_store import PERSISTED_FORECAST_PRODUCTS

    rechazados = [
        product
        for product in PERSISTED_FORECAST_PRODUCTS
        if not re.match(FORECAST_PRODUCT_PATTERN, product)
    ]
    assert not rechazados, f"la API rechazaría {rechazados}"
    # Y sigue rechazando lo que no existe.
    assert not re.match(FORECAST_PRODUCT_PATTERN, "no-existe")
    assert not re.match(FORECAST_PRODUCT_PATTERN, "temperature-2m; drop")


def test_the_viewer_can_describe_a_validation_error():
    """El visor traduce el detalle de FastAPI en vez de interpolar el objeto."""
    from pathlib import Path

    api = (
        Path(__file__).resolve().parents[1]
        / "prototype-svelte" / "src" / "services" / "forecastApi.js"
    ).read_text(encoding="utf-8")

    assert "function describeApiDetail" in api
    assert "describeApiDetail(payload.detail)" in api


def test_wcs_metadata_is_shared_between_processes(tmp_path, monkeypatch):
    """GetCapabilities y DescribeCoverage se guardan en disco, no por proceso.

    Cada trabajo se aísla en su propio intérprete, así que la caché en memoria
    no le sirve al siguiente. Medido en producción, rehacer esos metadatos
    costaba 46 minutos por pasada, con catálogos de hasta 60 s.
    """
    from tabs import arome_forecast as arome

    monkeypatch.setattr(arome.tempfile, "gettempdir", lambda: str(tmp_path))
    llamadas = []

    def api_falsa(url, params, token):
        llamadas.append(url)
        return b"<xml/>", "application/xml"

    monkeypatch.setattr(arome, "_api_get", api_falsa)
    parametros = (("service", "WCS"), ("version", "2.0.1"))

    primera = arome._api_get_metadata("https://x/GetCapabilities", parametros, "t")
    segunda = arome._api_get_metadata("https://x/GetCapabilities", parametros, "t")

    assert primera[0] == segunda[0] == b"<xml/>"
    assert len(llamadas) == 1, "la segunda debe salir del disco"

    # Una consulta distinta no reutiliza la anterior.
    arome._api_get_metadata("https://x/DescribeCoverage", parametros, "t")
    assert len(llamadas) == 2


def test_expired_wcs_metadata_is_fetched_again(tmp_path, monkeypatch):
    """Caducado se vuelve a pedir: el catálogo cambia cuando el modelo publica."""
    from tabs import arome_forecast as arome

    monkeypatch.setattr(arome.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(arome, "METADATA_CACHE_TTL_S", 0)
    llamadas = []
    monkeypatch.setattr(
        arome, "_api_get",
        lambda url, params, token: (llamadas.append(url), (b"<xml/>", "x"))[1],
    )

    arome._api_get_metadata("https://x/GetCapabilities", (), "t")
    arome._api_get_metadata("https://x/GetCapabilities", (), "t")

    assert len(llamadas) == 2

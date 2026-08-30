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
    assert '<link rel="canonical" href="https://www.meteolabx.com/forecast"' in forecast_html
    # El texto exacto es cosa de SEO y se ajusta; lo que no puede faltar es un
    # título propio con el nombre del modelo y la marca, ni la descripción que
    # Google enseña debajo.
    import re as _re

    titulo = _re.search(r"<title>([^<]+)</title>", forecast_html)
    assert titulo and "AROME" in titulo.group(1) and "MeteoLabX" in titulo.group(1)
    assert len(titulo.group(1)) <= 60, "Google recorta el título sobre los 60"
    descripcion = _re.search(
        r'<meta name="description" content="([^"]+)"', forecast_html
    )
    assert descripcion and len(descripcion.group(1)) <= 160
    # La página tiene que decir de qué va sin depender de JavaScript: es lo
    # único que ve un rastreador que no ejecuta el visor.
    assert "<h1>" in forecast_html and "AROME" in forecast_html
    assert "supercélulas" in forecast_html
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

    # Los dos isobáricos llevan el geopotencial encima, y el nombre lo dice:
    # quien busca isohipsas no tiene por qué adivinar que están ahí.
    assert "Temperatura y geopotencial 500 hPa" in products
    assert "Temperatura y geopotencial 850 hPa" in products
    # Y el subtítulo dice lo que lleva encima, no solo su categoría.
    assert "contents: 'Temperatura · Geopotencial'" in products
    # La capa superpuesta se nombra desde el producto y no a mano en el globo,
    # que decía «LI 584,8 dam» al poner el geopotencial debajo. Los CAPE la
    # nombran porque conviven tres índices; en un mapa de geopotencial, un
    # valor en dam no necesita presentación.
    assert "overlay: 'MULI'" in products
    # El mapa de masas de aire: theta-e en color, isobaras encima y sus centros.
    assert "'mslp-theta-e-850'" in products
    assert "overlaySmoothing: 0" in products, "las isobaras van sin suavizar"
    assert "pressureCentres: true" in products
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
    # Los frames se sirven con `immutable` y un año de caché, así que esta
    # revisión es lo único que hace que un navegador vuelva a pedirlos. Sube
    # con cada cambio de formato de la rejilla: la v17 es la que trae la capa
    # de geopotencial, y sin subirla el cambio no llega a quien ya tenga la
    # hora guardada, ni recargando ni reiniciando.
    assert "forecast-fields-v17" in api
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
    assert "contourLines" in grid
    assert "scalar-contour" in grid
    # Las isolíneas del propio campo van discontinuas y con halo claro: sobre
    # los extremos de la paleta, que son azul y granate oscuros, una línea
    # negra fina desaparece.
    assert "value-contour" in grid
    assert "stroke-dasharray" in grid
    assert "value-contour-halo" in grid
    # El suelo del cero es solo para la precipitación acumulada. Aplicado con
    # un 0 por defecto, descartaba el campo entero de cualquier mapa negativo:
    # la temperatura de 500 hPa se quedó sin color.
    assert "zeroFloor > 0 ? zeroFloor : -Infinity" in grid
    # El trazo discontinuo va en píxeles de pantalla, así que a poco zoom una
    # raya fina se lee como línea continua: el patrón depende del encuadre.
    assert "contourDash" in grid
    assert "style:stroke-dasharray" in grid
    # Sobre un campo ya cruzado de isotermas, las divisiones interiores
    # compiten con ellas: esos mapas se quedan con costas y fronteras
    # nacionales, que son 146 de los 519 anillos del dominio.
    assert "nationalBoundariesOnly" in grid
    # Isohipsas: continuas, con paso propio y grosor que depende del encuadre.
    # Fijo, el que se lee de lejos se convierte en un chorizo al ampliar.
    assert "height-contour" in grid
    assert "overlayWidth" in grid
    assert "isMajorOverlay" in grid
    # Capas apagables desde el propio mapa, con la elección recordada.
    assert "layer-panel" in grid
    # La capa superpuesta se llama distinto según el campo: isohipsas sobre
    # geopotencial, isobaras sobre presión.
    assert "overlayLayerLabel" in grid
    assert "detectPressureCentres" in grid
    # Mayúscula para el centro principal y minúscula para el relativo, como en
    # los mapas de AEMET. Lo decide el cierre, medido por inundación.
    assert "centre.main ? 'B' : 'b'" in grid
    centros = (ROOT / "prototype-svelte" / "src" / "lib" / "pressureCentres.js").read_text(encoding="utf-8")
    assert "closureDepth" in centros
    assert "CENTRE_MAIN_DEPTH_HPA = 4" in centros
    assert "CENTRE_MAIN_RADIUS_KM = 150" in centros
    assert "ringExtreme" not in centros, (
        "el anillo fijo medía el sector que más favorecía al candidato"
    )
    # Un solo reparto para todos los rótulos: con uno por familia, cada una
    # esquivaba los suyos y los dos salían impresos uno encima del otro.
    assert "mapLabels" in grid
    assert "LI {hover.overlay" not in grid, "el nombre de la capa no puede ir a mano"
    assert "contourLabels" not in grid and "overlayLabels" not in grid
    assert "toggleLayer" in grid
    assert "showIsotherms" in grid and "showIsohypses" in grid and "showTroughs" in grid
    capas = (ROOT / "prototype-svelte" / "src" / "lib" / "layerPreferences.svelte.js").read_text(encoding="utf-8")
    assert "mlx-forecast-layers" in capas
    assert "localStorage.setItem" in capas
    assert "zoomWithWheel" in grid
    assert "ondblclick" in grid


def test_the_map_percentage_is_measured_against_its_final_total():
    """El visor mide contra el horizonte final, no contra lo ya publicado."""
    vista = (ROOT / "prototype-svelte" / "src" / "views" / "ForecastView.svelte").read_text(encoding="utf-8")

    assert "Number(metadata?.expected_total) || expectedTimes.length" in vista, (
        "el denominador tiene que ser el total final, con lo anunciado de respaldo"
    )


def test_every_list_the_grid_template_iterates_is_declared():
    """Lo que recorre la plantilla tiene que existir en el script.

    Svelte no se queja al compilar de un identificador que no declara nadie:
    trata la plantilla como si pudiera venir de un global. El fallo sale en el
    navegador y de la peor manera, porque el error ocurre al pintar el frame ya
    descargado y el visor se queda con el cartel de «Cargando» puesto: sin
    error visible, con el mapa en el aire y la red diciendo 200.
    """
    import re

    fuente = (ROOT / "prototype-svelte" / "src" / "components" / "ForecastGrid.svelte").read_text(encoding="utf-8")
    script, plantilla = fuente.split("</script>", 1)
    plantilla = plantilla.split("<style>", 1)[0]

    declarados = set(re.findall(r"(?:const|let|function)\s+([A-Za-z_$][\w$]*)", script))
    declarados |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*[,}]", script.split("$props()")[0].split("let {")[-1]))
    recorridos = set(re.findall(r"\{#each\s+([A-Za-z_$][\w$]*)", plantilla))

    huerfanos = sorted(recorridos - declarados)
    assert not huerfanos, f"la plantilla recorre algo que no existe: {huerfanos}"


def test_the_trough_detector_follows_the_six_steps():
    """El detector de vaguadas hace lo que dice hacer, en orden.

    El criterio no es la curvatura a secas: la fórmula divide por el cubo del
    gradiente, así que en un campo plano una ondulación de un decámetro sale
    con la curvatura de una vaguada. Lo que se busca es curvatura por
    intensidad del flujo, que es el término de curvatura de la vorticidad
    geostrófica: el mismo giro pesa en una corriente fuerte y no en un campo
    parado.
    """
    fuente = (ROOT / "prototype-svelte" / "src" / "lib" / "troughs.js").read_text(encoding="utf-8")

    assert "coarsen" in fuente, "1. engrosado antes del suavizado sinóptico"
    assert "TROUGH_SIGMA_KM = 50" in fuente, "1. sigma de escala sinóptica"
    assert "curvatureVorticity" in fuente, "2. curvatura por intensidad del flujo"
    assert "geostrophicVorticity" in fuente
    assert "curvaturePeaks" in fuente, "3. máximos sobre cada isohipsa"
    assert "chainAxes" in fuente, "4. encadenado"
    assert "TROUGH_MIN_LENGTH_KM = 350" in fuente, "5. poda por longitud"
    # La cadena se siembra por el pico más marcado y crece hacia las dos
    # isohipsas vecinas. Sembrando en orden de barrido el resultado no era
    # monótono con el umbral, y creciendo en un solo sentido el eje se partía
    # en dos mitades que no llegaban a la longitud mínima.
    assert "semillas.sort" in fuente
    assert "crecer(nivel, semilla, -1)" in fuente
    assert "closedLows" in fuente, "6. depresiones cerradas aparte"
    # La curvatura ciclónica no basta: los hombros de una dorsal también curvan
    # hacia ese lado —un bulto tiene la cima convexa y los flancos cóncavos— y
    # el detector los marcaba como ejes. Hace falta que la onda exista: que la
    # isohipsa quede al sur de lo que hace a los dos lados.
    assert "waveAmplitude" in fuente
    assert "TROUGH_MIN_AMPLITUDE_KM = 150" in fuente
    assert "centro - Math.max(oeste, este)" in fuente, (
        "contra el lado menos favorable, no contra la media de los dos"
    )
    # Y la geometría: sin codos, recta por PCA con pocos vértices y curva con
    # muchos, uniendo antes las cadenas que son el mismo eje partido.
    assert "TROUGH_MAX_DRIFT_DEG" in fuente
    assert "pcaLine" in fuente and "smoothAxis" in fuente
    assert "mergeChains" in fuente
    # El gradiente mínimo es lo que evita los ejes fantasma de un campo plano.
    assert "TROUGH_MIN_GRADIENT" in fuente


def test_the_contour_pipeline_cleans_the_field_before_drawing():
    """Las isolíneas no se trazan sobre el campo crudo.

    Sin suavizar, siguen el color celda a celda y llenan el mapa de anillos de
    dos o tres celdas que no dicen nada. El orden importa: gaussiano primero,
    después el trazado, y solo al final se tiran los anillos diminutos y se
    simplifica lo que queda.
    """
    contornos = (ROOT / "prototype-svelte" / "src" / "lib" / "contours.js").read_text(encoding="utf-8")

    assert "gaussianBlur" in contornos
    assert "CONTOUR_SIGMA = 1.75" in contornos, "sigma fuera del rango pedido"
    assert "linkSegments" in contornos
    assert "ringArea" in contornos
    assert "CONTOUR_MIN_RING_CELLS = 20" in contornos
    assert "simplify" in contornos
    assert "CONTOUR_TOLERANCE = 0.8" in contornos
    assert "CONTOUR_LABEL_MIN_LENGTH" in contornos
    # El suavizado tiene que renormalizarse con el peso de las muestras válidas:
    # fuera del dominio no hay dato y la media arrastraría el borde al vacío.
    assert "weights > 0 ? total / weights : NaN" in contornos


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
    assert len(selected_ids) == 29
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


def test_installing_the_frontend_removes_the_previous_bundle(tmp_path):
    """El destino no puede quedarse con el bundle de la instalación anterior.

    Streamlit sirve /forecast desde su propio `static`, y la instalación solo
    copiaba encima. Con un nombre distinto por build, el directorio acumulaba
    un bundle por compilación —sesenta y uno, trece megas, en una instalación
    local— y bastaba que el `forecast.html` no se refrescara para que el
    navegador siguiera ejecutando un visor viejo contra frames nuevos.
    """
    from scripts.install_forecast_frontend import install_forecast_frontend

    instalado = install_forecast_frontend(tmp_path)
    intruso = instalado / "assets" / "forecast-VIEJO0000.js"
    intruso.write_text("// build anterior")

    install_forecast_frontend(tmp_path)

    assert not intruso.exists(), "el bundle anterior sigue servible"
    actuales = {path.name for path in (ROOT / "static" / "forecast_app" / "assets").iterdir()}
    assert {path.name for path in (instalado / "assets").iterdir()} == actuales


def test_the_local_launcher_installs_the_current_build():
    """Compilar el visor tiene que llegar al navegador sin pasos manuales.

    `start_web.sh` ya lo instala en el contenedor; en local faltaba, así que un
    `npm run build:forecast` no se veía en :8501 y costaba media hora entender
    por qué el mapa salía con ruido.
    """
    local_start = (ROOT / "scripts" / "run_app.sh").read_text(encoding="utf-8")

    assert "scripts/install_forecast_frontend.py" in local_start


def test_production_streamlit_runner_registers_clean_forecast_route_first():
    runner = (ROOT / "scripts" / "run_streamlit.py").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")
    local_start = (ROOT / "scripts" / "run_app.sh").read_text(encoding="utf-8")

    assert 're.compile(r"^/forecast/?$")' in runner
    assert 're.compile(r"^/v1/stats/(?:section|seo-view)$")' in runner
    assert "PublicStatsProxyHandler" in runner
    assert 'self.request.path == "/forecast/"' in runner
    assert 'self.redirect("/forecast", permanent=True)' in runner
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


def test_the_proxy_does_not_send_a_body_with_204():
    """Tornado rechaza un cuerpo en un 204, aunque venga vacío.

    Las métricas de visita responden 204, así que cada una dejaba un
    «AssertionError: Cannot send body with 204» en el log del servidor. La
    petición del navegador se completaba igual, pero el ruido escondía errores
    de verdad.
    """
    import inspect
    from pathlib import Path

    fuente = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_streamlit.py"
    ).read_text(encoding="utf-8")

    assert "response.code in (204, 304)" in fuente, (
        "las respuestas sin cuerpo tienen que cerrarse sin él"
    )


def test_the_health_endpoint_is_reachable_from_outside():
    """El estado del backend tiene que llegar al exterior.

    Sin ruta pública, la plataforma no puede saber que el servicio dejó de
    responder y devuelve 502 hasta que alguien lo mira. Pasa por el mismo
    proxy que el resto, así que sólo contesta si el backend está vivo.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    proxy = (raiz / "scripts" / "run_streamlit.py").read_text(encoding="utf-8")
    despliegue = (raiz / "railway.toml").read_text(encoding="utf-8")

    assert 'r"^/v1/health/?$"' in proxy
    assert "health_route" in proxy
    assert 'healthcheckPath = "/v1/health"' in despliegue


def test_the_worker_runs_at_lower_priority():
    """El cálculo cede la CPU a la web cuando compiten.

    Con siete procesos saturando los núcleos, una visita esperaba detrás de
    ellos. `nice` no les quita tiempo mientras sobra; sólo los adelanta cuando
    hay competencia.
    """
    from pathlib import Path

    arranque = (
        Path(__file__).resolve().parents[1] / "scripts" / "start_web.sh"
    ).read_text(encoding="utf-8")

    assert "nice -n" in arranque
    assert "METEOLABX_FORECAST_WORKER_NICE" in arranque

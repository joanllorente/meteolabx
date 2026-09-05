<script>
  import {
    Calendar, ChevronDown, ChevronLeft, ChevronRight, Download,
    Info, Layers, Maximize2, Pause, Play, RefreshCw, Search
  } from '@lucide/svelte';
  import { onMount } from 'svelte';
  import ForecastGrid from '../components/ForecastGrid.svelte';
  import MathFormula from '../components/MathFormula.svelte';
  import {
    DEFAULT_FORECAST_MODEL, catalogSummaryFor, forecastCategories, forecastModels,
    productsForModel
  } from '../data/forecastProducts.js';
  import { activeUnit, formatBound, formatValue, unitFamilyOf, unitLabel, unitOptions } from '../lib/units.js';
  import { chooseUnit, unitPreferences } from '../lib/unitPreferences.svelte.js';
  import { bandHexColors, defaultPalette, precipitationPalette } from '../lib/palettes.js';
  import { fetchForecastCatalog, fetchForecastFrame, getCachedForecastFrame, prefetchForecastFrames } from '../services/forecastApi.js';
  import { exportarMapaPng } from '../lib/mapExport.js';
  import { forecastLocale, forecastText, localizedForecastCategories, localizedForecastProducts } from '../lib/forecast-i18n.js';
  import { loadForecastGuides, localizedForecastGuide } from '../lib/forecast-guides.svelte.js';

  let { language = 'es' } = $props();
  const tr = $derived((key, params = {}) => forecastText(language, key, params));
  const locale = $derived(forecastLocale(language));

  const assetBase = import.meta.env.BASE_URL;
  const hours = Array.from({ length: 18 }, (_, i) => ({
    horizon: i * 3,
    day: i < 7 ? 'Lun 24' : i < 15 ? 'Mar 25' : 'Mié 26',
    time: `${String((5 + i * 3) % 24).padStart(2, '0')}:00`
  }));

  let selectedModel = $state(DEFAULT_FORECAST_MODEL);
  let selectedProduct = $state(null);
  let expandedCategory = $state('dynamics');
  let search = $state('');
  let hourIndex = $state(5);
  let playing = $state(false);
  let catalog = $state.raw(null);
  let selectedRun = $state('');
  let catalogError = $state('');
  let frameData = $state.raw(null);
  let frameLoading = $state(false);
  let framePending = $state(false);
  let frameError = $state('');
  let windLevelKind = $state('height');
  let windLevel = $state(10);
  let unitMenuOpen = $state(false);
  let mapResetKey = $state(0);
  let mapContainer = $state();
  // Blanco o negro, según lo que haya bajo la marca de agua. Lo mide el
  // componente del mapa, que es quien tiene los píxeles.
  let mapInk = $state('');
  let exporting = $state(false);
  let exportError = $state('');
  let frameRequest = null;
  let catalogRequest = null;

  /**
   * Descarga el mapa tal y como está en pantalla.
   *
   * Se exporta la tarjeta entera —cabecera y mapa—, así que el PNG lleva
   * impresos el nombre del producto y la hora válida, que es lo que hace que
   * una captura suelta siga siendo legible meses después.
   */
  async function downloadPng() {
    if (!mapContainer || !frameData || exporting) return;
    exporting = true;
    exportError = '';
    try {
      const sello = String(valid?.iso || '').replace(/[-:]/g, '');
      await exportarMapaPng(mapContainer.closest('.map-card'), {
        nombre: `meteolabx-${selectedModel}-${product.id}-${sello}`
      });
    } catch (error) {
      exportError = error.message;
    } finally {
      exporting = false;
    }
  }

  function toggleFullscreen() {
    if (!mapContainer) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else mapContainer.requestFullscreen?.();
  }

  const model = $derived(forecastModels.find((item) => item.id === selectedModel) || forecastModels[0]);
  const modelProducts = $derived(localizedForecastProducts(productsForModel(selectedModel), language));
  const categories = $derived(localizedForecastCategories(forecastCategories, language));
  const modelSummary = $derived(catalogSummaryFor(selectedModel));
  const product = $derived(modelProducts.find((item) => item.id === selectedProduct) || modelProducts[0]);
  const rawGuide = $derived(product.guide || {
    what: product.description,
    interpretation: [product.description],
    method: product.method,
    equations: [],
    steps: [],
    sources: []
  });
  // Las guías del idioma se piden al vuelo la primera vez que hacen falta.
  $effect(() => loadForecastGuides(language));
  const guide = $derived(localizedForecastGuide(rawGuide, product, language));
  const runCatalogs = $derived(catalog?.runs?.length ? catalog.runs : (catalog ? [{
    run: Object.values(catalog.products || {})[0]?.run || '',
    products: catalog.products || {},
    publication: catalog.publication || {}
  }] : []));
  const selectedRunCatalog = $derived(runCatalogs.find((item) => item.run === selectedRun) || runCatalogs[0] || null);
  const connectedProduct = $derived(selectedRunCatalog?.products?.[product.id] || null);
  const precomputedOnly = $derived(Boolean(selectedRunCatalog?.publication?.precomputed_only));
  const activeHours = $derived(connectedProduct ? connectedProduct.valid_times.map((value) => {
    const date = new Date(value);
    const run = new Date(connectedProduct.run);
    return {
      iso: value,
      horizon: Math.round((date - run) / 3_600_000),
      day: new Intl.DateTimeFormat(locale, { weekday: 'short', day: '2-digit', timeZone: 'UTC' }).format(date),
      time: new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(date)
    };
  }) : hours);
  const valid = $derived(activeHours[Math.min(hourIndex, activeHours.length - 1)] || hours[0]);
  const selectedFrameReady = $derived(
    !precomputedOnly
      || (connectedProduct?.available_times || []).includes(valid?.iso)
  );
  const selectedCategory = $derived(categories.find((item) => item.id === product.category));
  // Qué enseña el mapa. Por defecto su categoría, que para la mayoría es
  // descripción bastante; los que llevan más de un campo encima lo dicen.
  const productContents = $derived(product.contents || selectedCategory?.label || '');
  const windLevels = $derived(connectedProduct?.levels?.[windLevelKind] || []);
  const displayedWindLevels = $derived(windLevelKind === 'height' ? [...windLevels].reverse() : windLevels);
  const windLevelUnit = $derived(windLevelKind === 'height' ? 'm AGL' : 'hPa');
  const mapProductLabel = $derived(product.id === 'wind-level' ? `${product.label} · ${windLevel} ${windLevelUnit}` : product.label);
  // Unidad de presentación: no toca el frame ni la escala de color, solo los
  // números que se escriben en la leyenda y en el globo del cursor.
  const displayUnit = $derived(activeUnit(product, unitPreferences));
  const displayUnitLabel = $derived(displayUnit ? unitLabel(product, displayUnit) : product.unit);
  const displayUnitOptions = $derived(unitOptions(product));
  const legendMin = $derived(formatBound(product.min, product, displayUnit));
  const legendMax = $derived(formatBound(product.max, product, displayUnit));
  // Leyenda por clases: los mismos colores y los mismos cortes que pinta el
  // ráster, para que la barra no describa una escala que el mapa no usa.
  const legendBands = $derived(
    product.scaleBreaks?.length
      ? bandHexColors(
          product.palette === 'precipitation' ? precipitationPalette : defaultPalette,
          product.scaleBreaks.length + 1
        )
      : []
  );
  const legendMarks = $derived.by(() => {
    if (!legendBands.length) return [];
    const cortes = [0, ...product.scaleBreaks];
    return cortes.map((value, index) => ({
      at: index / legendBands.length * 100,
      label: formatBound(value, product, displayUnit)
    }));
  });
  // Rótulo de isolínea: sin decimales y con la unidad pegada, que va dentro
  // del mapa y compite por sitio con el propio campo.
  const formatContour = $derived(
    (value) => `${formatValue(value, product, displayUnit, 0)}${displayUnitLabel}`
  );
  const formatProbe = $derived(
    (value) => `${formatValue(value, product, displayUnit, product.id === 'ship' ? 2 : undefined)} ${displayUnitLabel}`.trim()
  );
  const runLabel = $derived(connectedProduct
    ? `${new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(connectedProduct.run))} UTC`
    : '24/08 · 03:00 UTC');
  const latestRunLabel = $derived(runCatalogs[0]?.run
    ? `${new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(runCatalogs[0].run))} UTC`
    : runLabel);
  const latestProgress = $derived(runCatalogs[0]?.publication?.progress || null);
  const currentJobLabel = $derived.by(() => {
    const current = latestProgress?.current_job;
    if (!current) return '';
    const grupos = {
      'convective-group': 'Diagnósticos convectivos',
      'shear-group': 'Cizalladuras',
      'mixed-group': 'Varios mapas'
    };
    if (grupos[current.product]) return grupos[current.product];
    return modelProducts.find((item) => item.id === current.product)?.label || current.product;
  });
  const latestRunShortLabel = $derived(
    runCatalogs[0]?.run ? shortRunLabel(runCatalogs[0].run) : latestRunLabel
  );
  const latestStatusLabel = $derived.by(() => {
    if (catalogError) return tr('backendUnavailable', { model: model.short });
    if (!catalog) return tr('connecting');
    if (!latestProgress?.frames_total) return tr('available');
    const complete = latestProgress.frames_available >= latestProgress.frames_total;
    if (complete) return tr('complete');
    const percent = Number(latestProgress.percent || 0).toLocaleString(locale);
    return currentJobLabel ? `${percent} % · ${currentJobLabel}` : `${percent} %`;
  });
  const visibleCategories = $derived.by(() => {
    const term = search.trim().toLocaleLowerCase(locale);
    return categories
      .map((category) => ({
        ...category,
        products: modelProducts.filter((item) => item.category === category.id && (!term || `${item.label} ${item.short}`.toLocaleLowerCase(locale).includes(term)))
      }))
      .filter((category) => category.products.length);
  });

  function selectProduct(item) {
    playing = false;
    unitMenuOpen = false;
    selectedProduct = item.id;
    expandedCategory = item.category;
    const count = selectedRunCatalog?.products?.[item.id]?.valid_times?.length || hours.length;
    hourIndex = Math.min(hourIndex, count - 1);
  }

  function selectModel(modelId) {
    if (modelId === selectedModel) return;
    playing = false;
    unitMenuOpen = false;
    selectedModel = modelId;
    // Ni el producto ni el RUN ni la hora se pueden heredar: cada modelo
    // publica los suyos, y arrastrarlos dejaba el visor pidiendo un mapa que
    // el otro modelo no tiene.
    const first = productsForModel(modelId)[0];
    selectedProduct = first?.id || null;
    expandedCategory = first?.category || 'dynamics';
    selectedRun = '';
    hourIndex = 0;
    catalog = null;
    frameData = null;
    catalogError = '';
    refreshCatalog();
  }

  function selectRun(run) {
    playing = false;
    selectedRun = run;
    const next = runCatalogs.find((item) => item.run === run)?.products?.[product.id];
    hourIndex = Math.min(hourIndex, Math.max(0, (next?.valid_times?.length || hours.length) - 1));
    frameData = null;
  }

  function shortRunLabel(run) {
    const value = new Date(run);
    const day = new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', timeZone: 'UTC' }).format(value);
    const hour = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(value);
    return `${day} · ${hour}Z`;
  }

  function runProgress(item) {
    return Number(item?.publication?.progress?.percent || 0);
  }

  function productProgress(item) {
    const metadata = selectedRunCatalog?.products?.[item.id];
    const expectedTimes = [...new Set(metadata?.valid_times || [])];
    const expected = new Set(expectedTimes);
    const available = [...new Set(metadata?.available_times || [])]
      .filter((value) => expected.has(value)).length;
    // El denominador es el horizonte final del mapa, no las horas que
    // Météo-France lleva publicadas: esas crecen durante la pasada, así que un
    // mapa marcaba «Completo» con doce plazos y volvía a bajar al aparecer los
    // siguientes. Si el manifiesto no lo trae —una foto local, por ejemplo—,
    // lo anunciado es el total.
    const total = Number(metadata?.expected_total) || expectedTimes.length;
    const percent = total ? Math.min(100, Math.round(available * 100 / total)) : 0;
    return {
      available,
      total,
      percent,
      state: total > 0 && available >= total ? 'complete' : available > 0 ? 'partial' : 'pending',
      label: total > 0 && available >= total ? tr('completed') : available > 0 ? `${percent} %` : tr('pending')
    };
  }

  function hourIsReady(hour) {
    return !precomputedOnly || (connectedProduct?.available_times || []).includes(hour?.iso);
  }

  function pickUnit(unit) {
    const family = unitFamilyOf(product);
    if (family) chooseUnit(family.id, unit);
    unitMenuOpen = false;
  }

  function selectWindKind(kind) {
    windLevelKind = kind;
    const levels = connectedProduct?.levels?.[kind] || [];
    const preferred = kind === 'height' ? 10 : 850;
    windLevel = levels.includes(preferred) ? preferred : levels[0] || preferred;
  }

  function toggleCategory(categoryId) {
    expandedCategory = expandedCategory === categoryId ? '' : categoryId;
  }

  function step(delta) {
    hourIndex = Math.max(0, Math.min(activeHours.length - 1, hourIndex + delta));
  }

  function nextReadyHourIndex(fromIndex) {
    return activeHours.findIndex((hour, index) => index > fromIndex && hourIsReady(hour));
  }

  function togglePlayback() {
    if (playing) {
      playing = false;
      return;
    }
    if (nextReadyHourIndex(hourIndex) < 0) {
      const firstReady = activeHours.findIndex(hourIsReady);
      if (firstReady >= 0) hourIndex = firstReady;
    }
    playing = true;
  }

  function refreshCatalog() {
    catalogRequest?.abort();
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 20_000);
    catalogRequest = controller;
    const requestedModel = selectedModel;
    fetchForecastCatalog({ model: requestedModel, signal: controller.signal })
      .then((payload) => {
        // Un catálogo que llega tarde, después de cambiar de modelo, no debe
        // pisar al del modelo que ya está en pantalla.
        if (controller.signal.aborted || requestedModel !== selectedModel) return;
        catalog = payload;
        const availableRuns = payload.runs || [];
        if (!availableRuns.some((item) => item.run === selectedRun)) {
          selectedRun = availableRuns[0]?.run || Object.values(payload.products || {})[0]?.run || '';
        }
        catalogError = '';
      })
      .catch((error) => {
        if (requestedModel !== selectedModel) return;
        if (timedOut) catalogError = tr('catalogTimeout', { model: requestedModel.toUpperCase() });
        else if (error.name !== 'AbortError') catalogError = error.message;
      })
      .finally(() => window.clearTimeout(timeout));
  }

  onMount(() => {
    refreshCatalog();
    const catalogTimer = window.setInterval(refreshCatalog, 30_000);
    return () => {
      window.clearInterval(catalogTimer);
      catalogRequest?.abort();
      frameRequest?.abort();
    };
  });

  $effect(() => {
    const selectedId = selectedProduct;
    const productId = product.id;
    const meta = connectedProduct;
    const validTime = valid?.iso;
    const requestedWindKind = windLevelKind;
    const requestedWindLevel = windLevel;
    const requestedRun = selectedRunCatalog?.run;
    if (!selectedId || !meta || !validTime) {
      frameRequest?.abort();
      frameData = null;
      frameLoading = false;
      framePending = false;
      frameError = '';
      return;
    }
    if (!selectedFrameReady) {
      frameRequest?.abort();
      frameData = null;
      frameLoading = false;
      framePending = true;
      frameError = '';
      return;
    }
    const frameOptions = {
      model: selectedModel,
      product: productId,
      validTime,
      run: requestedRun,
      verticalKind: productId === 'wind-level' ? requestedWindKind : undefined,
      level: productId === 'wind-level' ? requestedWindLevel : undefined
    };
    const cachedFrame = getCachedForecastFrame(frameOptions);
    if (cachedFrame) {
      frameRequest?.abort();
      frameData = cachedFrame;
      frameLoading = false;
      framePending = false;
      frameError = '';
      return;
    }
    frameRequest?.abort();
    const controller = new AbortController();
    frameRequest = controller;
    frameLoading = true;
    framePending = false;
    frameError = '';
    fetchForecastFrame({ ...frameOptions, signal: controller.signal })
      .then((frame) => {
        if (controller.signal.aborted) return;
        frameData = frame;
        frameLoading = false;
      })
      .catch((error) => {
        if (error.name === 'AbortError') return;
        frameError = error.message;
        frameLoading = false;
      });
    return () => {
      controller.abort();
    };
  });

  // Precarga las horas contiguas una vez la actual está en pantalla, para que
  // el deslizador no vuelva a mostrar la tarjeta de carga. Se espera a que la
  // hora visible haya llegado: si no, competirían por el mismo ancho de banda.
  $effect(() => {
    const meta = connectedProduct;
    const currentIso = valid?.iso;
    if (!selectedProduct || !meta || frameData?.valid_time !== currentIso) return;
    const productId = product.id;
    const requestedRun = selectedRunCatalog?.run;
    const isWind = productId === 'wind-level';
    // Primero hacia adelante: es el sentido en el que avanza la reproducción.
    const neighbours = [hourIndex + 1, hourIndex + 2, hourIndex - 1]
      .filter((index) => index >= 0 && index < activeHours.length)
      .map((index) => activeHours[index])
      .filter(hourIsReady);
    prefetchForecastFrames(neighbours.map((hour) => ({
      model: selectedModel,
      product: productId,
      validTime: hour.iso,
      run: requestedRun,
      verticalKind: isWind ? windLevelKind : undefined,
      level: isWind ? windLevel : undefined
    })));
  });

  $effect(() => {
    const active = playing;
    const currentIndex = hourIndex;
    const validTime = valid?.iso;
    const currentFrameReady = frameData?.valid_time === validTime;
    const busy = frameLoading || framePending || Boolean(frameError);
    if (!active || !selectedProduct || busy || !currentFrameReady) return;
    const nextIndex = nextReadyHourIndex(currentIndex);
    if (nextIndex < 0) {
      playing = false;
      return;
    }
    const timer = window.setTimeout(() => {
      hourIndex = nextIndex;
    }, 900);
    return () => window.clearTimeout(timer);
  });
</script>

<svelte:window
  onclick={() => (unitMenuOpen = false)}
  onkeydown={(event) => { if (event.key === 'Escape') unitMenuOpen = false; }}
/>

<section class="forecast-head">
  <div>
    <div class="forecast-title">
      <h2>{tr('title')}</h2>
      <span class="beta-badge">Beta</span>
    </div>
    <p>{tr('subtitle')}</p>
  </div>
</section>

<section class="control-bar" aria-label={tr('modelConfig')}>
  <label><Calendar size={14} /><span>RUN</span><select value={selectedRun} onchange={(event) => selectRun(event.currentTarget.value)}>{#each runCatalogs as item}<option value={item.run}>{new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(item.run))} UTC · {runProgress(item).toLocaleString(locale)} %</option>{/each}</select></label>
  <label><Layers size={14} /><span>{tr('model')}</span><select value={selectedModel} onchange={(event) => selectModel(event.currentTarget.value)}>{#each forecastModels as item}<option value={item.id}>{item.label}</option>{/each}</select></label>
  <div
    class="run-summary"
    title={latestProgress?.frames_total
      ? tr('mapsAvailable', { available: latestProgress.frames_available, total: latestProgress.frames_total })
      : latestStatusLabel}
  >
    <span class="status-dot" class:error={catalogError}></span>
    <span class="run-summary-copy">
      <small>{tr('latestRun')}</small>
      <strong>{latestRunShortLabel} · {latestStatusLabel}</strong>
    </span>
    <button type="button" aria-label={tr('refreshRun')} onclick={refreshCatalog}><RefreshCw size={15} /></button>
  </div>
</section>

<div class="forecast-layout">
  <aside class="product-selector" aria-label={tr('mapSelector')}>
    <header>
      <div><span>{tr('maps')}</span><small>{modelSummary.total} {tr('selected')}</small></div>
      <p>{modelSummary.selectedNative} {tr('fields')} {model.short}{modelSummary.selectedDerived ? ` · ${modelSummary.selectedDerived} ${tr('diagnostics')}` : ''}</p>
      <label class="search-box"><Search size={14} /><input bind:value={search} type="search" placeholder={tr('search')} aria-label={tr('searchMap')} /></label>
    </header>

    <div class="category-list">
      {#each visibleCategories as category}
        <section class="category">
          <button
            type="button"
            class="category-toggle"
            aria-expanded={expandedCategory === category.id || search.length > 0}
            onclick={() => toggleCategory(category.id)}
          >
            <span>{category.label}<small>{category.products.length}</small></span>
            <ChevronDown size={14} class={expandedCategory === category.id || search.length > 0 ? 'open' : ''} />
          </button>
          {#if expandedCategory === category.id || search.length > 0}
            <div class="product-list">
              {#each category.products as item}
                {@const availability = productProgress(item)}
                <button
                  type="button"
                  class:active={selectedProduct === item.id}
                  aria-current={selectedProduct === item.id ? 'true' : undefined}
                  onclick={() => selectProduct(item)}
                >
                  <i style:--accent={item.accent}></i>
                  <span>{item.label}</span>
                  <span class="product-meta">
                    <span
                      class="product-status {availability.state}"
                      aria-label={`${availability.label}: ${tr('hoursAvailable', { available: availability.available, total: availability.total })}`}
                      title={tr('hoursAvailable', { available: availability.available, total: availability.total })}
                    >{availability.state === 'complete' ? '✓' : availability.state === 'partial' ? `${availability.percent}%` : '·'}</span>
                    {#if item.kind === 'derived'}
                      <img src={`${assetBase}mlx-logo.png`} alt={tr('calculatedBy')} title={tr('mlxDiagnostic')} />
                    {/if}
                  </span>
                </button>
              {/each}
            </div>
          {/if}
        </section>
      {/each}
    </div>

    <footer>
      <img src={`${assetBase}mlx-logo.png`} alt="" />
      <span>{tr('mlxSymbol')}</span>
    </footer>
  </aside>

  <main class="viewer-column">
    {#if !selectedProduct}
      <section class="map-card empty-map-card" aria-label={tr('noneSelected')}>
        <div class="empty-forecast">
          <img src={`${assetBase}mlx-logo.png`} alt="" />
          <strong>METEOLABX</strong>
          <span>{tr('title')}</span>
        </div>
      </section>
    {:else}
    <section class="map-card">
      <header class="map-head">
        <div class="map-product">
          <span class="product-mark" style:--product-accent={product.accent}></span>
          <div>
            <span class="product-title">
              <strong>{product.label}</strong>
              {#if product.kind === 'derived'}<img src={`${assetBase}mlx-logo.png`} alt={tr('calculatedBy')} />{/if}
            </span>
            <small>{productContents}{product.id === 'wind-level' ? ` · ${windLevel} ${windLevelUnit}` : ''} · {tr('valid')} {valid.day} · {valid.time} UTC · H+{String(valid.horizon).padStart(2, '0')}</small>
          </div>
        </div>
        <div class="map-actions">
          {#if exportError}<small class="export-error">{exportError}</small>{/if}
          <button
            type="button"
            title={tr('downloadPng')}
            aria-label={tr('downloadMapPng')}
            disabled={!frameData || exporting}
            onclick={downloadPng}
          ><Download size={16} /></button>
          <button type="button" title={tr('fullscreen')} onclick={toggleFullscreen}><Maximize2 size={16} /></button>
        </div>
      </header>

      <div class="forecast-map palette-{product.palette}" bind:this={mapContainer} style:--map-ink={mapInk || null}>
        {#if frameData}<ForecastGrid frame={frameData} productLabel={mapProductLabel} {language} formatProbe={formatProbe} scaleBreaks={product.scaleBreaks || null} zeroFloor={product.zeroFloor || 0} displayMin={product.min} displayMax={product.max} contourStep={product.contourStep || 0} formatContour={formatContour} nationalBoundariesOnly={Boolean(product.nationalBoundariesOnly)} overlayStep={product.overlayStep || 0} overlayMajorStep={product.overlayMajorStep || 0} troughAxes={Boolean(product.troughAxes)} overlayLabel={product.overlay || ''} pressureCentres={Boolean(product.pressureCentres)} overlaySmoothing={product.overlaySmoothing ?? 4} overlayLayerLabel={product.overlayLayerLabel || ''} onink={(tinta) => (mapInk = tinta)} resetKey={`${mapResetKey}:${selectedRun}:${product.id}:${windLevelKind}:${windLevel}`} />{/if}
        {#if product.id === 'wind-level' && windLevels.length}
          <aside class="level-rail" aria-label={tr('windLevel')}>
            <header><strong>{tr('level')}</strong><small>{windLevelKind === 'height' ? tr('aboveGround') : tr('isobaric')}</small></header>
            <div class="level-kind">
              <button type="button" class:active={windLevelKind === 'height'} onclick={() => selectWindKind('height')}>m AGL</button>
              <button type="button" class:active={windLevelKind === 'isobaric'} onclick={() => selectWindKind('isobaric')}>hPa</button>
            </div>
            <div class="level-list">
              {#each displayedWindLevels as level}
                <button type="button" class:active={windLevel === level} onclick={() => (windLevel = level)}>{level}</button>
              {/each}
            </div>
          </aside>
        {/if}

        {#if frameLoading}<div class="frame-state"><span class="spinner"></span><strong>{tr('loading', { product: product.short })}</strong><small>{tr('downloading')}</small></div>{/if}
        {#if framePending}<div class="frame-state"><span class="spinner"></span><strong>{tr('calculating', { product: product.short })}</strong><small>{tr('workerPending')}</small></div>{/if}
        {#if frameError}<div class="frame-state error"><strong>{tr('loadError')}</strong><small>{frameError}</small><button type="button" onclick={() => (hourIndex = Math.max(0, hourIndex - 1))}>{tr('previousHourTry')}</button></div>{/if}
        {#if frameData}
          <div class="map-watermark" aria-hidden="true">
            <img src={`${assetBase}mlx-logo.png`} alt="" />
            <span><strong>METEOLABX</strong><small>{tr('title')}</small></span>
          </div>
          <div class="legend" class:legend-classes={legendBands.length > 0}>
            {#if legendBands.length}
              <div class="band-scale">
                <div class="bands" style:--bands={legendBands.length}>
                  {#each legendBands as color}<span class="band" style:background-color={color}></span>{/each}
                </div>
                <div class="band-marks">
                  {#each legendMarks as mark}<span style:left={`${mark.at}%`}>{mark.label}</span>{/each}
                </div>
              </div>
            {:else}
              <span>{legendMin}</span><i></i><span>{legendMax}</span>
            {/if}
            {#if displayUnitOptions.length > 1}
              <div class="unit-picker">
                <button
                  type="button"
                  class="unit-button"
                  class:open={unitMenuOpen}
                  aria-haspopup="menu"
                  aria-expanded={unitMenuOpen}
                  title={tr('changeUnits')}
                  onclick={(event) => { event.stopPropagation(); unitMenuOpen = !unitMenuOpen; }}
                >
                  <span>{displayUnitLabel}</span>
                  <ChevronDown size={11} />
                </button>
                {#if unitMenuOpen}
                  <div class="unit-menu" role="menu" aria-label={tr('units')}>
                    {#each displayUnitOptions as option}
                      <button
                        type="button"
                        role="menuitemradio"
                        aria-checked={option.unit === displayUnit}
                        class:active={option.unit === displayUnit}
                        onclick={(event) => { event.stopPropagation(); pickUnit(option.unit); }}
                      >{option.label}</button>
                    {/each}
                  </div>
                {/if}
              </div>
            {:else}
              <span class="unit-static">{product.unit}</span>
            {/if}
          </div>
        {/if}
      </div>

      <div class="timeline">
        <button type="button" onclick={() => step(-1)} disabled={hourIndex === 0} aria-label={tr('previousHour')}><ChevronLeft size={17} /></button>
        <button class="play" class:active={playing} type="button" onclick={togglePlayback} aria-label={playing ? tr('pause') : tr('play')}>
          {#if playing}<Pause size={16} />{:else}<Play size={16} />{/if}
        </button>
        <div class="time-range">
          <div class="time-labels"><span>{activeHours[0].day} · {activeHours[0].time} UTC</span><strong>{valid.day} · {valid.time} UTC</strong><span>{activeHours.at(-1).day} · {activeHours.at(-1).time} UTC</span></div>
          <input type="range" min="0" max={activeHours.length - 1} bind:value={hourIndex} aria-label={tr('forecastHour')} />
          <div class="ticks">{#each activeHours as hour, index}<i class:major={index % 6 === 0} class:ready={hourIsReady(hour)} class:pending={!hourIsReady(hour)} title={tr(hourIsReady(hour) ? 'hourAvailable' : 'hourPending', { time: hour.time })}></i>{/each}</div>
        </div>
        <button type="button" onclick={() => step(1)} disabled={hourIndex === activeHours.length - 1} aria-label={tr('nextHour')}><ChevronRight size={17} /></button>
      </div>
    </section>

    <section class="product-explainer" id="method">
      <header>
        <div class="explainer-identity">
          <span class="explainer-icon"><Info size={18} /></span>
          <div>
            <small>{tr('viewedMap')} · {productContents}</small>
            <span class="product-title">
              <h3>{product.label}</h3>
              {#if product.kind === 'derived'}<img src={`${assetBase}mlx-logo.png`} alt={tr('calculatedBy')} />{/if}
            </span>
          </div>
        </div>
        <span class:derived={product.kind === 'derived'} class="source-tag">{product.kind === 'derived' ? tr('diagnostics') : tr('nativeField', { model: model.short })}</span>
      </header>
      <!-- Mientras el idioma no tenga su guía, se enseña la castellana con su
           aviso: incompleta, pero es la explicación de verdad. -->
      {#if guide.untranslated}
        <p class="guide-untranslated">{tr('guideUntranslated')}</p>
      {/if}
      <div class="explanation-overview">
        <section class="what"><h4>{tr('what')}</h4><p>{guide.what}</p></section>
        <section class="interpretation">
          <h4>{tr('interpretation')}</h4>
          <ul>{#each guide.interpretation as paragraph}<li>{paragraph}</li>{/each}</ul>
        </section>
      </div>
      <section class="calculation-detail">
        <h4>{tr('calculation')}</h4>
        <div class="calculation-copy">
          <p>{guide.method}</p>
          {#if guide.equations?.length}
            {#each guide.equations as equation}
              <MathFormula expression={equation.latex} label={equation.label} />
            {/each}
          {/if}
          {#if guide.steps?.length}
            <ol>{#each guide.steps as step}<li>{step}</li>{/each}</ol>
          {/if}
          <code>{product.coverage}</code>
        </div>
      </section>
      {#if guide.sources?.length}
        <footer class="technical-sources">
          <strong>{tr('sources')}</strong>
          <div>
            {#each guide.sources as source}
              <a href={source.url} target="_blank" rel="noreferrer">{source.label}</a>
            {/each}
          </div>
        </footer>
      {/if}
    </section>
    {/if}

  </main>
</div>

<style>
  .forecast-head{margin-bottom:16px}.forecast-title{display:flex;align-items:center;gap:8px}.forecast-title h2{font-size:1.15rem;font-weight:700;letter-spacing:-.02em}.forecast-head p{margin-top:4px;color:var(--muted);font-size:.8rem;text-wrap:balance}.beta-badge{display:inline-flex;align-items:center;padding:.12rem .35rem;border:1px solid rgba(255,75,75,.42);border-radius:999px;background:rgba(255,75,75,.1);color:#ff4b4b;font-size:.58rem;font-weight:700;line-height:1}.status-dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;background:#43c98a;box-shadow:0 0 0 4px rgba(67,201,138,.14)}.status-dot.error{background:#ef6f76;box-shadow:0 0 0 4px rgba(239,111,118,.14)}.run-summary button,.map-actions button,.timeline>button{display:grid;place-items:center;border:1px solid var(--border);border-radius:9px;color:var(--ink-2);background:var(--card);transition:border-color .15s ease,color .15s ease,background .15s ease}.run-summary button:hover,.map-actions button:hover,.timeline>button:hover:not(:disabled){border-color:var(--border-2);color:var(--ink);background:var(--panel-2)}
  .control-bar{display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:9px;border:1px solid var(--border);border-radius:13px;background:var(--panel)}.control-bar label{display:flex;align-items:center;gap:7px;height:40px;padding:0 11px;border:1px solid var(--border);border-radius:9px;color:var(--ink-2);background:var(--panel-2);font-size:.76rem;transition:border-color .15s ease,background .15s ease}.control-bar label:hover,.control-bar label:focus-within{border-color:var(--border-2);background:var(--card)}.control-bar select{height:100%;max-width:220px;border:0;outline:0;color:var(--ink);background:transparent;font:inherit;font-weight:650;cursor:pointer}.run-summary{display:flex;align-items:center;gap:10px;height:40px;margin-left:auto;padding:0 4px 0 12px;border:1px solid var(--border);border-radius:9px;background:var(--panel-2)}.run-summary-copy{display:flex;min-width:0;flex-direction:column;gap:1px}.run-summary small{color:var(--ink-2);font-size:.62rem;line-height:1}.run-summary strong{max-width:330px;overflow:hidden;color:var(--ink);font-size:.71rem;line-height:1.2;text-overflow:ellipsis;white-space:nowrap}.run-summary button{width:32px;height:32px}
  .forecast-layout{display:grid;grid-template-columns:260px minmax(0,1fr);align-items:start;gap:14px}.product-selector,.map-card,.product-explainer{border:1px solid var(--border);border-radius:15px;background:var(--panel);overflow:hidden}.product-selector{position:sticky;top:78px;max-height:calc(100vh - 96px);display:flex;flex-direction:column}.product-selector>header{padding:15px;border-bottom:1px solid var(--border)}.product-selector>header>div{display:flex;align-items:baseline;justify-content:space-between}.product-selector>header span{font-size:.82rem;font-weight:720}.product-selector>header small,.product-selector>header p{color:var(--muted);font-size:.57rem}.product-selector>header p{margin:5px 0 11px}.search-box{display:flex;align-items:center;gap:7px;padding:8px 9px;border:1px solid var(--border);border-radius:9px;color:var(--muted);background:var(--panel-2)}.search-box input{min-width:0;width:100%;border:0;outline:0;color:var(--ink);background:transparent;font:inherit;font-size:.66rem}.category-list{overflow-y:auto;padding:7px}.category{border-bottom:1px solid var(--border)}.category:last-child{border:0}.category-toggle{display:flex;align-items:center;justify-content:space-between;width:100%;padding:10px 8px;border:0;color:var(--ink-2);background:transparent;font-size:.68rem;font-weight:680;text-align:left}.category-toggle span{display:flex;align-items:center;gap:6px}.category-toggle small{display:grid;place-items:center;min-width:18px;height:18px;border-radius:6px;color:var(--muted);background:var(--panel-2);font-size:.52rem}.category-toggle :global(svg){transition:transform .18s}.category-toggle :global(svg.open){transform:rotate(180deg)}.product-list{display:flex;flex-direction:column;gap:2px;padding:0 2px 8px}.product-list button{display:grid;grid-template-columns:3px minmax(0,1fr) auto;align-items:center;gap:8px;min-height:35px;padding:6px 7px;border:1px solid transparent;border-radius:8px;color:var(--muted);background:transparent;font-size:.63rem;text-align:left}.product-list button:hover{color:var(--ink);background:var(--panel-2)}.product-list button.active{border-color:color-mix(in srgb,var(--accent) 28%,var(--border));color:var(--ink);background:var(--card)}.product-list>button>i{width:3px;height:20px;border-radius:4px;background:var(--accent)}.product-meta{display:flex;align-items:center;justify-content:flex-end;gap:5px}.product-list img{width:20px;height:20px;border-radius:6px}.product-status{display:grid;place-items:center;min-width:22px;height:18px;padding:0 4px;border-radius:6px;font-size:.48rem;font-weight:780;font-variant-numeric:tabular-nums}.product-status.complete{color:#143c2b;background:rgba(67,201,138,.78)}.product-status.partial{color:#5a3a09;background:rgba(240,178,78,.82)}.product-status.pending{color:var(--muted);background:var(--panel-2);font-size:.82rem}.product-selector>footer{display:flex;align-items:center;gap:8px;padding:10px 12px;border-top:1px solid var(--border);color:var(--muted);background:var(--panel-2);font-size:.55rem;line-height:1.35}.product-selector>footer img{width:22px;height:22px;border-radius:6px}
  .viewer-column{min-width:0}.empty-map-card{display:grid;place-items:center;min-height:clamp(620px,64vh,780px);background:var(--panel)}.empty-forecast{display:flex;align-items:center;flex-direction:column;color:var(--ink);text-align:center}.empty-forecast img{width:62px;height:62px;margin-bottom:18px;border-radius:16px;opacity:.88}.empty-forecast strong{font-size:1.72rem;letter-spacing:.14em}.empty-forecast span{margin-top:7px;color:var(--muted);font-size:.76rem;letter-spacing:.18em;text-transform:uppercase}.map-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 15px;border-bottom:1px solid var(--border)}.map-product{display:flex;align-items:center;gap:10px}.product-mark{width:4px;height:35px;border-radius:5px;background:var(--product-accent);box-shadow:0 0 16px color-mix(in srgb,var(--product-accent) 45%,transparent)}.product-title{display:flex;align-items:center;gap:7px}.product-title strong{font-size:.82rem}.product-title img{width:21px;height:21px;border-radius:6px}.map-head small{display:block;margin-top:3px;color:var(--muted);font-size:.61rem;font-variant-numeric:tabular-nums}.map-actions{display:flex;align-items:center;gap:5px}.map-actions button{width:31px;height:31px;border-radius:8px}.map-actions button:disabled{opacity:.45}.export-error{max-width:210px;color:#e8846b;font-size:.52rem;line-height:1.25}
  /* El lienzo del mapa lleva un fondo claro fijo, no el del tema: las
     fronteras se trazan casi en negro (`.region-boundary`) y sobre un fondo
     oscuro no se ven. Los paneles que se posan encima —leyenda, zoom, tooltip,
     estado del fotograma— son cristales oscuros con texto claro, hechos ya
     para este fondo. */
  .forecast-map{position:relative;min-height:clamp(620px,64vh,780px);overflow:hidden;background:#d5e1e6}.real-frame{position:absolute;inset:5% 7%;z-index:3;width:86%;height:90%;object-fit:contain;filter:drop-shadow(0 12px 24px rgba(0,0,0,.25))}.frame-state{position:absolute;left:50%;top:50%;z-index:9;display:flex;align-items:center;flex-direction:column;gap:6px;width:min(280px,70%);padding:16px;transform:translate(-50%,-50%);border:1px solid rgba(255,255,255,.12);border-radius:12px;color:#eaf3f8;background:rgba(6,16,25,.82);backdrop-filter:blur(10px);text-align:center}.frame-state strong{font-size:.72rem}.frame-state small{color:rgba(235,244,251,.65);font-size:.58rem;line-height:1.4}.frame-state.error{border-color:rgba(239,111,118,.32)}.frame-state button{margin-top:4px;padding:6px 9px;border:1px solid rgba(255,255,255,.14);border-radius:7px;color:#dceaf2;background:rgba(255,255,255,.06);font-size:.57rem}.spinner{width:20px;height:20px;border:2px solid rgba(255,255,255,.18);border-top-color:#70b9ef;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.legend{position:absolute;right:12px;bottom:12px;z-index:18;display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid rgba(255,255,255,.13);border-radius:9px;color:rgba(235,244,251,.8);background:rgba(6,16,25,.62);font-size:.6rem}.legend i{width:130px;height:8px;border-radius:99px;background:linear-gradient(90deg,#3b4cc0,#3288bd,#66c2a5,#e6f598,#fdae61,#d73027,#762a83)}
  .palette-precipitation .legend i{background:linear-gradient(90deg,#28465f,#2f6f8e,#369aa1,#58bd91,#9bd275,#d7dc69,#f2c55a,#ed914c,#df6262,#b44f88)}
  .legend-classes{align-items:flex-end;padding-bottom:6px}
  .band-scale{position:relative;padding-bottom:11px}
  .bands{display:grid;grid-template-columns:repeat(var(--bands),1fr);width:262px;height:8px;border-radius:99px;overflow:hidden}
  .bands .band{display:block;height:100%}
  .band-marks{position:absolute;left:0;right:0;bottom:0;height:10px}
  .band-marks span{position:absolute;color:rgba(235,244,251,.72);font-size:.44rem;line-height:1;transform:translateX(-50%);white-space:nowrap}
  .band-marks span:first-child{transform:none}
  .unit-picker{position:relative}
  .legend .unit-static{margin-left:-4px}
  .unit-button{display:flex;align-items:center;gap:3px;padding:3px 5px 3px 7px;border:1px solid rgba(140,205,246,.42);border-radius:6px;color:#9fd8ff;background:rgba(76,163,219,.16);font-size:.6rem;font-weight:720;line-height:1;cursor:pointer}
  .unit-button :global(svg){opacity:.75;transition:transform .14s ease}
  .unit-button:hover{border-color:rgba(140,205,246,.75);color:#cfeaff;background:rgba(76,163,219,.3)}
  .unit-button:focus-visible{outline:2px solid rgba(140,205,246,.85);outline-offset:1px}
  .unit-button.open{border-color:rgba(140,205,246,.85);color:#e8f5ff;background:rgba(76,163,219,.38)}
  .unit-button.open :global(svg){transform:rotate(180deg)}
  .unit-menu{position:absolute;right:0;bottom:calc(100% + 6px);z-index:16;display:flex;min-width:74px;flex-direction:column;gap:1px;padding:4px;border:1px solid rgba(255,255,255,.16);border-radius:8px;background:rgba(5,14,22,.94);box-shadow:0 10px 26px rgba(0,0,0,.38);backdrop-filter:blur(8px)}
  .unit-menu button{width:100%;padding:5px 8px;border:0;border-radius:5px;color:rgba(235,244,251,.74);background:transparent;font-size:.6rem;text-align:right;cursor:pointer}
  .unit-menu button:hover{color:#fff;background:rgba(255,255,255,.08)}
  .unit-menu button.active{color:#06131c;background:#68bdf1;font-weight:750}
  .map-watermark{position:absolute;left:14px;bottom:13px;z-index:7;display:flex;align-items:center;gap:8px;color:var(--map-ink,#e6f1f8);pointer-events:none;user-select:none}.map-watermark img{width:27px;height:27px;border-radius:7px}.map-watermark span{display:flex;flex-direction:column;line-height:1}.map-watermark strong{font-size:.56rem;letter-spacing:.12em}.map-watermark small{margin-top:4px;font-size:.46rem;letter-spacing:.16em;text-transform:uppercase}:global(.theme-light) .map-watermark{color:var(--map-ink,#1b3a4e)}
  .forecast-map:fullscreen{min-height:100vh}
  .level-rail{position:absolute;right:12px;top:58px;bottom:54px;z-index:14;display:flex;width:92px;flex-direction:column;border:1px solid rgba(255,255,255,.14);border-radius:10px;color:#e8f2f7;background:rgba(5,14,22,.78);backdrop-filter:blur(10px);overflow:hidden}.level-rail header{padding:9px 9px 7px;border-bottom:1px solid rgba(255,255,255,.1)}.level-rail header strong,.level-rail header small{display:block}.level-rail header strong{font-size:.62rem}.level-rail header small{margin-top:2px;color:rgba(235,244,251,.55);font-size:.47rem}.level-kind{display:grid;grid-template-columns:1fr 1fr;gap:3px;padding:5px}.level-kind button,.level-list button{border:0;color:rgba(235,244,251,.62);background:transparent;font-size:.5rem}.level-kind button{padding:5px 2px;border-radius:5px}.level-kind button.active{color:#06131c;background:#68bdf1;font-weight:750}.level-list{display:flex;min-height:0;flex:1;flex-direction:column;overflow-y:auto;padding:2px 5px 6px}.level-list button{flex:0 0 25px;border-left:2px solid transparent;text-align:right}.level-list button:hover{color:#fff;background:rgba(255,255,255,.06)}.level-list button.active{border-left-color:#68bdf1;border-radius:4px;color:#8ed3ff;background:rgba(76,163,219,.12);font-weight:750}
  .timeline{display:grid;grid-template-columns:34px 34px 1fr 34px;align-items:center;gap:7px;padding:12px 14px 14px;border-top:1px solid var(--border)}.timeline>button{width:34px;height:34px;border-radius:9px}.timeline>button:disabled{opacity:.35;cursor:default}.timeline .play{color:#76bfff}.timeline .play.active{color:#08141f;background:#76bfff}.time-range{min-width:0;padding:0 5px}.time-labels{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:.57rem}.time-labels strong{color:var(--ink);font-size:.63rem}.time-range input{width:100%;margin:9px 0 2px;accent-color:#5faeea}.ticks{display:flex;justify-content:space-between;padding:0 3px}.ticks i{width:2px;height:4px;border-radius:2px;background:var(--border-2)}.ticks i.major{height:7px}.ticks i.ready{background:#43c98a}.ticks i.pending{background:var(--border-2);opacity:.62}
  .product-explainer{margin-top:14px;padding:17px}.product-explainer>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding-bottom:14px;border-bottom:1px solid var(--border)}.explainer-identity{display:flex;align-items:center;gap:11px}.explainer-icon{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;color:#6ab7ef;background:rgba(62,142,208,.11)}.explainer-identity small{display:block;margin-bottom:3px;color:var(--muted);font-size:.56rem}.explainer-identity h3{font-size:.88rem}.source-tag{padding:5px 8px;border-radius:6px;color:#78baf0;background:rgba(62,142,208,.11);font-size:.55rem;font-weight:740;text-transform:uppercase}.source-tag.derived{color:#f08b9d;background:rgba(240,112,134,.1)}.product-explainer h4{margin-bottom:7px;color:var(--ink-2);font-size:.61rem;text-transform:uppercase;letter-spacing:.065em}.explanation-overview{display:grid;grid-template-columns:1fr;gap:19px;padding-top:17px}.explanation-overview p,.interpretation li,.calculation-detail p,.calculation-detail li{color:var(--muted);font-size:.65rem;line-height:1.62}.interpretation ul{display:grid;gap:8px;margin:0;padding-left:17px}.interpretation li::marker,.calculation-copy li::marker{color:#67b7ef}.calculation-detail{margin-top:19px;padding-top:17px;border-top:1px solid var(--border)}.calculation-copy{min-width:0}.calculation-copy ol{display:grid;gap:5px;margin:11px 0 0;padding-left:18px}.calculation-copy code{display:block;margin-top:12px;padding:7px 9px;border-radius:7px;color:#70b9ef;background:var(--panel-2);font-size:.52rem;overflow-wrap:anywhere}.technical-sources{display:grid;grid-template-columns:1fr;gap:12px;margin-top:17px;padding-top:14px;border-top:1px solid var(--border)}.technical-sources>strong{color:var(--ink-2);font-size:.58rem;text-transform:uppercase;letter-spacing:.055em}.technical-sources>div{display:flex;flex-wrap:wrap;gap:6px}.technical-sources a{padding:5px 7px;border:1px solid var(--border);border-radius:6px;color:#6db5e9;background:var(--panel-2);font-size:.53rem;line-height:1.35;text-decoration:none}.technical-sources a:hover{border-color:rgba(109,181,233,.42);color:#8bcbf8}
  /* Igualamos la escala tipográfica con el resto de MeteoLabX. El mapa
     conserva todo su espacio: solo crecen los rótulos y controles que antes
     quedaban por debajo del tamaño habitual de la aplicación. */
  .product-selector>header span{font-size:.92rem}
  .product-selector>header small,.product-selector>header p{color:var(--ink-2);font-size:.67rem}
  .search-box{color:var(--ink-2)}
  .search-box input{font-size:.76rem}
  .category-toggle{font-size:.77rem}
  .category-toggle small{color:var(--ink-2);font-size:.61rem}
  .product-list button{min-height:40px;color:var(--ink-2);font-size:.72rem}
  .product-status{font-size:.56rem}
  .product-selector>footer{color:var(--ink-2);font-size:.64rem}
  .product-title strong{font-size:.9rem}
  .map-head small{color:var(--ink-2);font-size:.7rem}
  .frame-state strong{font-size:.82rem}
  .frame-state small{font-size:.68rem}
  .frame-state button{font-size:.67rem}
  .legend{font-size:.68rem}
  .band-marks span{font-size:.5rem}
  .unit-button,.unit-menu button{font-size:.68rem}
  .level-rail header strong{font-size:.7rem}
  .level-rail header small{font-size:.56rem}
  .level-kind button,.level-list button{font-size:.59rem}
  .time-labels{color:var(--ink-2);font-size:.66rem}
  .time-labels strong{font-size:.72rem}
  .explainer-identity small{color:var(--ink-2);font-size:.66rem}
  .explainer-identity h3{font-size:.96rem}
  .source-tag{font-size:.64rem}
  .product-explainer h4{font-size:.7rem}
  .guide-untranslated{margin:0 0 12px;padding:8px 12px;border-radius:8px;background:var(--panel-2);color:var(--muted);font-size:.78rem;font-style:italic}
  .explanation-overview p,.interpretation li,.calculation-detail p,.calculation-detail li{color:var(--ink-2);font-size:.75rem}
  .calculation-copy code{font-size:.62rem}
  .technical-sources>strong{font-size:.67rem}
  .technical-sources a{font-size:.63rem}

  @media(max-width:980px){.forecast-layout{grid-template-columns:220px minmax(0,1fr)}.forecast-map{min-height:440px}}
  @media(max-width:760px){.control-bar{flex-wrap:wrap}.run-summary{order:3;width:100%;margin-left:0}.forecast-layout{grid-template-columns:1fr}.product-selector{position:static;max-height:none}.category-list{max-height:360px}.forecast-map{min-height:390px}.map-head{align-items:flex-start}.map-actions button:nth-child(2){display:none}}
  @media(max-width:640px){.bands{width:158px}.band-marks span:nth-child(even){display:none}}
  @media(max-width:480px){.control-bar label{min-width:0;flex:1}.control-bar label>span{display:none}.control-bar select{min-width:0;width:100%}.run-summary strong{max-width:220px}.forecast-map{min-height:330px}.legend i{width:76px}.timeline{grid-template-columns:32px 32px minmax(150px,1fr) 32px;padding-inline:8px}.timeline>button{width:32px;height:32px}.time-labels>span{display:none}.time-labels{justify-content:center}.product-explainer>header{align-items:flex-start;flex-direction:column}.source-tag{margin-left:47px}}
</style>

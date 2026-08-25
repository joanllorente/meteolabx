<script>
  import {
    Activity, Calendar, ChevronDown, ChevronLeft, ChevronRight,
    Info, Layers, Maximize2, Pause, Play, RefreshCw, Search
  } from '@lucide/svelte';
  import { onMount } from 'svelte';
  import ForecastGrid from '../components/ForecastGrid.svelte';
  import MathFormula from '../components/MathFormula.svelte';
  import { forecastCategories, forecastProducts, forecastCatalogSummary } from '../data/forecastProducts.js';
  import { fetchAromeCatalog, fetchAromeFrame } from '../services/forecastApi.js';

  const assetBase = import.meta.env.BASE_URL;
  const hours = Array.from({ length: 18 }, (_, i) => ({
    horizon: i * 3,
    day: i < 7 ? 'Lun 24' : i < 15 ? 'Mar 25' : 'Mié 26',
    time: `${String((5 + i * 3) % 24).padStart(2, '0')}:00`
  }));

  let selectedProduct = $state(null);
  let expandedCategory = $state('dynamics');
  let search = $state('');
  let hourIndex = $state(5);
  let playing = $state(false);
  let catalog = $state(null);
  let selectedRun = $state('');
  let catalogError = $state('');
  let frameData = $state(null);
  let frameLoading = $state(false);
  let framePending = $state(false);
  let frameError = $state('');
  let windLevelKind = $state('height');
  let windLevel = $state(10);
  let mapResetKey = $state(0);
  let mapContainer = $state();
  let frameRequest = null;
  let catalogRequest = null;

  function toggleFullscreen() {
    if (!mapContainer) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else mapContainer.requestFullscreen?.();
  }

  const product = $derived(forecastProducts.find((item) => item.id === selectedProduct) || forecastProducts[0]);
  const guide = $derived(product.guide || {
    what: product.description,
    interpretation: [product.description],
    method: product.method,
    equations: [],
    steps: [],
    sources: []
  });
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
      day: new Intl.DateTimeFormat('es-ES', { weekday: 'short', day: '2-digit', timeZone: 'UTC' }).format(date),
      time: new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(date)
    };
  }) : hours);
  const valid = $derived(activeHours[Math.min(hourIndex, activeHours.length - 1)] || hours[0]);
  const selectedFrameReady = $derived(
    !precomputedOnly
      || (connectedProduct?.available_times || []).includes(valid?.iso)
  );
  const selectedCategory = $derived(forecastCategories.find((item) => item.id === product.category));
  const windLevels = $derived(connectedProduct?.levels?.[windLevelKind] || []);
  const displayedWindLevels = $derived(windLevelKind === 'height' ? [...windLevels].reverse() : windLevels);
  const windLevelUnit = $derived(windLevelKind === 'height' ? 'm AGL' : 'hPa');
  const mapProductLabel = $derived(product.id === 'wind-level' ? `${product.label} · ${windLevel} ${windLevelUnit}` : product.label);
  const runLabel = $derived(connectedProduct
    ? `${new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(connectedProduct.run))} UTC`
    : '24/08 · 03:00 UTC');
  const latestRunLabel = $derived(runCatalogs[0]?.run
    ? `${new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(runCatalogs[0].run))} UTC`
    : runLabel);
  const latestProgress = $derived(runCatalogs[0]?.publication?.progress || null);
  const currentJobLabel = $derived.by(() => {
    const current = latestProgress?.current_job;
    if (!current) return '';
    if (current.product === 'convective-group') return 'Diagnósticos convectivos';
    return forecastProducts.find((item) => item.id === current.product)?.label || current.product;
  });
  const visibleCategories = $derived.by(() => {
    const term = search.trim().toLocaleLowerCase('es');
    return forecastCategories
      .map((category) => ({
        ...category,
        products: forecastProducts.filter((item) => item.category === category.id && (!term || `${item.label} ${item.short}`.toLocaleLowerCase('es').includes(term)))
      }))
      .filter((category) => category.products.length);
  });

  function selectProduct(item) {
    selectedProduct = item.id;
    expandedCategory = item.category;
    const count = selectedRunCatalog?.products?.[item.id]?.valid_times?.length || hours.length;
    hourIndex = Math.min(hourIndex, count - 1);
  }

  function selectRun(run) {
    selectedRun = run;
    const next = runCatalogs.find((item) => item.run === run)?.products?.[product.id];
    hourIndex = Math.min(hourIndex, Math.max(0, (next?.valid_times?.length || hours.length) - 1));
    frameData = null;
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

  function refreshCatalog() {
    catalogRequest?.abort();
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 20_000);
    catalogRequest = controller;
    fetchAromeCatalog({ signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        catalog = payload;
        const availableRuns = payload.runs || [];
        if (!availableRuns.some((item) => item.run === selectedRun)) {
          selectedRun = availableRuns[0]?.run || Object.values(payload.products || {})[0]?.run || '';
        }
        catalogError = '';
      })
      .catch((error) => {
        if (timedOut) catalogError = 'El catálogo AROME ha tardado demasiado';
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
    frameRequest?.abort();
    const controller = new AbortController();
    frameRequest = controller;
    frameLoading = true;
    framePending = false;
    frameError = '';
    const timer = window.setTimeout(() => {
      fetchAromeFrame({
        product: productId,
        validTime,
        run: requestedRun,
        verticalKind: productId === 'wind-level' ? requestedWindKind : undefined,
        level: productId === 'wind-level' ? requestedWindLevel : undefined,
        signal: controller.signal
      })
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
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  });
</script>

<section class="forecast-head">
  <div>
    <div class="eyebrow"><Activity size={14} /> Predicción numérica</div>
    <h2>Mapas AROME</h2>
  </div>
  <div class="run-status">
    <span class="status-dot" class:error={catalogError}></span>
    <div>
      <small>{catalogError ? 'Backend AROME no disponible' : 'Último RUN disponible'}</small>
      <strong>{catalogError ? 'Pulsa para reintentar' : catalog ? latestRunLabel : 'Conectando…'}</strong>
      {#if !catalogError && latestProgress?.frames_total}
        <span class="progress-summary">
          <span><i style:--progress={`${latestProgress.percent || 0}%`}></i></span>
          <em>{latestProgress.frames_available} / {latestProgress.frames_total} · {Number(latestProgress.percent || 0).toLocaleString('es-ES')} %</em>
        </span>
        {#if currentJobLabel}<small>Ahora: {currentJobLabel} · {latestProgress.current_job.valid_time.slice(11, 16)} UTC</small>{/if}
      {/if}
    </div>
    <button type="button" aria-label="Actualizar RUN" onclick={refreshCatalog}><RefreshCw size={15} /></button>
  </div>
</section>

<section class="control-bar" aria-label="Configuración del modelo">
  <label><Calendar size={14} /><span>RUN</span><select value={selectedRun} onchange={(event) => selectRun(event.currentTarget.value)}>{#each runCatalogs as item}<option value={item.run}>{new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(new Date(item.run))} UTC</option>{/each}</select></label>
  <label><Layers size={14} /><span>Modelo</span><select><option>AROME 0,025°</option></select></label>
</section>

<div class="forecast-layout">
  <aside class="product-selector" aria-label="Selector de mapas">
    <header>
      <div><span>Mapas</span><small>{forecastProducts.length} seleccionados</small></div>
      <p>{forecastCatalogSummary.selectedNative} campos AROME · {forecastCatalogSummary.selectedDerived} diagnósticos MLX</p>
      <label class="search-box"><Search size={14} /><input bind:value={search} type="search" placeholder="Buscar variable…" aria-label="Buscar mapa" /></label>
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
                <button
                  type="button"
                  class:active={selectedProduct === item.id}
                  aria-current={selectedProduct === item.id ? 'true' : undefined}
                  onclick={() => selectProduct(item)}
                >
                  <i style:--accent={item.accent}></i>
                  <span>{item.label}</span>
                  {#if item.kind === 'derived'}
                    <img src={`${assetBase}mlx-logo.png`} alt="Calculado por MeteoLabX" title="Diagnóstico calculado por MeteoLabX" />
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        </section>
      {/each}
    </div>

    <footer>
      <img src={`${assetBase}mlx-logo.png`} alt="" />
      <span>El símbolo MLX identifica los mapas calculados por MeteoLabX.</span>
    </footer>
  </aside>

  <main class="viewer-column">
    {#if !selectedProduct}
      <section class="map-card empty-map-card" aria-label="Ningún mapa seleccionado">
        <div class="empty-forecast">
          <img src={`${assetBase}mlx-logo.png`} alt="" />
          <strong>METEOLABX</strong>
          <span>Predicción numérica</span>
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
              {#if product.kind === 'derived'}<img src={`${assetBase}mlx-logo.png`} alt="Calculado por MeteoLabX" />{/if}
            </span>
            <small>{selectedCategory?.label}{product.id === 'wind-level' ? ` · ${windLevel} ${windLevelUnit}` : ''} · Válido {valid.day} · {valid.time} UTC · H+{String(valid.horizon).padStart(2, '0')}</small>
          </div>
        </div>
        <div class="map-actions">
          <button type="button" title="Pantalla completa" onclick={toggleFullscreen}><Maximize2 size={16} /></button>
        </div>
      </header>

      <div class="forecast-map palette-{product.palette}" bind:this={mapContainer}>
        {#if frameData}<ForecastGrid frame={frameData} productLabel={mapProductLabel} resetKey={`${mapResetKey}:${selectedRun}:${product.id}:${windLevelKind}:${windLevel}`} />{/if}
        {#if product.id === 'wind-level' && windLevels.length}
          <aside class="level-rail" aria-label="Nivel vertical del viento">
            <header><strong>Nivel</strong><small>{windLevelKind === 'height' ? 'Sobre terreno' : 'Isobárico'}</small></header>
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

        {#if frameLoading}<div class="frame-state"><span class="spinner"></span><strong>Calculando {product.short}</strong><small>La primera carga puede tardar unos segundos.</small></div>{/if}
        {#if framePending}<div class="frame-state"><span class="spinner"></span><strong>Calculando {product.short}</strong><small>AROME ya ha publicado esta hora. MeteoLabX la añadirá automáticamente al terminar el worker.</small></div>{/if}
        {#if frameError}<div class="frame-state error"><strong>No se pudo cargar el mapa real</strong><small>{frameError}</small><button type="button" onclick={() => (hourIndex = Math.max(0, hourIndex - 1))}>Probar la hora anterior</button></div>{/if}
        {#if frameData}
          <div class="map-watermark" aria-hidden="true">
            <img src={`${assetBase}mlx-logo.png`} alt="" />
            <span><strong>METEOLABX</strong><small>Predicción numérica</small></span>
          </div>
          <div class="legend"><span>{product.min}</span><i></i><span>{product.max} {product.unit}</span></div>
        {/if}
      </div>

      <div class="timeline">
        <button type="button" onclick={() => step(-1)} disabled={hourIndex === 0} aria-label="Hora anterior"><ChevronLeft size={17} /></button>
        <button class="play" class:active={playing} type="button" onclick={() => (playing = !playing)} aria-label={playing ? 'Pausar' : 'Reproducir'}>
          {#if playing}<Pause size={16} />{:else}<Play size={16} />{/if}
        </button>
        <div class="time-range">
          <div class="time-labels"><span>{activeHours[0].day} · {activeHours[0].time} UTC</span><strong>{valid.day} · {valid.time} UTC</strong><span>{activeHours.at(-1).day} · {activeHours.at(-1).time} UTC</span></div>
          <input type="range" min="0" max={activeHours.length - 1} bind:value={hourIndex} aria-label="Hora prevista" />
          <div class="ticks">{#each activeHours as _, index}<i class:major={index % 6 === 0}></i>{/each}</div>
        </div>
        <button type="button" onclick={() => step(1)} disabled={hourIndex === activeHours.length - 1} aria-label="Hora siguiente"><ChevronRight size={17} /></button>
      </div>
    </section>

    <section class="product-explainer" id="method">
      <header>
        <div class="explainer-identity">
          <span class="explainer-icon"><Info size={18} /></span>
          <div>
            <small>Mapa visualizado · {selectedCategory?.label}</small>
            <span class="product-title">
              <h3>{product.label}</h3>
              {#if product.kind === 'derived'}<img src={`${assetBase}mlx-logo.png`} alt="Calculado por MeteoLabX" />{/if}
            </span>
          </div>
        </div>
        <span class:derived={product.kind === 'derived'} class="source-tag">{product.kind === 'derived' ? 'Diagnóstico MLX' : 'Campo AROME'}</span>
      </header>
      <div class="explanation-overview">
        <section class="what"><h4>Qué representa</h4><p>{guide.what}</p></section>
        <section class="interpretation">
          <h4>Interpretación</h4>
          <ul>{#each guide.interpretation as paragraph}<li>{paragraph}</li>{/each}</ul>
        </section>
      </div>
      <section class="calculation-detail">
        <h4>Cálculo</h4>
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
          <strong>Base documental</strong>
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
  .forecast-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:18px}.eyebrow{display:flex;align-items:center;gap:7px;margin-bottom:7px;color:#74bfff;font-size:.68rem;font-weight:720;letter-spacing:.08em;text-transform:uppercase}.forecast-head h2{font-size:1.65rem;letter-spacing:-.035em}.run-status{display:flex;align-items:center;gap:10px;min-width:300px;padding:11px 12px;border:1px solid var(--border);border-radius:12px;background:var(--panel)}.status-dot{width:8px;height:8px;border-radius:50%;background:#43c98a;box-shadow:0 0 0 4px rgba(67,201,138,.14)}.status-dot.error{background:#ef6f76;box-shadow:0 0 0 4px rgba(239,111,118,.14)}.run-status div{display:flex;flex:1;flex-direction:column;gap:2px}.run-status small{color:var(--muted);font-size:.61rem}.run-status strong{font-size:.75rem}.progress-summary{display:grid;grid-template-columns:1fr auto;align-items:center;gap:7px;margin-top:4px}.progress-summary>span{height:4px;border-radius:99px;background:var(--panel-2);overflow:hidden}.progress-summary i{display:block;width:var(--progress);height:100%;border-radius:inherit;background:#43c98a}.progress-summary em{color:var(--muted);font-size:.54rem;font-style:normal;font-variant-numeric:tabular-nums}.run-status button,.map-actions button,.timeline>button{display:grid;place-items:center;border:1px solid var(--border);color:var(--ink-2);background:var(--card)}.run-status button{width:31px;height:31px;border-radius:8px}
  .control-bar{display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:9px;border:1px solid var(--border);border-radius:13px;background:var(--panel)}.control-bar label{display:flex;align-items:center;gap:7px;padding:7px 9px;border:1px solid var(--border);border-radius:9px;color:var(--muted);background:var(--panel-2);font-size:.66rem}.control-bar select{max-width:160px;border:0;outline:0;color:var(--ink);background:transparent;font:inherit;font-weight:650}
  .forecast-layout{display:grid;grid-template-columns:260px minmax(0,1fr);align-items:start;gap:14px}.product-selector,.map-card,.product-explainer{border:1px solid var(--border);border-radius:15px;background:var(--panel);overflow:hidden}.product-selector{position:sticky;top:78px;max-height:calc(100vh - 96px);display:flex;flex-direction:column}.product-selector>header{padding:15px;border-bottom:1px solid var(--border)}.product-selector>header>div{display:flex;align-items:baseline;justify-content:space-between}.product-selector>header span{font-size:.82rem;font-weight:720}.product-selector>header small,.product-selector>header p{color:var(--muted);font-size:.57rem}.product-selector>header p{margin:5px 0 11px}.search-box{display:flex;align-items:center;gap:7px;padding:8px 9px;border:1px solid var(--border);border-radius:9px;color:var(--muted);background:var(--panel-2)}.search-box input{min-width:0;width:100%;border:0;outline:0;color:var(--ink);background:transparent;font:inherit;font-size:.66rem}.category-list{overflow-y:auto;padding:7px}.category{border-bottom:1px solid var(--border)}.category:last-child{border:0}.category-toggle{display:flex;align-items:center;justify-content:space-between;width:100%;padding:10px 8px;border:0;color:var(--ink-2);background:transparent;font-size:.68rem;font-weight:680;text-align:left}.category-toggle span{display:flex;align-items:center;gap:6px}.category-toggle small{display:grid;place-items:center;min-width:18px;height:18px;border-radius:6px;color:var(--muted);background:var(--panel-2);font-size:.52rem}.category-toggle :global(svg){transition:transform .18s}.category-toggle :global(svg.open){transform:rotate(180deg)}.product-list{display:flex;flex-direction:column;gap:2px;padding:0 2px 8px}.product-list button{display:grid;grid-template-columns:3px 1fr 22px;align-items:center;gap:8px;min-height:35px;padding:6px 7px;border:1px solid transparent;border-radius:8px;color:var(--muted);background:transparent;font-size:.63rem;text-align:left}.product-list button:hover{color:var(--ink);background:var(--panel-2)}.product-list button.active{border-color:color-mix(in srgb,var(--accent) 28%,var(--border));color:var(--ink);background:var(--card)}.product-list i{width:3px;height:20px;border-radius:4px;background:var(--accent)}.product-list img{width:20px;height:20px;border-radius:6px}.product-selector>footer{display:flex;align-items:center;gap:8px;padding:10px 12px;border-top:1px solid var(--border);color:var(--muted);background:var(--panel-2);font-size:.55rem;line-height:1.35}.product-selector>footer img{width:22px;height:22px;border-radius:6px}
  .viewer-column{min-width:0}.empty-map-card{display:grid;place-items:center;min-height:clamp(620px,64vh,780px);background:var(--panel)}.empty-forecast{display:flex;align-items:center;flex-direction:column;color:var(--ink);text-align:center}.empty-forecast img{width:62px;height:62px;margin-bottom:18px;border-radius:16px;opacity:.88}.empty-forecast strong{font-size:1.72rem;letter-spacing:.14em}.empty-forecast span{margin-top:7px;color:var(--muted);font-size:.76rem;letter-spacing:.18em;text-transform:uppercase}.map-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 15px;border-bottom:1px solid var(--border)}.map-product{display:flex;align-items:center;gap:10px}.product-mark{width:4px;height:35px;border-radius:5px;background:var(--product-accent);box-shadow:0 0 16px color-mix(in srgb,var(--product-accent) 45%,transparent)}.product-title{display:flex;align-items:center;gap:7px}.product-title strong{font-size:.82rem}.product-title img{width:21px;height:21px;border-radius:6px}.map-head small{display:block;margin-top:3px;color:var(--muted);font-size:.61rem;font-variant-numeric:tabular-nums}.map-actions{display:flex;gap:5px}.map-actions button{width:31px;height:31px;border-radius:8px}
  .forecast-map{position:relative;min-height:clamp(620px,64vh,780px);overflow:hidden;background:#0b1926}:global(.theme-light) .forecast-map{background:#d5e1e6}.real-frame{position:absolute;inset:5% 7%;z-index:3;width:86%;height:90%;object-fit:contain;filter:drop-shadow(0 12px 24px rgba(0,0,0,.25))}.frame-state{position:absolute;left:50%;top:50%;z-index:9;display:flex;align-items:center;flex-direction:column;gap:6px;width:min(280px,70%);padding:16px;transform:translate(-50%,-50%);border:1px solid rgba(255,255,255,.12);border-radius:12px;color:#eaf3f8;background:rgba(6,16,25,.82);backdrop-filter:blur(10px);text-align:center}.frame-state strong{font-size:.72rem}.frame-state small{color:rgba(235,244,251,.65);font-size:.58rem;line-height:1.4}.frame-state.error{border-color:rgba(239,111,118,.32)}.frame-state button{margin-top:4px;padding:6px 9px;border:1px solid rgba(255,255,255,.14);border-radius:7px;color:#dceaf2;background:rgba(255,255,255,.06);font-size:.57rem}.spinner{width:20px;height:20px;border:2px solid rgba(255,255,255,.18);border-top-color:#70b9ef;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.legend{position:absolute;right:12px;bottom:12px;z-index:8;display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid rgba(255,255,255,.13);border-radius:9px;color:rgba(235,244,251,.8);background:rgba(6,16,25,.62);font-size:.6rem}.legend i{width:130px;height:8px;border-radius:99px;background:linear-gradient(90deg,#3b4cc0,#3288bd,#66c2a5,#e6f598,#fdae61,#d73027,#762a83)}
  .palette-precipitation .legend i{background:linear-gradient(90deg,#28465f,#2f6f8e,#369aa1,#58bd91,#9bd275,#d7dc69,#f2c55a,#ed914c,#df6262,#b44f88)}
  .map-watermark{position:absolute;left:14px;bottom:13px;z-index:7;display:flex;align-items:center;gap:8px;color:rgba(230,241,248,.58);pointer-events:none;user-select:none}.map-watermark img{width:27px;height:27px;border-radius:7px;opacity:.55}.map-watermark span{display:flex;flex-direction:column;line-height:1}.map-watermark strong{font-size:.56rem;letter-spacing:.12em}.map-watermark small{margin-top:4px;font-size:.46rem;letter-spacing:.16em;text-transform:uppercase}:global(.theme-light) .map-watermark{color:rgba(27,58,78,.52)}
  .forecast-map:fullscreen{min-height:100vh}
  .level-rail{position:absolute;right:12px;top:58px;bottom:54px;z-index:14;display:flex;width:92px;flex-direction:column;border:1px solid rgba(255,255,255,.14);border-radius:10px;color:#e8f2f7;background:rgba(5,14,22,.78);backdrop-filter:blur(10px);overflow:hidden}.level-rail header{padding:9px 9px 7px;border-bottom:1px solid rgba(255,255,255,.1)}.level-rail header strong,.level-rail header small{display:block}.level-rail header strong{font-size:.62rem}.level-rail header small{margin-top:2px;color:rgba(235,244,251,.55);font-size:.47rem}.level-kind{display:grid;grid-template-columns:1fr 1fr;gap:3px;padding:5px}.level-kind button,.level-list button{border:0;color:rgba(235,244,251,.62);background:transparent;font-size:.5rem}.level-kind button{padding:5px 2px;border-radius:5px}.level-kind button.active{color:#06131c;background:#68bdf1;font-weight:750}.level-list{display:flex;min-height:0;flex:1;flex-direction:column;overflow-y:auto;padding:2px 5px 6px}.level-list button{flex:0 0 25px;border-left:2px solid transparent;text-align:right}.level-list button:hover{color:#fff;background:rgba(255,255,255,.06)}.level-list button.active{border-left-color:#68bdf1;border-radius:4px;color:#8ed3ff;background:rgba(76,163,219,.12);font-weight:750}
  .timeline{display:grid;grid-template-columns:34px 34px 1fr 34px;align-items:center;gap:7px;padding:12px 14px 14px;border-top:1px solid var(--border)}.timeline>button{width:34px;height:34px;border-radius:9px}.timeline>button:disabled{opacity:.35;cursor:default}.timeline .play{color:#76bfff}.timeline .play.active{color:#08141f;background:#76bfff}.time-range{min-width:0;padding:0 5px}.time-labels{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:.57rem}.time-labels strong{color:var(--ink);font-size:.63rem}.time-range input{width:100%;margin:9px 0 2px;accent-color:#5faeea}.ticks{display:flex;justify-content:space-between;padding:0 3px}.ticks i{width:1px;height:3px;background:var(--border-2)}.ticks i.major{height:6px;background:var(--muted)}
  .product-explainer{margin-top:14px;padding:17px}.product-explainer>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding-bottom:14px;border-bottom:1px solid var(--border)}.explainer-identity{display:flex;align-items:center;gap:11px}.explainer-icon{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;color:#6ab7ef;background:rgba(62,142,208,.11)}.explainer-identity small{display:block;margin-bottom:3px;color:var(--muted);font-size:.56rem}.explainer-identity h3{font-size:.88rem}.source-tag{padding:5px 8px;border-radius:6px;color:#78baf0;background:rgba(62,142,208,.11);font-size:.55rem;font-weight:740;text-transform:uppercase}.source-tag.derived{color:#f08b9d;background:rgba(240,112,134,.1)}.product-explainer h4{margin-bottom:7px;color:var(--ink-2);font-size:.61rem;text-transform:uppercase;letter-spacing:.065em}.explanation-overview{display:grid;grid-template-columns:1fr;gap:19px;padding-top:17px}.explanation-overview p,.interpretation li,.calculation-detail p,.calculation-detail li{color:var(--muted);font-size:.65rem;line-height:1.62}.interpretation ul{display:grid;gap:8px;margin:0;padding-left:17px}.interpretation li::marker,.calculation-copy li::marker{color:#67b7ef}.calculation-detail{margin-top:19px;padding-top:17px;border-top:1px solid var(--border)}.calculation-copy{min-width:0}.calculation-copy ol{display:grid;gap:5px;margin:11px 0 0;padding-left:18px}.calculation-copy code{display:block;margin-top:12px;padding:7px 9px;border-radius:7px;color:#70b9ef;background:var(--panel-2);font-size:.52rem;overflow-wrap:anywhere}.technical-sources{display:grid;grid-template-columns:1fr;gap:12px;margin-top:17px;padding-top:14px;border-top:1px solid var(--border)}.technical-sources>strong{color:var(--ink-2);font-size:.58rem;text-transform:uppercase;letter-spacing:.055em}.technical-sources>div{display:flex;flex-wrap:wrap;gap:6px}.technical-sources a{padding:5px 7px;border:1px solid var(--border);border-radius:6px;color:#6db5e9;background:var(--panel-2);font-size:.53rem;line-height:1.35;text-decoration:none}.technical-sources a:hover{border-color:rgba(109,181,233,.42);color:#8bcbf8}
  @media(max-width:980px){.forecast-layout{grid-template-columns:220px minmax(0,1fr)}.forecast-map{min-height:440px}}
  @media(max-width:760px){.forecast-head{align-items:stretch;flex-direction:column}.run-status{min-width:0}.control-bar{flex-wrap:wrap}.forecast-layout{grid-template-columns:1fr}.product-selector{position:static;max-height:none}.category-list{max-height:360px}.forecast-map{min-height:390px}.map-head{align-items:flex-start}.map-actions button:nth-child(2){display:none}}
  @media(max-width:480px){.control-bar label{flex:1}.control-bar label>span{display:none}.forecast-map{min-height:330px}.legend i{width:76px}.timeline{grid-template-columns:32px 32px minmax(150px,1fr) 32px;padding-inline:8px}.timeline>button{width:32px;height:32px}.time-labels>span{display:none}.time-labels{justify-content:center}.product-explainer>header{align-items:flex-start;flex-direction:column}.source-tag{margin-left:47px}}
</style>

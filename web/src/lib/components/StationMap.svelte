<script>
  import { currentTheme as appTheme, loadTheme } from '$lib/theme.svelte.js';
  /**
   * El mapa de observación.
   *
   * Copia el comportamiento del componente que hoy usa Streamlit
   * (`components/temperature_clusters_frontend`): mismo mapa base, el campo
   * interpolado insertado BAJO el agua vectorial —para que el mar recorte la
   * costa en cada zoom en vez de enseñar el borde dentado de una imagen
   * mundial— y las estaciones como cajas HTML agrupadas por rejilla.
   *
   * Las cajas no son capas de MapLibre: son un overlay proyectado a mano. Con
   * trece mil puntos, agrupar en el navegador y pintar solo lo visible sale
   * más barato que mantener un símbolo por estación.
   */
  import { onMount } from 'svelte';

  import 'maplibre-gl/dist/maplibre-gl.css';

  import { cardinal, num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import {
    cellSizeForZoom,
    clusterByGrid,
    colorForPrecipitation,
    colorForTemperature,
    meanDirection,
    meanOf,
    textColor
  } from '$lib/map/markers.js';
  import { providerLabel } from '$lib/seo/i18n.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { convertUnit, unitLabel } from '$lib/units.js';

  let {
    points = [],
    // Catálogo compacto (arrays paralelos) del modo «Estaciones». Ahí no hay
    // agrupado: son decenas de miles de puntos y los pinta la GPU, igual que
    // hace hoy la aplicación con pydeck.
    catalog = null,
    layer = 'stations',
    language = 'es',
    centre = { lat: 41.39, lon: 2.15, zoom: 6 },
    onSelect,
    onMove
  } = $props();

  // Recorte del campo global que publica el backend: lo fija
  // `render_global_grid_png`.
  const FIELD_BOUNDS = { west: -180, east: 180, north: 85, south: -60 };
  const FIELD_URL = {
    temperature: '/v1/stations/temperature-field.png',
    wind: '/v1/stations/wind-field.png',
    precipitation: '/v1/stations/precipitation-field.png'
  };
  const CARTO = {
    dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
  };

  let container;
  let overlay;
  let map = null;
  let dark = true;
  let styleReady = false;
  let appliedFieldUrl = null;
  let failed = $state(false);
  let ready = $state(false);
  let hovered = $state(null);
  let frame = null;

  const valueOf = (point) =>
    layer === 'wind' ? point.speed : layer === 'precipitation' ? point.amount : point.t;

  /** Puntos utilizables, con su medida y su rumbo ya normalizados. */
  const usable = $derived(
    points
      .filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon))
      .map((point) => ({
        ...point,
        value: Number(valueOf(point)),
        direction: Number(point.direction)
      }))
  );

  const isCatalog = $derived(layer === 'stations');

  $effect(() => {
    usable;
    catalog;
    layer;
    unitPreferences.temperature;
    unitPreferences.wind;
    unitPreferences.precip;
    scheduleRender();
    applyField();
    applyCatalog();
  });

  /**
   * El mapa sigue al tema de la aplicación.
   *
   * El basemap es otro estilo entero —CARTO tiene uno claro y uno oscuro—, así
   * que cambiar de tema con un mapa abierto obliga a recargarlo. Al hacerlo se
   * pierden las capas propias, y por eso `style.load` las vuelve a montar.
   */
  $effect(() => {
    const wanted = appTheme();
    if (!map || !styleReady) return;
    if ((wanted === 'dark') === dark) return;
    dark = wanted === 'dark';
    styleReady = false;
    map.setStyle(CARTO[wanted]);
  });

  function currentTheme() {
    const explicit = document.documentElement.dataset.theme;
    if (explicit === 'light' || explicit === 'dark') return explicit;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  /**
   * Capa por encima de la cual va el campo.
   *
   * El raster se pone sobre los rellenos de tierra pero DEBAJO del agua
   * vectorial: CARTO vuelve a pintar mar, lagos y costa con su geometría en
   * cada nivel de zoom, así que la costa queda limpia sin ampliar el borde
   * alfa de una imagen mundial. Si el estilo no tuviera agua identificable,
   * se cae al primer símbolo, que al menos deja los nombres por encima.
   */
  function fieldBeforeId() {
    try {
      const layers = map.getStyle()?.layers || [];
      const water = layers.find(
        (item) =>
          item.type === 'fill' &&
          (String(item['source-layer'] || '').toLowerCase() === 'water' ||
            String(item.id || '').toLowerCase() === 'water')
      );
      if (water) return water.id;
      return layers.find((item) => item.type === 'symbol')?.id;
    } catch {
      return undefined;
    }
  }

  function applyField() {
    if (!map || !styleReady) return;
    const url = FIELD_URL[layer];
    if (!url) {
      if (map.getLayer('field')) map.setLayoutProperty('field', 'visibility', 'none');
      return;
    }
    if (map.getSource('field')) {
      if (appliedFieldUrl !== url) {
        map.getSource('field').updateImage({ url });
        appliedFieldUrl = url;
      }
      map.setLayoutProperty('field', 'visibility', 'visible');
      return;
    }
    appliedFieldUrl = url;
    map.addSource('field', {
      type: 'image',
      url,
      coordinates: [
        [FIELD_BOUNDS.west, FIELD_BOUNDS.north],
        [FIELD_BOUNDS.east, FIELD_BOUNDS.north],
        [FIELD_BOUNDS.east, FIELD_BOUNDS.south],
        [FIELD_BOUNDS.west, FIELD_BOUNDS.south]
      ]
    });
    map.addLayer(
      {
        id: 'field',
        type: 'raster',
        source: 'field',
        paint: {
          // Los mismos valores que la app actual: por debajo se ve apagado.
          'raster-opacity': 0.84,
          'raster-resampling': 'linear',
          'raster-fade-duration': 0
        }
      },
      fieldBeforeId()
    );
  }

  /** El catálogo entero como GeoJSON, construido desde los arrays. */
  function catalogCollection() {
    const features = [];
    const size = catalog?.lat?.length ?? 0;
    for (let index = 0; index < size; index += 1) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [catalog.lon[index], catalog.lat[index]] },
        properties: {
          name: catalog.name[index],
          provider: catalog.provider[index],
          station_id: catalog.station_id[index]
        }
      });
    }
    return { type: 'FeatureCollection', features };
  }

  /**
   * Capa de puntos del catálogo.
   *
   * Aquí no se agrupa ni se etiqueta: con cincuenta mil estaciones, los
   * rótulos ensucian y un nodo del DOM por punto es inviable. Un `circle` de
   * MapLibre lo resuelve en la tarjeta gráfica.
   */
  function applyCatalog() {
    if (!map || !styleReady) return;
    if (!isCatalog) {
      if (map.getLayer('catalog')) map.setLayoutProperty('catalog', 'visibility', 'none');
      return;
    }
    const data = catalogCollection();
    if (map.getSource('catalog')) {
      map.getSource('catalog').setData(data);
      map.setLayoutProperty('catalog', 'visibility', 'visible');
      return;
    }
    map.addSource('catalog', { type: 'geojson', data });
    map.addLayer({
      id: 'catalog',
      type: 'circle',
      source: 'catalog',
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 2, 6, 3.5, 10, 6, 14, 9],
        'circle-color': '#4f83f1',
        'circle-opacity': 0.9,
        'circle-stroke-width': 1,
        'circle-stroke-color': 'rgba(255,255,255,.65)'
      }
    });

    map.on('click', 'catalog', (event) => {
      const feature = event.features?.[0];
      if (feature) onSelect?.(feature.properties);
    });
    map.on('mousemove', 'catalog', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      map.getCanvas().style.cursor = 'pointer';
      const at = map.project(feature.geometry.coordinates);
      hovered = {
        name: feature.properties.name,
        meta: providerLabel(feature.properties.provider),
        rows: [],
        x: at.x,
        y: at.y
      };
    });
    map.on('mouseleave', 'catalog', () => {
      map.getCanvas().style.cursor = '';
      hovered = null;
    });
  }

  const scheduleRender = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      renderMarkers();
    });
  };

  /** Color de la caja según la vista. El catálogo va por red. */
  function markerColors(cluster, value) {
    if (layer === 'temperature') return colorForTemperature(value);
    if (layer === 'precipitation') return colorForPrecipitation(value);
    if (layer === 'wind') return dark ? [17, 22, 30] : [255, 255, 255];
    // Catálogo: un tono estable por proveedor, derivado de su nombre, para
    // que una red nueva entre sin tocar ninguna tabla.
    let hash = 0;
    for (const character of String(cluster[0].provider || '')) {
      hash = (hash * 31 + character.charCodeAt(0)) % 360;
    }
    const [r, g, b] = hslToRgb(hash / 360, 0.55, 0.58);
    return [r, g, b];
  }

  function hslToRgb(h, s, l) {
    const k = (n) => (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
  }

  function markerText(value) {
    if (layer === 'stations') return '';
    if (layer === 'wind') return num(convertUnit(value, 'wind', unitPreferences), { language, decimals: 0 });
    if (layer === 'precipitation') {
      return num(convertUnit(value, 'precip', unitPreferences), {
        language,
        decimals: unitPreferences.precip === 'in' ? 2 : 1
      });
    }
    return `${num(convertUnit(value, 'temperature', unitPreferences), { language, decimals: 0 })}°`;
  }

  function tooltipFor(point) {
    const rows = [];
    if (layer === 'wind') {
      rows.push([ui(language, 'layer_wind'), `${num(convertUnit(point.speed, 'wind', unitPreferences), { language, decimals: 0 })} ${unitLabel('wind', unitPreferences)}`]);
      if (Number.isFinite(point.gust)) {
        rows.push([ui(language, 'gust'), `${num(convertUnit(point.gust, 'wind', unitPreferences), { language, decimals: 0 })} ${unitLabel('wind', unitPreferences)}`]);
      }
      if (Number.isFinite(point.direction)) {
        rows.push([
          ui(language, 'direction'),
          `${cardinal(point.direction, language)} · ${num(point.direction, { language, decimals: 0 })}°`
        ]);
      }
    } else if (layer === 'precipitation') {
      rows.push([ui(language, 'layer_precipitation'), `${num(convertUnit(point.amount, 'precip', unitPreferences), { language, decimals: unitPreferences.precip === 'in' ? 2 : 1 })} ${unitLabel('precip', unitPreferences)}`]);
    } else if (layer === 'temperature') {
      // «Actual», no «Temperatura»: las tres filas son temperaturas y hay
      // sitio de sobra para escribir máxima y mínima enteras.
      rows.push([ui(language, 'temp_current'), `${num(convertUnit(point.t, 'temperature', unitPreferences), { language, decimals: 1 })} ${unitLabel('temperature', unitPreferences)}`]);
      if (Number.isFinite(point.tmax)) {
        rows.push([
          ui(language, 'maximum_full'),
          `${num(convertUnit(point.tmax, 'temperature', unitPreferences), { language, decimals: 1 })} ${unitLabel('temperature', unitPreferences)}`
        ]);
      }
      if (Number.isFinite(point.tmin)) {
        rows.push([
          ui(language, 'minimum_full'),
          `${num(convertUnit(point.tmin, 'temperature', unitPreferences), { language, decimals: 1 })} ${unitLabel('temperature', unitPreferences)}`
        ]);
      }
    }
    return {
      name: point.name || '',
      meta: [providerLabel(point.provider), point.time].filter(Boolean).join(' · '),
      rows
    };
  }

  /**
   * Pinta las cajas visibles.
   *
   * Se proyecta cada punto, se descartan los que caen fuera del lienzo y el
   * resto se agrupa por celdas. Un grupo con una sola estación se comporta
   * como estación: abre su panel. Con varias, acerca el mapa.
   */
  function renderMarkers() {
    if (!map || !overlay) return;
    if (isCatalog) {
      overlay.replaceChildren();
      return;
    }
    const canvas = map.getCanvas();
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const margin = 40;

    const projected = [];
    for (const point of usable) {
      const at = map.project([point.lon, point.lat]);
      if (at.x < -margin || at.y < -margin || at.x > width + margin || at.y > height + margin) {
        continue;
      }
      projected.push({ ...point, x: at.x, y: at.y });
    }

    const clusters = clusterByGrid(projected, cellSizeForZoom(map.getZoom()));
    const fragment = document.createDocumentFragment();

    for (const cluster of clusters) {
      const x = meanOf(cluster, 'x');
      const y = meanOf(cluster, 'y');
      const value =
        layer === 'precipitation'
          ? Math.max(...cluster.map((item) => item.value).filter(Number.isFinite), 0)
          : meanOf(cluster, 'value');

      // En el catálogo, una estación suelta es un punto y un grupo es una
      // caja con su número dentro. El globo rojo pegado al punto, que sí vale
      // para las capas con medida, aquí tapaba el mapa entero.
      const grouped = cluster.length > 1;
      const asDot = layer === 'stations' && !grouped;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = `mlx-marker${asDot ? ' dot' : ''}`;
      button.style.left = `${x}px`;
      button.style.top = `${y}px`;

      const rgb = markerColors(cluster, value);
      button.style.setProperty('--bg', `rgba(${rgb[0]},${rgb[1]},${rgb[2]},.94)`);
      button.style.setProperty('--fg', textColor(rgb));
      button.style.setProperty(
        '--edge',
        dark ? 'rgba(255,255,255,.86)' : 'rgba(30,33,40,.78)'
      );

      if (layer === 'stations') {
        if (grouped) {
          const text = document.createElement('span');
          text.textContent = String(cluster.length);
          button.appendChild(text);
        }
      } else if (layer === 'wind') {
        const arrow = document.createElement('span');
        arrow.className = 'mlx-arrow';
        arrow.textContent = '➤';
        const direction = meanDirection(cluster);
        if (Number.isFinite(direction)) {
          arrow.style.transform = `rotate(${direction + 90}deg)`;
        }
        button.appendChild(arrow);
      } else {
        const text = document.createElement('span');
        text.textContent = markerText(value);
        button.appendChild(text);
      }

      if (grouped) {
        if (layer !== 'stations') {
          const count = document.createElement('span');
          count.className = 'mlx-count';
          count.textContent = String(cluster.length);
          button.appendChild(count);
        }
        button.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          map.easeTo({
            center: [meanOf(cluster, 'lon'), meanOf(cluster, 'lat')],
            zoom: Math.min(17, (map.getZoom() || 0) + (cluster.length > 25 ? 2.5 : 2)),
            duration: 320
          });
        });
      } else {
        const point = cluster[0];
        button.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          onSelect?.(point);
        });
        button.addEventListener('pointerenter', () => {
          hovered = { ...tooltipFor(point), x, y };
        });
        button.addEventListener('pointerleave', () => {
          hovered = null;
        });
      }

      fragment.appendChild(button);
    }

    overlay.replaceChildren(fragment);
  }

  onMount(async () => {
    loadTheme();
    // Importaciones CON NOMBRE: desde la versión 6, MapLibre no publica export
    // por defecto.
    let MapConstructor;
    let NavigationControl;
    try {
      const maplibre = await import('maplibre-gl');
      ({ Map: MapConstructor, NavigationControl } = maplibre);

      // El worker, siempre desde `static/`, en desarrollo y en producción.
      //
      // En desarrollo, porque Vite inyecta su cliente HMR en todo lo que sirve
      // desde `node_modules`, worker incluido, y dentro de un Web Worker eso
      // muere en silencio.
      //
      // En producción, porque MapLibre 6 no deja que Rollup empaquete el
      // worker: lo resuelve al arrancar como `./maplibre-gl-worker.mjs` junto
      // al módulo, leyendo `import.meta.url`. Rollup no ve ese import y no
      // emite el fichero, así que la petición acababa en un 404 dentro de
      // `_app/immutable/chunks/` y el mapa se quedaba sin worker: en blanco en
      // Chrome, y en «Cargando el mapa…» eterno donde `style.load` no llega.
      //
      // `scripts/copy-maplibre-worker.mjs` deja el worker y su módulo
      // compartido en `static/maplibre/` antes de cada build.
      maplibre.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
    } catch (error) {
      console.error('[mapa] no se pudo cargar MapLibre', error);
      failed = true;
      return;
    }

    dark = currentTheme() === 'dark';

    try {
      map = new MapConstructor({
        container,
        style: CARTO[currentTheme()],
        center: [centre.lon, centre.lat],
        zoom: centre.zoom ?? 6,
        attributionControl: { compact: true }
      });
    } catch (error) {
      console.error('[mapa] MapLibre no pudo arrancar', error);
      failed = true;
      return;
    }

    map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    map.on('error', (event) => console.error('[mapa]', event?.error || event));

    // MapLibre mide el contenedor una sola vez, al construirse; si el CSS
    // llega después, el lienzo se queda pequeño.
    const resizer = new ResizeObserver(() => {
      map?.resize();
      scheduleRender();
    });
    resizer.observe(container);

    // `style.load` avisa de que el estilo admite capas. `load` espera además
    // al primer render y no siempre llega.
    // `on`, no `once`: al cambiar de tema se cambia el estilo entero y hay
    // que volver a montar las capas, que se van con el estilo viejo.
    // El primer `style.load` lo destraba el arranque; los siguientes son
    // cambios de tema y hay que volver a forzar el repintado.
    let started = false;
    if (import.meta.env.DEV) window.__mlxMap = map;

    map.on('style.load', () => {
      styleReady = true;
      ready = true;
      applyField();
      applyCatalog();
      renderMarkers();
      if (started) repaintAgain();
      started = true;
    });

    // Un `resize` en cuanto el mapa se asienta por primera vez. Sin él el
    // lienzo se quedaba con el estilo y las teselas cargadas pero sin
    // repintar, y el mapa salía negro aunque `isStyleLoaded()` dijera que
    // todo estaba listo. En `style.load` es demasiado pronto: las teselas
    // todavía no han llegado.
    // El lienzo se queda a veces con el estilo y las teselas cargadas pero sin
    // repintar, y el mapa sale negro aunque MapLibre diga que todo está listo.
    // Se fuerza un repintado en cuanto el mapa base termina de cargar y otro
    // cuando todo se asienta; `resize` es lo que lo destraba.
    let repainted = false;
    const forceRepaint = () => {
      if (repainted) return;
      repainted = true;
      map.resize();
      map.triggerRepaint();
      scheduleRender();
    };
    // Cambiar de tema es cambiar el estilo entero, y el lienzo vuelve a
    // quedarse sin repintar igual que en el arranque: se rearma el destrabe.
    const repaintAgain = () => {
      repainted = false;
      forceRepaint();
    };
    map.on('sourcedata', (event) => {
      if (event.isSourceLoaded) forceRepaint();
    });
    map.once('idle', forceRepaint);

    for (const event of ['move', 'zoom', 'moveend', 'zoomend']) {
      map.on(event, scheduleRender);
    }
    map.on('movestart', () => (hovered = null));

    map.on('moveend', () => {
      if (!onMove) return;
      const { lat, lng } = map.getCenter();
      onMove({ lat, lon: lng, zoom: map.getZoom() });
    });

    return () => {
      resizer.disconnect();
      if (frame) cancelAnimationFrame(frame);
      map?.remove();
    };
  });

  /** Centro actual de la vista, para calcular distancias. Se llama distinto
   *  que la prop `centre`, que es el centro INICIAL. */
  export function viewCentre() {
    if (!map) return null;
    const { lat, lng } = map.getCenter();
    return { lat, lon: lng };
  }

  export function flyTo(lon, lat, zoom = 9) {
    map?.flyTo({ center: [lon, lat], zoom });
  }
</script>

<div class="map-wrap">
  <div class="canvas" bind:this={container}></div>
  <div class="markers" bind:this={overlay}></div>

  {#if hovered}
    <div class="tooltip" style:left="{hovered.x}px" style:top="{hovered.y}px">
      <strong>{hovered.name}</strong>
      {#if hovered.meta}<span class="meta">{hovered.meta}</span>{/if}
      {#if hovered.rows.length}
        <dl>
          {#each hovered.rows as [label, value] (label)}
            <div><dt>{label}</dt><dd>{value}</dd></div>
          {/each}
        </dl>
      {/if}
    </div>
  {/if}

  {#if failed}
    <p class="cover">{ui(language, 'map_no_webgl')}</p>
  {:else if !ready}
    <p class="cover">{ui(language, 'map_loading')}</p>
  {/if}
</div>

<style>
  .map-wrap {
    position: relative;
    height: min(74vh, 760px);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    overflow: hidden;
    background: var(--panel-2);
  }
  .canvas { position: absolute; inset: 0; }

  .markers { position: absolute; inset: 0; z-index: 2; pointer-events: none; overflow: hidden; }

  .markers :global(.mlx-marker) {
    position: absolute;
    transform: translate(-50%, -50%);
    min-width: 31px;
    height: 31px;
    padding: 0 7px;
    border-radius: 999px;
    border: 2px solid var(--edge);
    background: var(--bg);
    color: var(--fg);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28);
    font: 700 13px/27px var(--font);
    text-align: center;
    white-space: nowrap;
    pointer-events: auto;
    cursor: pointer;
    user-select: none;
  }
  .markers :global(.mlx-marker:hover) { transform: translate(-50%, -50%) scale(1.08); z-index: 3; }

  /* Catálogo: puntos, sin nombre. Los rótulos ensucian con miles de estaciones. */
  .markers :global(.mlx-marker.dot) {
    min-width: 11px;
    width: 11px;
    height: 11px;
    padding: 0;
    border-width: 1.5px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
  }

  .markers :global(.mlx-arrow) {
    display: block;
    width: 27px;
    font: 800 20px/27px var(--font);
    transform-origin: 50% 50%;
  }

  .markers :global(.mlx-count) {
    display: inline-block;
    min-width: 15px;
    height: 15px;
    margin-left: 3px;
    padding: 0 3px;
    border-radius: 999px;
    background: #ff514c;
    color: #fff;
    font: 800 9px/15px var(--font);
    vertical-align: 1px;
  }
  .tooltip {
    position: absolute;
    z-index: 4;
    transform: translate(-50%, calc(-100% - 22px));
    min-width: 175px;
    max-width: 290px;
    padding: 10px 12px;
    border: 1px solid var(--border-2);
    border-radius: 10px;
    background: var(--panel);
    box-shadow: var(--shadow);
    pointer-events: none;
  }
  .tooltip strong { display: block; font-size: 0.84rem; font-weight: 660; }
  .tooltip .meta { display: block; margin-top: 2px; font-size: 0.7rem; color: var(--muted); }
  .tooltip dl { margin: 8px 0 0; display: grid; gap: 3px; }
  .tooltip dl div { display: flex; justify-content: space-between; gap: 14px; }
  .tooltip dt { font-size: 0.72rem; color: var(--muted); }
  .tooltip dd { margin: 0; font-size: 0.75rem; font-weight: 650; font-variant-numeric: tabular-nums; }

  .cover {
    position: absolute; inset: 0; z-index: 5; display: grid; place-items: center;
    padding: 20px; text-align: center; color: var(--muted); font-size: 0.88rem;
    background: var(--panel-2);
  }

  .map-wrap :global(.maplibregl-ctrl-group) { background: var(--card); border: 1px solid var(--border); }
  .map-wrap :global(.maplibregl-ctrl-attrib) { font-size: 10px; }
</style>

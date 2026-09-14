<script>
  /**
   * Mapa de avisos: zonas coloreadas por nivel sobre el dominio de AROME.
   *
   * Mismo mapa base y mismo arranque que `StationMap`. Las zonas son una
   * fuente GeoJSON con dos capas —relleno y contorno— que se pintan bajo las
   * etiquetas del basemap para que los nombres de ciudad sigan leyéndose. Lo
   * que queda fuera de la rejilla del modelo se vela: sin eso, «sin aviso» y
   * «sin datos» se verían igual.
   */
  import { onMount } from 'svelte';

  import 'maplibre-gl/dist/maplibre-gl.css';

  import { ui } from '$lib/i18n/ui.js';
  import { currentTheme } from '$lib/theme.svelte.js';
  import { AROME_BOUNDS, LEVEL_COLORS } from '$lib/warnings/warnings.js';

  let { zones, levels = {}, selected = '', language = 'es', onSelect } = $props();

  const CARTO = {
    dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
  };
  const { west, south, east, north } = AROME_BOUNDS;
  const DOMAIN_RING = [[west, south], [east, south], [east, north], [west, north], [west, south]];
  // El mundo con el dominio recortado: el velo cubre justo lo que AROME no ve.
  const OUTSIDE = {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], [...DOMAIN_RING].reverse()]
    }
  };

  let container;
  let map = null;
  let styleReady = false;
  let dark = true;
  let ready = $state(false);
  let failed = $state(false);

  /** Color de relleno por zona: una expresión `match` con las que tienen aviso. */
  function fillColor(current) {
    const pairs = Object.entries(current).flatMap(([zone, level]) => [zone, LEVEL_COLORS[level]]);
    return pairs.length ? ['match', ['get', 'zone'], ...pairs, 'rgba(0,0,0,0)'] : 'rgba(0,0,0,0)';
  }

  function firstLabelLayer() {
    return map.getStyle().layers.find((layer) => layer.type === 'symbol')?.id;
  }

  function mountLayers() {
    if (!map || !styleReady || map.getSource('zones')) return;
    const before = firstLabelLayer();
    map.addSource('zones', { type: 'geojson', data: zones });
    map.addSource('outside', { type: 'geojson', data: OUTSIDE });
    map.addLayer({
      id: 'zones-fill', type: 'fill', source: 'zones',
      paint: { 'fill-color': fillColor(levels), 'fill-opacity': 0.62 }
    }, before);
    map.addLayer({
      id: 'zones-line', type: 'line', source: 'zones',
      paint: { 'line-color': dark ? 'rgba(200,215,235,0.28)' : 'rgba(30,50,80,0.25)', 'line-width': 0.6 }
    }, before);
    map.addLayer({
      id: 'zones-selected', type: 'line', source: 'zones',
      filter: ['==', ['get', 'zone'], selected || ''],
      paint: { 'line-color': dark ? '#ffffff' : '#10203a', 'line-width': 2.2 }
    }, before);
    map.addLayer({
      id: 'outside-fill', type: 'fill', source: 'outside',
      paint: { 'fill-color': dark ? '#05090f' : '#8a96a6', 'fill-opacity': dark ? 0.55 : 0.35 }
    });
    map.addLayer({
      id: 'domain-line', type: 'line', source: 'outside',
      paint: { 'line-color': dark ? '#a7b6c9' : '#42546c', 'line-width': 1.2, 'line-dasharray': [3, 2] }
    });
  }

  // Niveles y selección cambian con los filtros: basta con reescribir la
  // pintura de las capas, sin tocar la fuente.
  $effect(() => {
    const current = levels;
    const zone = selected;
    if (!map || !styleReady || !map.getLayer('zones-fill')) return;
    map.setPaintProperty('zones-fill', 'fill-color', fillColor(current));
    map.setFilter('zones-selected', ['==', ['get', 'zone'], zone || '']);
  });

  // Al elegir una zona desde la lista, el mapa va a ella.
  $effect(() => {
    const zone = selected;
    if (!map || !zone) return;
    const feature = zones.features.find((item) => item.properties.zone === zone);
    const bounds = feature && featureBounds(feature);
    if (bounds) map.fitBounds(bounds, { padding: 60, maxZoom: 7, duration: 700 });
  });

  // El basemap sigue al tema de la aplicación; las capas se vuelven a montar
  // en `style.load`, que es cuando el estilo nuevo las admite.
  $effect(() => {
    const wanted = currentTheme();
    if (!map || !styleReady || (wanted === 'dark') === dark) return;
    dark = wanted === 'dark';
    styleReady = false;
    map.setStyle(CARTO[wanted]);
  });

  function featureBounds(feature) {
    const { type, coordinates } = feature.geometry;
    const polygons = type === 'Polygon' ? [coordinates] : coordinates;
    let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const polygon of polygons) {
      for (const [x, y] of polygon[0]) {
        minX = Math.min(minX, x); minY = Math.min(minY, y);
        maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
      }
    }
    return Number.isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null;
  }

  export function fitDomain() {
    map?.fitBounds([[west, south], [east, north]], { padding: 20, duration: 600 });
  }

  onMount(() => {
    let resizer;
    (async () => {
      let maplibre;
      try {
        maplibre = await import('maplibre-gl');
        maplibre.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
      } catch (error) {
        console.error('[avisos] no se pudo cargar MapLibre', error);
        failed = true;
        return;
      }

      dark = currentTheme() === 'dark';
      try {
        map = new maplibre.Map({
          container,
          style: CARTO[dark ? 'dark' : 'light'],
          bounds: [[west, south], [east, north]],
          fitBoundsOptions: { padding: 20 },
          attributionControl: { compact: true }
        });
      } catch (error) {
        console.error('[avisos] MapLibre no pudo arrancar', error);
        failed = true;
        return;
      }

      map.addControl(new maplibre.NavigationControl({ showCompass: false }), 'top-right');
      map.on('error', (event) => console.error('[avisos]', event?.error || event));

      resizer = new ResizeObserver(() => map?.resize());
      resizer.observe(container);

      map.on('style.load', () => {
        styleReady = true;
        ready = true;
        mountLayers();
      });
      // Mismo destrabe que el mapa de estaciones: sin un `resize` cuando llegan
      // las teselas, el lienzo puede quedarse sin repintar.
      map.once('idle', () => {
        map.resize();
        map.triggerRepaint();
      });

      map.on('click', 'zones-fill', (event) => {
        const zone = event.features?.[0]?.properties?.zone;
        if (zone && levels[zone]) onSelect?.(zone);
      });
      map.on('mousemove', 'zones-fill', (event) => {
        const zone = event.features?.[0]?.properties?.zone;
        map.getCanvas().style.cursor = zone && levels[zone] ? 'pointer' : '';
      });
      map.on('mouseleave', 'zones-fill', () => (map.getCanvas().style.cursor = ''));
    })();

    return () => {
      resizer?.disconnect();
      map?.remove();
      map = null;
    };
  });
</script>

<div class="map-wrap">
  <div class="canvas" bind:this={container}></div>
  {#if failed}
    <p class="cover">{ui(language, 'map_no_webgl')}</p>
  {:else if !ready}
    <p class="cover">{ui(language, 'map_loading')}</p>
  {/if}
</div>

<style>
  .map-wrap {
    position: relative;
    height: min(62vh, 620px);
    min-height: 360px;
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    overflow: hidden;
    background: var(--panel-2);
  }
  .canvas { position: absolute; inset: 0; }
  .cover {
    position: absolute; inset: 0; display: grid; place-items: center;
    font-size: 0.84rem; color: var(--muted); pointer-events: none;
  }
</style>

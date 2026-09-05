<script>
  import { currentConnection, loadConnection } from '$lib/connection.svelte.js';
  import AppShell from '$lib/components/AppShell.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import FieldLegend from '$lib/components/FieldLegend.svelte';
  import MapFilters from '$lib/components/MapFilters.svelte';
  import SelectedStation from '$lib/components/SelectedStation.svelte';
  import StationMap from '$lib/components/StationMap.svelte';
  import { locale, num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { rememberViewSearch } from '$lib/view-memory.svelte.js';

  import { appTabs } from '$lib/tabs.js';
  import { SITE_URL, providerLabel } from '$lib/seo/i18n.js';

  let { data } = $props();
  const lang = $derived(data.lang);

  let selected = $state(null);
  let resolving = $state(false);

  // La estación elegida pertenece a los puntos que había en pantalla. Al
  // cambiar de vista o de zona esos puntos son otros, así que arrastrar la
  // tarjeta anterior enseña una estación que ya no está en el mapa.
  $effect(() => {
    data.layer;
    data.points;
    selected = null;
  });
  let mapRef;

  const layers = $derived([
    { id: 'stations', slug: 'estaciones', label: ui(lang, 'layer_stations') },
    { id: 'temperature', slug: 'temperatura', label: ui(lang, 'layer_temperature') },
    { id: 'wind', slug: 'viento', label: ui(lang, 'layer_wind') },
    { id: 'precipitation', slug: 'precipitacion', label: ui(lang, 'layer_precipitation') }
  ]);

  const slugOf = $derived(
    (id) => layers.find((item) => item.id === id)?.slug || 'estaciones'
  );

  /** Nombre del país en el idioma de la página; el catálogo solo da el ISO2. */
  const countryName = $derived((code) => {
    try {
      return new Intl.DisplayNames([locale(lang)], { type: 'region' }).of(code) || code;
    } catch {
      return code;
    }
  });

  /** Países con estaciones, ordenados por su nombre traducido. */
  const countryOptions = $derived(
    Object.entries(data.countries || {})
      .filter(([code, count]) => code && code !== 'UN' && count > 0)
      .map(([code, count]) => ({ code, count, name: countryName(code) }))
      .sort((a, b) => a.name.localeCompare(b.name, locale(lang)))
  );

  /** Hora del último refresco del ranking, para las capas con medida. */
  const updated = $derived(
    data.updatedAt
      ? new Intl.DateTimeFormat(locale(lang), { hour: '2-digit', minute: '2-digit' }).format(
          new Date(data.updatedAt)
        )
      : ''
  );

  const tabs = $derived(appTabs({
    language: lang,
    slug: currentConnection()?.slug || '',
    observationPath: currentConnection()?.path || ''
  }));

  /**
   * Al pinchar una estación se piden dos cosas: su ficha completa —el mapa
   * solo lleva posición, red e identificador— y el slug de su panel, porque
   * los puntos se identifican por el ID de su red.
   */
  async function select(properties) {
    const provider = properties.provider;
    const stationId = properties.station_id;
    selected = { station: { ...properties }, slug: '', distanceKm: null };
    resolving = true;
    try {
      const [station, slugPayload] = await Promise.all([
        fetch(`/v1/stations/${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}`)
          .then((response) => (response.ok ? response.json() : null))
          .catch(() => null),
        fetch(`/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`)
          .then((response) => (response.ok ? response.json() : null))
          .catch(() => null)
      ]);
      selected = {
        station: station || { ...properties },
        slug: slugPayload?.url_slug || '',
        distanceKm: distanceFromCentre(station || properties)
      };
    } finally {
      resolving = false;
    }
  }

  /** Distancia al centro del mapa, como la «Distancia» de la app actual. */
  function distanceFromCentre(station) {
    if (!mapRef || !Number.isFinite(station?.lat) || !Number.isFinite(station?.lon)) return null;
    const centre = mapRef.viewCentre?.();
    if (!centre) return null;
    const toRad = (degrees) => (degrees * Math.PI) / 180;
    const dLat = toRad(station.lat - centre.lat);
    const dLon = toRad(station.lon - centre.lon);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(centre.lat)) * Math.cos(toRad(station.lat)) * Math.sin(dLon / 2) ** 2;
    return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function locate() {
    navigator.geolocation?.getCurrentPosition((position) =>
      mapRef?.flyTo(position.coords.longitude, position.coords.latitude, 9)
    );
  }

  // La estación conectada vive en el navegador: se lee al hidratar y las
  // pestañas dejan de perderla al pasar por aquí.
  onMount(loadConnection);

  // La capa forma parte de la sección: saber si se mira el mapa de
  // estaciones o el de precipitación es justo lo que interesa contar.
  $effect(() => recordSection(`map.${data.layer}`));

  // Con qué filtros se está mirando: al volver desde otra pestaña, la
  // barra devuelve esta misma vista en vez de la pelada.
  $effect(() => rememberViewSearch('map', page.url.search));
</script>

<svelte:head>
  <title>{ui(lang, 'map_title')} | MeteoLabX</title>
  <meta name="description" content={ui(lang, 'map_subtitle', { count: data.points.length })} />
  <link rel="canonical" href={`${SITE_URL}/${lang}/map`} />
  <meta name="robots" content="noindex, follow" />
</svelte:head>

<AppShell language={lang} {tabs} active="map" alternates={[]}>
  <div class="map-head">
    <div>
      <h2>{ui(lang, 'map_title')}</h2>
      <p>
        {#if data.layer === 'stations'}
          {ui(lang, 'map_visible_subtitle', {
            count: num(data.catalog?.count ?? 0, { language: lang, decimals: 0 })
          })}
          {#if data.catalog?.truncated}
            · {ui(lang, 'map_truncated', {
              count: num(data.catalog.count, { language: lang, decimals: 0 })
            })}
          {/if}
        {:else}
          {ui(lang, 'map_subtitle', {
            count: num(data.points.length, { language: lang, decimals: 0 })
          })}
          {#if updated}· {ui(lang, 'map_updated', { time: updated })}{/if}
        {/if}
        · {ui(lang, 'map_click_hint')}
      </p>
    </div>
    <div class="controls">
      <div class="seg" role="group">
        {#each layers as item (item.id)}
          <a href={`/${lang}/map?capa=${item.slug}`} class:active={item.id === data.layer}>{item.label}</a>
        {/each}
      </div>
      {#if data.layer === 'stations'}
        <MapFilters
          language={lang}
          filters={data.filters}
          sensorKeys={data.sensorKeys}
          countries={data.countries}
          {countryName}
        />
      {/if}
      <button class="geo" type="button" onclick={locate}>{ui(lang, 'use_my_location')}</button>
    </div>
  </div>

  <!-- En «Estaciones» los datos viajan en `catalog`, no en `points`. -->
  {#if data.points.length || data.catalog?.count}
    <StationMap
      bind:this={mapRef}
      points={data.points}
      catalog={data.catalog}
      layer={data.layer}
      language={lang}
      centre={data.centre}
      onSelect={select}
    />
  {:else}
    <p class="empty">{ui(lang, 'map_empty')}</p>
  {/if}

  <!-- La leyenda solo tiene sentido donde hay campo: el catálogo no mide. -->
  {#if data.layer !== 'stations' && data.points.length}
    <FieldLegend layer={data.layer} language={lang} />
  {/if}

  {#if selected}
    <SelectedStation
      station={selected.station}
      slug={selected.slug}
      distanceKm={selected.distanceKm}
      language={lang}
      loading={resolving}
    />
  {/if}

  <SiteFooter language={lang} />
</AppShell>

<style>
  .map-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .map-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .map-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted);  text-wrap: balance; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg a { padding: 6px 12px; border-radius: 7px; font-size: 0.74rem; font-weight: 600; color: var(--muted); text-decoration: none; }
  .seg a:hover { color: var(--ink-2); }
  .seg a.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }


  .geo { padding: 7px 13px; border: 1px solid var(--border); border-radius: 9px; background: var(--card); color: var(--ink-2); font-size: 0.74rem; font-weight: 600; }
  .geo:hover { border-color: var(--border-2); color: var(--ink); }

  .empty { padding: 60px 0; text-align: center; color: var(--muted); font-size: 0.9rem; }
</style>

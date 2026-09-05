<script>
  import { onMount } from 'svelte';
  import { navigatingTo } from '$lib/navigation.js';
  /**
   * Ficha de la estación elegida en el mapa.
   *
   * Los mismos datos y las mismas acciones que la aplicación actual:
   * metadatos, conectar, guardar en favoritos y autoconexión al arrancar.
   *
   * «Conectar» aquí significa abrir su panel: en esta aplicación la estación
   * conectada es la que está en la URL, no un estado de sesión escondido.
   */
  import {
    autoconnectSlug,
    isFavourite,
    loadFavourites,
    setAutoconnect,
    toggleFavourite
  } from '$lib/favourites.svelte.js';
  import { num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { providerLabel, sensorLabel } from '$lib/seo/i18n.js';

  let { station, slug, language, distanceKm = null, loading = false } = $props();

  let saved = $state(false);
  let autoconnect = $state(false);

  // `localStorage` no existe en el servidor: se lee al montar, una sola vez.
  //
  // Y solo al montar: llamando a `loadFavourites()` desde el efecto, este
  // escribía el estado que él mismo lee y se re-disparaba sin fin. Svelte
  // abortaba con «maximum update depth» y con él se caía la página entera:
  // el mapa se quedaba clavado y ni siquiera navegaba al pulsar «Conectar».
  onMount(loadFavourites);

  $effect(() => {
    saved = isFavourite(favouriteKey);
    autoconnect = autoconnectSlug() === favouriteKey;
  });

  const sensors = $derived(
    Object.entries(station?.sensors || {})
      .filter(([, present]) => present)
      .map(([key]) => sensorLabel(language, key))
  );

  const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);

  /** Ruta del panel: por slug si lo hay, por red e identificador si no. */
  const target = $derived(
    slug
      ? `/${language}/observation/${slug}`
      : `/${language}/observation/${encodeURIComponent(station.provider)}/${encodeURIComponent(station.station_id)}`
  );

  /** Identidad del favorito: su slug, o la ruta cuando la red no tiene. */
  const favouriteKey = $derived(slug || target);

  function onFavourite() {
    saved = toggleFavourite({
      slug,
      path: slug ? '' : target,
      name: station.name,
      provider: station.provider
    });
  }

  function onAutoconnect(event) {
    autoconnect = event.currentTarget.checked;
    setAutoconnect(autoconnect ? favouriteKey : '');
  }
</script>

<section class="picked">
  <h2>{ui(language, 'selected_station')}</h2>

  <div class="body">
    <div class="meta">
      <p class="title">
        <strong>{station.name}</strong>
        <span>· {providerLabel(station.provider)}</span>
      </p>

      <dl>
        <div><dt>{ui(language, 'identifier')}</dt><dd class="mono">{station.station_id}</dd></div>
        {#if station.locality}
          <div><dt>{ui(language, 'locality')}</dt><dd>{station.locality}</dd></div>
        {/if}
        {#if station.elevation !== null && station.elevation !== undefined}
          <div>
            <dt>{ui(language, 'altitude')}</dt>
            <dd class="mono">{num(station.elevation, { language, decimals: 0 })} m</dd>
          </div>
        {/if}
        {#if distanceKm !== null}
          <div>
            <dt>{ui(language, 'distance')}</dt>
            <dd class="mono">{num(distanceKm, { language })} km</dd>
          </div>
        {/if}
        <div>
          <dt>Lat/Lon</dt>
          <dd class="mono">{station.lat?.toFixed(4)}, {station.lon?.toFixed(4)}</dd>
        </div>
        <div>
          <dt>{ui(language, 'station_type_filters')}</dt>
          <dd>{ui(language, station.manual ? 'type_manual' : 'type_automatic')}</dd>
        </div>
      </dl>

      {#if sensors.length}
        <p class="sensors">
          <span class="label">{ui(language, 'sensors_label')}</span>
          {#each sensors as sensor (sensor)}<b>{capitalize(sensor)}</b>{/each}
        </p>
      {/if}
    </div>

    <div class="actions">
      <!-- Todas las redes se conectan. Las que tienen ficha indexable van por
           su slug; el resto, por red e identificador. Los datos son los
           mismos: lo único que no tienen es URL que posicionar. -->
      <a class="connect" class:busy={navigatingTo(target)} href={target}>
        {#if navigatingTo(target)}<span class="spin" aria-hidden="true"></span>{/if}
        {navigatingTo(target) ? ui(language, 'connecting') : ui(language, 'connect')}
      </a>
      <button class="favourite" type="button" onclick={onFavourite} aria-pressed={saved}>
        {saved ? ui(language, 'saved_favourite') : ui(language, 'save_favourite')}
      </button>
      <label class="auto">
        <input type="checkbox" checked={autoconnect} onchange={onAutoconnect} />
        <span>{ui(language, 'autoconnect')}</span>
      </label>
    </div>
  </div>
</section>

<style>
  .connect.busy { opacity: 0.8; pointer-events: none; }
  .spin {
    width: 12px; height: 12px; flex: none;
    border: 2px solid rgba(255, 255, 255, 0.35); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation: none; } }

  .picked {
    margin-top: 14px; padding: 16px 18px;
    border: 1px solid var(--border-2); border-radius: var(--r-md);
    background: var(--card);
  }
  h2 { font-size: 0.96rem; font-weight: 700; margin-bottom: 12px; }
  .body { display: flex; gap: 24px; justify-content: space-between; flex-wrap: wrap; }
  .meta { flex: 1 1 380px; min-width: 0; }

  .title { font-size: 0.94rem; margin-bottom: 10px; }
  .title span { color: var(--muted); font-weight: 500; }

  dl { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0; }
  dl div { display: flex; align-items: baseline; gap: 7px; }
  dt { font-size: 0.72rem; color: var(--muted); }
  dd { margin: 0; font-size: 0.8rem; font-weight: 640; }
  .mono { font-family: var(--mono); font-size: 0.76rem; }

  .sensors { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; }
  .sensors .label { font-size: 0.72rem; color: var(--muted); margin-right: 2px; }
  .sensors b {
    padding: 3px 8px; border-radius: 999px;
    background: var(--panel-2); border: 1px solid var(--border);
    font-size: 0.7rem; font-weight: 620; color: var(--ink-2);
  }

  .actions { display: flex; flex-direction: column; gap: 9px; min-width: 190px; }
  .connect {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    padding: 10px 18px; border-radius: var(--r-sm);
    background: var(--accent); color: var(--accent-ink);
    font-size: 0.86rem; font-weight: 700; text-decoration: none;
  }
  .favourite {
    padding: 9px 18px; border-radius: var(--r-sm);
    border: 1px solid var(--border-2); background: var(--panel-2);
    color: var(--ink-2); font-size: 0.82rem; font-weight: 650;
  }
  .favourite[aria-pressed='true'] { color: var(--accent); border-color: var(--accent); }
  .auto { display: flex; align-items: center; gap: 7px; font-size: 0.74rem; color: var(--muted); cursor: pointer; }
  .auto input { accent-color: var(--accent); }
  .unavailable { font-size: 0.78rem; color: var(--muted); }
</style>

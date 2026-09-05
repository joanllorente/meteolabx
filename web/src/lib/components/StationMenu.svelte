<script>
  /**
   * Menú de estación en la barra: siempre a mano, en todas las pestañas.
   *
   * Conectarse a una estación guardada era un viaje: volver a la portada,
   * buscar la lista y pinchar. Aquí están la estación conectada, las
   * favoritas y el acceso al buscador, se esté donde se esté.
   */
  import { onMount } from 'svelte';

  import { closeOnOutside } from '$lib/close-on-outside.js';
  import { currentConnection, forgetConnection } from '$lib/connection.svelte.js';
  import {
    favouriteHref,
    favouriteKey,
    listFavourites,
    loadFavourites,
    toggleFavourite
  } from '$lib/favourites.svelte.js';
  import { ui } from '$lib/i18n/ui.js';

  let { language } = $props();

  let panel;

  // La lista es estado compartido: guardar desde el mapa o desde la ficha se
  // ve aquí sin recargar.
  const favourites = $derived(listFavourites());

  onMount(loadFavourites);

  const connection = $derived(currentConnection());
  const connectedKey = $derived(favouriteKey(connection || {}));
  const saved = $derived(Boolean(connectedKey) && favourites.some((item) => favouriteKey(item) === connectedKey));

  function toggleCurrent() {
    if (!connection) return;
    toggleFavourite({
      slug: connection.slug || '',
      path: connection.path || '',
      name: connection.name || '',
      provider: connection.provider || ''
    });
  }

  const remove = (favourite) => toggleFavourite(favourite);

  function disconnect() {
    forgetConnection();
    if (panel) panel.open = false;
  }
</script>

<details
  class="station"
  bind:this={panel}
  use:closeOnOutside
  ontoggle={(event) => event.currentTarget.open && loadFavourites()}
>
  <summary title={ui(language, 'favourites')}>
    <svg viewBox="0 0 24 24" aria-hidden="true" class:filled={saved}>
      <path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8Z" />
    </svg>
    {#if connection?.name}<span class="who">{connection.name}</span>{/if}
  </summary>

  <div class="panel">
    {#if connection}
      <div class="current">
        <div>
          <small>{ui(language, 'connected_station')}</small>
          <strong>{connection.name || connection.slug}</strong>
        </div>
        <div class="acts">
          <button type="button" onclick={toggleCurrent}>
            {saved ? ui(language, 'saved_favourite') : ui(language, 'save_favourite')}
          </button>
          <a href="/" onclick={disconnect}>{ui(language, 'disconnect')}</a>
        </div>
      </div>
    {/if}

    <div class="list-head">{ui(language, 'favourites')}</div>
    {#if favourites.length}
      <ul>
        {#each favourites as favourite (favouriteKey(favourite))}
          <li>
            <a href={favouriteHref(favourite, language)}>
              <strong>{favourite.name || favourite.slug}</strong>
              {#if favourite.provider}<span>{favourite.provider}</span>{/if}
            </a>
            <button
              type="button"
              class="drop"
              title={ui(language, 'remove_favourite')}
              aria-label={ui(language, 'remove_favourite')}
              onclick={() => remove(favourite)}
            >×</button>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="empty">{ui(language, 'no_favourites')}</p>
    {/if}

    <a class="search" href={`/${language}`}>{ui(language, 'connect_other')}</a>
  </div>
</details>

<style>
  .station { position: relative; }
  summary {
    display: inline-flex; align-items: center; gap: 7px;
    max-width: 210px; padding: 5px 10px 5px 8px;
    border: 1px solid var(--border); border-radius: 9px;
    background: var(--card); color: var(--muted);
    font-size: 0.74rem; font-weight: 600; cursor: pointer; list-style: none;
  }
  summary::-webkit-details-marker { display: none; }
  summary:hover { color: var(--ink); border-color: var(--border-2); }
  summary svg { width: 15px; height: 15px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linejoin: round; }
  summary svg.filled { fill: var(--accent); stroke: var(--accent); }
  .who { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }

  .panel {
    position: absolute; z-index: 40; top: calc(100% + 7px); right: 0;
    width: min(340px, calc(100vw - 28px));
    padding: 10px; border: 1px solid var(--border-2); border-radius: 12px;
    background: var(--panel); box-shadow: var(--shadow);
  }

  /* Nombre arriba y acciones debajo: en la misma fila, los dos botones le
     robaban el ancho al nombre y «Barcelona - Observatori Fabra» caía en tres
     líneas. */
  .current {
    display: flex; flex-direction: column; gap: 8px;
    padding: 8px 9px 10px; margin-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  .current small { display: block; font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted-2); }
  .current strong {
    display: block; font-size: 0.82rem; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .acts { display: flex; gap: 6px; justify-content: flex-end; }
  .acts button, .acts a {
    padding: 4px 8px; border: 1px solid var(--border); border-radius: 7px;
    background: var(--card); color: var(--muted); font-size: 0.66rem; font-weight: 600;
    text-decoration: none; white-space: nowrap;
  }
  .acts button:hover, .acts a:hover { color: var(--ink); border-color: var(--border-2); }

  .list-head { padding: 2px 9px 6px; font-size: 0.62rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted-2); }

  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; max-height: 280px; overflow-y: auto; }
  li { display: flex; align-items: center; gap: 6px; border-radius: 8px; }
  li:hover { background: var(--card); }
  li a { flex: 1; display: flex; flex-direction: column; gap: 1px; padding: 7px 9px; text-decoration: none; }
  li strong { font-size: 0.78rem; color: var(--ink); }
  li span { font-size: 0.64rem; color: var(--muted); }
  .drop { padding: 0 9px; border: 0; background: none; color: var(--muted-2); font-size: 1rem; line-height: 1; }
  .drop:hover { color: var(--ink); }

  .empty { padding: 8px 9px 12px; font-size: 0.74rem; color: var(--muted); }

  .search {
    display: block; margin-top: 8px; padding: 8px;
    border-top: 1px solid var(--border);
    color: var(--accent); font-size: 0.74rem; font-weight: 650; text-decoration: none; text-align: center;
  }
  .search:hover { text-decoration: underline; }

  @media (max-width: 760px) {
    .who { display: none; }
  }
</style>

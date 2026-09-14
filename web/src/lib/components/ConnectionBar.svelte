<script>
  import { page } from '$app/state';
  import { navigatingTo } from '$lib/navigation.js';
  /**
   * Conexión de estación.
   *
   * Sustituye a la barra lateral de Streamlit: una sola caja que acepta una
   * localidad («Girona»), unas coordenadas («41.38, 2.17») o la ubicación del
   * navegador, y devuelve las estaciones publicables más cercanas.
   *
   * Es un formulario GET, así que funciona sin JavaScript y cada búsqueda
   * queda en la URL.
   */
  import { ui } from '$lib/i18n/ui.js';
  import { convertUnit, unitLabel, unitPreferences } from '$lib/units.svelte.js';
  import { providerLabel } from '$lib/seo/i18n.js';
  import { stationKey } from '$lib/seo/station.js';
  import { tick } from 'svelte';
  import {
    forgetSearch, loadRecentSearches, matchRecentSearches, rememberSearch
  } from '$lib/recent-searches.js';

  let {
    language, query = '', place = '', results = [], failed = false, searched = false,
    hideAmateur = false
  } = $props();

  /**
   * Enlace que enciende o apaga las estaciones de particulares.
   *
   * El filtro viaja en la URL, como la búsqueda: así se puede compartir el
   * resultado y funciona sin JavaScript.
   */
  const toggleHref = $derived.by(() => {
    const params = new URLSearchParams(page?.url?.search || '');
    if (hideAmateur) params.delete('sin-particulares');
    else params.set('sin-particulares', 'si');
    const query = params.toString();
    return `${page?.url?.pathname || '/'}${query ? `?${query}` : ''}`;
  });

  let locating = $state(false);

  /**
   * Búsquedas recientes, desplegadas bajo la caja al pulsarla.
   *
   * Se apunta una búsqueda cuando el servidor la ha resuelto a un sitio: lo
   * que no encontró nada no merece volver a ofrecerse.
   */
  let form = $state();
  let text = $state('');
  let recents = $state([]);
  let open = $state(false);
  let active = $state(-1);
  const suggestions = $derived(matchRecentSearches(recents, text));
  const showRecents = $derived(open && suggestions.length > 0);

  $effect.pre(() => {
    text = query;
  });

  $effect(() => {
    recents = searched && query && place && !failed
      ? rememberSearch({ query, label: place })
      : loadRecentSearches();
  });

  function openRecents() {
    open = true;
    active = -1;
  }

  async function pick(item) {
    text = item.query;
    open = false;
    await tick();
    form?.requestSubmit();
  }

  function forget(item) {
    recents = forgetSearch(item.query);
    active = -1;
  }

  function onKeydown(event) {
    if (event.key === 'Escape') {
      open = false;
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!showRecents) {
        openRecents();
        return;
      }
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const count = suggestions.length;
      active = active < 0 && step < 0 ? count - 1 : (active + step + count) % count;
      return;
    }
    if (event.key === 'Enter' && showRecents && active >= 0) {
      event.preventDefault();
      pick(suggestions[active]);
    }
  }

  function locate() {
    if (!navigator.geolocation) return;
    locating = true;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        window.location.href = `/?lat=${latitude.toFixed(4)}&lon=${longitude.toFixed(4)}`;
      },
      () => {
        locating = false;
      },
      { timeout: 8000 }
    );
  }
</script>

<section class="connect">
  <div class="head">
    <h2>{ui(language, 'connect_title')}</h2>
    <p>{ui(language, 'connect_hint')}</p>
  </div>

  <form method="GET" action="/" bind:this={form}>
    <div class="combo">
      <input
        type="search"
        name="q"
        bind:value={text}
        placeholder={ui(language, 'connect_placeholder')}
        autocomplete="off"
        enterkeyhint="search"
        aria-label={ui(language, 'connect_title')}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showRecents}
        aria-controls="recent-searches"
        aria-activedescendant={showRecents && active >= 0 ? `recent-${active}` : undefined}
        onfocus={openRecents}
        onclick={() => (open = true)}
        oninput={openRecents}
        onblur={() => (open = false)}
        onkeydown={onKeydown}
      />
      {#if showRecents}
        <!-- `mousedown` con `preventDefault`: si no, la caja pierde el foco y
             el desplegable se cierra antes de que llegue el clic. -->
        <div class="recents" role="presentation" onmousedown={(event) => event.preventDefault()}>
          <p class="recents-head">{ui(language, 'recent_searches')}</p>
          <ul id="recent-searches" role="listbox" aria-label={ui(language, 'recent_searches')}>
            {#each suggestions as item, index (item.query)}
              <!-- El teclado se lleva desde la caja con `aria-activedescendant`. -->
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <li
                id="recent-{index}"
                role="option"
                aria-selected={index === active}
                class:active={index === active}
                tabindex="-1"
                onclick={() => pick(item)}
                onmouseenter={() => (active = index)}
              >
                <svg class="clock" viewBox="0 0 16 16" aria-hidden="true">
                  <circle cx="8" cy="8" r="6.2" /><path d="M8 4.6V8l2.3 1.5" />
                </svg>
                <span class="recent-text">
                  <strong>{item.query}</strong>
                  {#if item.label && item.label !== item.query}<span>{item.label}</span>{/if}
                </span>
                <button
                  type="button"
                  class="forget"
                  aria-label={ui(language, 'remove_recent')}
                  title={ui(language, 'remove_recent')}
                  onclick={(event) => { event.stopPropagation(); forget(item); }}
                >×</button>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </div>
    <button class="go" type="submit">{ui(language, 'search')}</button>
    <button class="geo" type="button" onclick={locate} disabled={locating}>
      {locating ? ui(language, 'locating') : ui(language, 'use_my_location')}
    </button>
  </form>

  {#if failed}
    <p class="note">{ui(language, 'search_failed')}</p>
  {:else if searched && !results.length}
    <p class="note">{ui(language, 'no_results')}</p>
  {/if}

  {#if results.length}
    <div class="results">
      <div class="results-head">
        <strong>{ui(language, 'nearby_results')}</strong>
        {#if place}<span>{place}</span>{/if}
        <a class="filter" class:on={hideAmateur} href={toggleHref} data-sveltekit-noscroll>
          {ui(language, hideAmateur ? 'show_amateur' : 'hide_amateur')}
        </a>
      </div>
      <ul>
        {#each results as station (stationKey(station))}
          {@const target = station.url_slug
            ? `/${language}/observation/${station.url_slug}`
            : `/${language}/observation/${encodeURIComponent(station.provider)}/${encodeURIComponent(station.station_id)}`}
          <li class:busy={navigatingTo(target)}>
            <a href={target}>
              <strong>{station.name}</strong>
              {#if navigatingTo(target)}<span class="spin" aria-hidden="true"></span>{/if}
              <span>{providerLabel(station.provider)}</span>
            </a>
            <span class="dist">{convertUnit(station.distance_km, 'distance', unitPreferences).toFixed(1)} {unitLabel('distance', unitPreferences)}</span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</section>

<style>
  .results li strong {
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }

  /* Fila a la que se está conectando. El giro va pegado al nombre, que es lo
     que se ha pulsado: a la derecha de la fila están los datos —el valor, la
     distancia— y taparlos con un indicador es peor que no ponerlo. */
  .spin {
    display: inline-block; width: 11px; height: 11px; margin-left: 8px;
    vertical-align: -1px; flex: none;
    border: 2px solid var(--border-2); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  .busy { opacity: 0.75; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation: none; } }

  .connect {
    margin-bottom: 26px; padding: 20px 22px;
    border: 1px solid var(--border-2); border-radius: var(--r-md);
    background: var(--panel);
  }
  .head h2 { font-size: 1.02rem; font-weight: 700; letter-spacing: -0.01em; }
  .head p { margin-top: 4px; font-size: 0.82rem; color: var(--muted); }

  form { display: flex; gap: 9px; flex-wrap: wrap; margin-top: 15px; }
  .combo { position: relative; flex: 1 1 250px; display: flex; }
  input {
    flex: 1; min-width: 0; padding: 12px 15px;
    border: 1px solid var(--border-2); border-radius: var(--r-sm);
    background: var(--card); color: var(--ink); font: inherit;
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

  .recents {
    position: absolute; z-index: 20; top: calc(100% + 4px); left: 0; right: 0;
    padding: 6px 0; border: 1px solid var(--border-2); border-radius: var(--r-sm);
    background: var(--card); box-shadow: 0 10px 28px rgb(0 0 0 / 0.16);
  }
  .recents-head {
    padding: 4px 14px 6px; font-size: 0.68rem; font-weight: 650;
    letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted);
  }
  .recents ul { display: block; }
  .recents li {
    display: flex; align-items: center; gap: 11px; justify-content: flex-start;
    padding: 8px 8px 8px 14px; border: 0; border-radius: 0; background: none;
    cursor: pointer;
  }
  .recents li.active, .recents li:hover { background: var(--card-hover); }
  .clock {
    width: 15px; height: 15px; flex: none;
    fill: none; stroke: var(--muted); stroke-width: 1.4; stroke-linecap: round;
  }
  .recent-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
  .recent-text strong {
    font-size: 0.88rem; font-weight: 560; color: var(--ink);
    display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .recent-text span {
    font-size: 0.72rem; color: var(--muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .forget {
    flex: none; width: 26px; height: 26px; border: 0; border-radius: 50%;
    background: none; color: var(--muted); font-size: 1.05rem; line-height: 1;
    opacity: 0;
  }
  .recents li.active .forget, .recents li:hover .forget, .forget:focus-visible { opacity: 1; }
  .forget:hover { color: var(--ink); background: var(--border); }
  @media (hover: none) { .forget { opacity: 1; } }

  .go, .geo {
    padding: 12px 17px; border-radius: var(--r-sm);
    border: 1px solid transparent; font-weight: 680; font-size: 0.86rem;
  }
  .go { background: var(--accent); color: var(--accent-ink); }
  .geo { background: var(--card); border-color: var(--border); color: var(--ink-2); }
  .geo:disabled { opacity: 0.6; cursor: progress; }

  .note { margin-top: 12px; color: var(--muted); font-size: 0.84rem; }

  .results { margin-top: 18px; padding-top: 15px; border-top: 1px solid var(--border); }
  .filter {
    margin-left: auto; padding: 3px 9px;
    border: 1px solid var(--border); border-radius: 999px;
    color: var(--muted); font-size: 0.68rem; font-weight: 600; text-decoration: none;
  }
  .filter:hover { color: var(--ink-2); border-color: var(--border-2); }
  .filter.on { color: var(--accent); border-color: var(--accent); }

  .results-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .results-head strong { font-size: 0.86rem; font-weight: 660; }
  .results-head span { font-size: 0.74rem; color: var(--muted); }
  /* Columnas fijas por tamaño de pantalla, no `auto-fit`: doce resultados
     son divisibles por cuatro, por tres y por dos, así que la última fila
     siempre queda completa y todas las tarjetas miden lo mismo. */
  ul {
    list-style: none; margin: 0; padding: 0;
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
    /* Todas las filas del mismo alto: si no, una fila con un nombre de tres
       líneas queda el doble de gruesa que la de al lado. */
    grid-auto-rows: 1fr;
  }
  @media (max-width: 1100px) { ul { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 760px) { ul { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 460px) { ul { grid-template-columns: 1fr; } }
  li {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 11px 13px; border: 1px solid var(--border); border-radius: var(--r-sm);
    background: var(--card);
  }
  li:hover { border-color: var(--border-2); background: var(--card-hover); }
  li a { display: flex; flex-direction: column; gap: 2px; text-decoration: none; min-width: 0; }
  li strong { font-size: 0.88rem; font-weight: 640; }
  li a span { font-size: 0.72rem; color: var(--muted); }
  .dist { font-size: 0.78rem; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>

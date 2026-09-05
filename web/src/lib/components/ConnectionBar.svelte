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
  import { providerLabel } from '$lib/seo/i18n.js';

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

  <form method="GET" action="/">
    <input
      type="search"
      name="q"
      value={query}
      placeholder={ui(language, 'connect_placeholder')}
      autocomplete="off"
      enterkeyhint="search"
      aria-label={ui(language, 'connect_title')}
    />
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
        {#each results as station (station.provider + station.station_id)}
          {@const target = station.url_slug
            ? `/${language}/observation/${station.url_slug}`
            : `/${language}/observation/${encodeURIComponent(station.provider)}/${encodeURIComponent(station.station_id)}`}
          <li class:busy={navigatingTo(target)}>
            <a href={target}>
              <strong>{station.name}</strong>
              {#if navigatingTo(target)}<span class="spin" aria-hidden="true"></span>{/if}
              <span>{providerLabel(station.provider)}</span>
            </a>
            <span class="dist">{station.distance_km.toFixed(1)} km</span>
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
  input {
    flex: 1 1 250px; padding: 12px 15px;
    border: 1px solid var(--border-2); border-radius: var(--r-sm);
    background: var(--card); color: var(--ink); font: inherit;
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
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

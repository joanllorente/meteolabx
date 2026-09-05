<script>
  import { onMount } from 'svelte';
  import TopNav from './lib/TopNav.svelte';
  import ForecastStationControls from './lib/ForecastStationControls.svelte';
  import ForecastFooter from './lib/ForecastFooter.svelte';
  import ForecastView from './views/ForecastView.svelte';
  import UnitPreferences from '../../web/src/lib/components/UnitPreferences.svelte';
  import { forecastText } from './lib/forecast-i18n.js';
  // El tema es el mismo módulo que usa el resto del sitio: tres estados
  // —automático, claro y oscuro— y, en automático, nada guardado. El visor
  // tenía su propia copia de dos estados que al arrancar guardaba el tema
  // resuelto, y con eso convertía el automático de todo el sitio en una
  // elección fija en cuanto alguien abría Predicción.
  import { currentMode, currentTheme, cycleTheme, loadTheme } from '../../web/src/lib/theme.svelte.js';

  const entryParams = new URLSearchParams(window.location.search);
  // Idioma y estación conectada con los que se llegó desde el resto de la web.
  // La barra los necesita para que salir del visor devuelva a donde se estaba.
  const LANGUAGES = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
  const entryLanguage = entryParams.get('lang') || '';
  const language = LANGUAGES.includes(entryLanguage) ? entryLanguage : 'es';
  function readConnection() {
    try {
      const value = JSON.parse(localStorage.getItem('mlx-connection') || 'null');
      return value && (value.slug || value.path) ? value : null;
    } catch { return null; }
  }
  const initialConnection = readConnection();
  let slug = $state(entryParams.get('slug') || initialConnection?.slug || '');
  let observationPath = $state(initialConnection?.path || '');
  const visitSection = entryParams.get('from') === 'streamlit'
    ? 'forecast.streamlit'
    : 'forecast.direct';
  function updateConnection(connection) {
    slug = connection?.slug || '';
    observationPath = connection?.path || '';
  }

  onMount(loadTheme);

  onMount(() => {
    // El HTML del visor es uno solo y se sirve en español —es la versión
    // indexada—, así que el título de la pestaña y el idioma del documento se
    // ajustan aquí: quien llega desde la web en inglés veía la interfaz
    // traducida y la pestaña en castellano.
    document.title = forecastText(language, 'pageTitle');
    document.documentElement.lang = language;
    // El origen se usa una sola vez y se retira de la URL: si el visitante
    // copia después el enlace, la siguiente apertura contará como directa.
    if (entryParams.has('from')) {
      entryParams.delete('from');
      const query = entryParams.toString();
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
      );
    }
    fetch('/v1/stats/section', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section: visitSection }),
      keepalive: true
    }).catch(() => {});
  });
</script>

<div class="forecast-shell theme-{currentTheme()}">
  <TopNav {language} {slug} {observationPath}>
    {#snippet stationControls()}
      <ForecastStationControls {language} onConnectionChange={updateConnection} />
    {/snippet}
    {#snippet themeControl()}
      <UnitPreferences {language} />
      <button
        class="theme"
        type="button"
        onclick={cycleTheme}
        aria-label={forecastText(language, 'theme')}
      >
        <!-- Los mismos tres iconos que la barra del resto del sitio, dibujados
             aquí en vez de tomados de la librería: los de lucide tienen otro
             trazo y el automático no se parecía en nada al de la web. -->
        {#if currentMode() === 'auto'}
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8.2" />
            <path d="M12 3.8a8.2 8.2 0 0 0 0 16.4Z" fill="currentColor" stroke="none" />
          </svg>
        {:else if currentMode() === 'light'}
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="4.4" />
            <g stroke-linecap="round">
              <path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6" />
            </g>
          </svg>
        {:else}
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2Z" />
          </svg>
        {/if}
      </button>
    {/snippet}
  </TopNav>

  <main class="forecast-wrap">
    <ForecastView {language} />
    <ForecastFooter {language} />
  </main>
</div>

<style>
  .forecast-shell {
    min-height: 100vh;
    color: var(--ink);
    color-scheme: dark;
    background: var(--bg);
  }

  .forecast-shell.theme-light { color-scheme: light; }

  .theme {
    display: inline-flex; align-items: center; justify-content: center;
    width: 36px; height: 36px;
    border: 1px solid var(--border); border-radius: 9px;
    color: var(--ink-2); background: var(--panel);
  }
  .theme svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; }
  .theme:hover { color: var(--ink); border-color: var(--border-2); }

  /* Mismo contenedor que las páginas SvelteKit: al cambiar de sección el
     contenido conserva exactamente sus márgenes y su línea de lectura. */
  .forecast-wrap { width: min(1240px, calc(100% - 40px)); margin: 0 auto; padding: 26px 0 40px; }

  @media (max-width: 720px) {
    .forecast-wrap { width: min(100% - 30px, 1240px); padding: 19px 0 32px; }
  }
</style>

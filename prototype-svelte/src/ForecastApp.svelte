<script>
  import { onMount } from 'svelte';
  import { ArrowLeft, Moon, Sun } from '@lucide/svelte';
  import ForecastView from './views/ForecastView.svelte';

  const assetBase = import.meta.env.BASE_URL;
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const forecastHome = isLocal ? `${assetBase}forecast.html?v=20260825-54` : '/forecast';
  const entryParams = new URLSearchParams(window.location.search);
  const visitSection = entryParams.get('from') === 'streamlit'
    ? 'forecast.streamlit'
    : 'forecast.direct';
  let theme = $state(localStorage.getItem('mlx-forecast-theme') || 'dark');
  $effect(() => localStorage.setItem('mlx-forecast-theme', theme));

  onMount(() => {
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

<div class="forecast-shell theme-{theme}">
  <header class="forecast-nav">
    <a class="brand" href={forecastHome} aria-label="MeteoLabx Predicción">
      <img src={`${assetBase}mlx-logo.png`} alt="" />
      <span>
        <strong>MeteoLabx</strong>
        <small>Versión 1.4.0</small>
      </span>
    </a>

    <div class="nav-actions">
      <button type="button" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')} aria-label="Cambiar tema">
        {#if theme === 'dark'}<Sun size={17} />{:else}<Moon size={17} />{/if}
      </button>
      <a href={import.meta.env.VITE_METEOLABX_APP_URL || '/'}>
        <ArrowLeft size={15} /><span>Volver a MeteoLabX</span>
      </a>
    </div>
  </header>

  <main class="forecast-wrap">
    <ForecastView />
    <footer>
      <span>MeteoLabx Predicción · prototipo AROME</span>
      <span>Météo-France · cartografía estatal · producto experimental</span>
    </footer>
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

  .forecast-nav {
    position: sticky;
    top: 0;
    z-index: 30;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 18px;
    padding: 11px 26px;
    border-bottom: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg) 84%, transparent);
    backdrop-filter: blur(16px);
  }

  .brand { display: inline-flex; align-items: center; gap: 11px; width: fit-content; color: inherit; text-decoration: none; }
  .brand img { width: 37px; height: 37px; border-radius: 10px; box-shadow: 0 5px 16px rgba(0,0,0,.3); }
  .brand > span { display: flex; flex-direction: column; line-height: 1.12; }
  .brand strong { font-size: .93rem; letter-spacing: -.02em; }
  .brand small { margin-top: 3px; color: var(--muted); font-size: .57rem; }

  .nav-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
  .nav-actions button, .nav-actions a { display: inline-flex; align-items: center; justify-content: center; height: 36px; border: 1px solid var(--border); border-radius: 9px; color: var(--ink-2); background: var(--panel); }
  .nav-actions button { width: 36px; }
  .nav-actions a { gap: 7px; padding: 0 12px; font-size: .68rem; font-weight: 640; text-decoration: none; }
  .nav-actions button:hover, .nav-actions a:hover { color: var(--ink); border-color: var(--border-2); }

  .forecast-wrap { max-width: 1280px; margin: 0 auto; padding: 25px 26px 8px; }
  footer { display: flex; justify-content: space-between; gap: 12px; padding: 25px 0 29px; color: var(--muted-2); font-size: .62rem; }

  @media (max-width: 720px) {
    .forecast-nav { grid-template-columns: 1fr auto; padding: 10px 15px; }
    .nav-actions a span { display: none; }
    .nav-actions a { width: 36px; padding: 0; }
    .forecast-wrap { padding: 19px 15px 8px; }
  }
</style>

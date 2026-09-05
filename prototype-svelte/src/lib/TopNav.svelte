<script>
  import brandMark from '../assets/brand-mark.png';
  import app from './app-i18n.generated.js';
  /**
   * La barra superior de la web, dentro del visor de Predicción.
   *
   * Es el mismo marcado y el mismo CSS que `AppShell.svelte` en `web/`: el
   * visor es un proyecto Vite aparte y no puede importar sus componentes, pero
   * comparte hoja de variables —`--bg`, `--ink`, `--border`…— porque los dos
   * salen del mismo prototipo. Los nombres de las pestañas no se copian a
   * mano: los exporta `scripts/export_app_i18n.py` desde `locales/*.json`.
   *
   * El idioma llega por la URL y la estación se comparte mediante el mismo
   * almacenamiento local que usa SvelteKit.
  */
  import { LayoutDashboard, Map, Trophy } from '@lucide/svelte';
  import TABS from './tabs-i18n.generated.js';

  let { language = 'es', slug = '', observationPath = '', stationControls, themeControl } = $props();

  const LANGUAGES = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
  const labels = $derived(TABS[language] || TABS.es);
  const navIcons = { LayoutDashboard, Map, Trophy };

  /** Predicción se lleva el idioma y la estación para poder volver a ellos. */
  const forecastHref = $derived((code) => {
    const params = new URLSearchParams();
    if (code !== 'es') params.set('lang', code);
    if (slug) params.set('slug', slug);
    const query = params.toString();
    return query ? `/forecast?${query}` : '/forecast';
  });

  const tabs = $derived([
    { id: 'observation', label: labels.observation, icon: 'LayoutDashboard', href: slug ? `/${language}/observation/${slug}` : (observationPath || '/') },
    { id: 'map', label: labels.map, icon: 'Map', href: `/${language}/map` },
    { id: 'forecast', label: labels.forecast, symbol: '∂', href: forecastHref(language), active: true },
    { id: 'ranking', label: labels.ranking, icon: 'Trophy', href: `/${language}/ranking` }
  ]);
</script>

<header class="topnav">
  <a class="brand" href="/">
    <!-- El mismo logotipo que el resto del sitio. Se importa para que
         viaje dentro del artefacto: el visor se sirve tanto desde
         `/forecast` del servicio nuevo como desde Streamlit, y una ruta
         absoluta a la raíz solo existiría en uno de los dos. -->
    <img class="mark" src={brandMark} alt="" width="26" height="26" decoding="async" />
    <span class="brand-txt">
      <strong>MeteoLabX</strong>
      <span class="brand-version">{app.app_version}</span>
    </span>
  </a>

  <nav class="tabs" aria-label="Secciones">
    {#each tabs as tab (tab.id)}
      {#if tab.disabled}
        <span class="tab off" aria-disabled="true"><span>{tab.label}</span></span>
      {:else}
        <a
          href={tab.href}
          class="tab"
          class:active={tab.active}
          aria-current={tab.active ? 'page' : undefined}
        >
          {#if tab.symbol}
            <span class="nav-symbol" aria-hidden="true">{tab.symbol}</span>
          {:else if tab.icon && navIcons[tab.icon]}
            {@const NavIcon = navIcons[tab.icon]}
            <NavIcon size={15} strokeWidth={1.9} aria-hidden="true" />
          {/if}
          <span>{tab.label}</span>
        </a>
      {/if}
    {/each}
  </nav>

  <div class="right">
    {@render stationControls?.()}
    <nav class="languages" aria-label="Idioma">
      {#each LANGUAGES as code (code)}
        <a
          href={forecastHref(code)}
          hreflang={code}
          lang={code}
          aria-current={code === language ? 'page' : undefined}
        >{code.toUpperCase()}</a>
      {/each}
    </nav>
    {@render themeControl?.()}
  </div>
</header>

<style>
  .topnav {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 22px;
    padding: 11px clamp(14px, 3vw, 30px);
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border);
  }
  .brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: inherit; }
  .mark { width: 26px; height: 26px; border-radius: 7px; flex: none; display: block; }
  .brand-txt { display: inline-flex; align-items: baseline; gap: 6px; }
  .brand-version { font-size: 0.64rem; font-weight: 700; color: var(--muted); letter-spacing: 0.02em; }
  .brand-txt strong { font-size: 0.98rem; font-weight: 750; letter-spacing: -0.01em; }

  .tabs { display: flex; gap: 3px; margin-left: 6px; overflow-x: auto; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .tab {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 7px 13px; border-radius: 9px;
    font-size: 0.82rem; font-weight: 600; color: var(--muted);
    text-decoration: none; white-space: nowrap;
    transition: background 0.16s, color 0.16s;
  }
  a.tab:hover { color: var(--ink-2); background: var(--card); }
  a.tab.active { color: var(--ink); background: var(--card); }
  .tabs .off { color: var(--muted-2); cursor: default; }
  .nav-symbol {
    width: 15px; height: 15px;
    display: inline-grid; place-items: center;
    font: 700 1.08rem/1 Georgia, 'Times New Roman', serif;
  }

  .right { margin-left: auto; display: flex; align-items: center; gap: 12px; }

  .languages { display: flex; gap: 2px; }
  .languages a {
    padding: 3px 6px;
    color: var(--muted);
    font-size: 0.74rem;
    font-weight: 600;
    text-decoration: none;
    border-radius: 6px;
  }
  .languages a:hover { color: var(--ink); background: var(--card); }
  .languages a[aria-current='page'] { color: var(--ink); background: var(--card); }

  @media (max-width: 760px) {
    .topnav { gap: 12px; }
    .languages { display: none; }
  }
</style>

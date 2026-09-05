<script>
  import { currentConnection, loadConnection } from '$lib/connection.svelte.js';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { rememberViewSearch } from '$lib/view-memory.svelte.js';
  import AppShell from '$lib/components/AppShell.svelte';
  import RankingPanel from '$lib/components/RankingPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { locale } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { SITE_URL } from '$lib/seo/i18n.js';
  import { appTabs } from '$lib/tabs.js';

  let { data } = $props();
  const lang = $derived(data.lang);

  /** Nombre del país en el idioma de la página; el catálogo solo da el ISO2. */
  const countryName = $derived((code) => {
    if (!code) return ui(lang, 'scope_global');
    try {
      return new Intl.DisplayNames([locale(lang)], { type: 'region' }).of(code) || code;
    } catch {
      return code;
    }
  });

  /** Enlaces que conservan el resto de la selección al cambiar una cosa. */
  const query = $derived((overrides) => {
    const merged = {
      metrica: data.metric,
      ambito: data.global ? 'global' : '',
      pais: data.global ? '' : data.country,
      dia: data.ranking.day,
      orden: data.reversed ? 'inverso' : '',
      'sin-antartida': data.withoutAntarctica ? 'si' : '',
      ...overrides
    };
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(merged)) if (value) params.set(key, value);
    return `?${params}`;
  });

  const links = $derived({
    countries: data.countries
      .map((code) => ({ code, name: countryName(code) }))
      .sort((a, b) => a.name.localeCompare(b.name, locale(lang))),
    metric: (metric) => query({ metrica: metric }),
    scope: (global) => query({ ambito: global ? 'global' : '', pais: global ? '' : data.country }),
    // Al invertir se conserva todo lo demás; solo cambia el sentido.
    order: () => query({ orden: data.reversed ? '' : 'inverso' }),
    day: (iso) => query({ dia: iso }),
    antarctica: () => query({ 'sin-antartida': data.withoutAntarctica ? '' : 'si' })
  });

  // La estación conectada vive en el navegador: se lee al hidratar y las
  // pestañas dejan de perderla al pasar por aquí.
  onMount(() => recordSection('ranking'));

  onMount(loadConnection);

  // Con qué filtros se está mirando: al volver desde otra pestaña, la
  // barra devuelve esta misma vista en vez de la pelada.
  $effect(() => rememberViewSearch('ranking', page.url.search));
</script>

<svelte:head>
  <title>{ui(lang, 'ranking_title')} | MeteoLabX</title>
  <meta name="description" content={ui(lang, 'ranking_subtitle', { limit: data.limit })} />
  <link rel="canonical" href={`${SITE_URL}/${lang}/ranking`} />
  <!-- Se indexa la lista limpia, no cada combinación de métrica, país y día. -->
  <meta
    name="robots"
    content={data.global || data.metric !== 'tmax' || data.reversed ? 'noindex, follow' : 'index, follow'}
  />
</svelte:head>

<AppShell language={lang} tabs={appTabs({
    language: lang,
    slug: currentConnection()?.slug || '',
    observationPath: currentConnection()?.path || ''
  })} active="ranking" alternates={[]}>
  <RankingPanel {data} language={lang} {countryName} {links} />
  <SiteFooter language={lang} />
</AppShell>

<script>
  import { forgetConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { onMount } from 'svelte';
  import AppShell from '$lib/components/AppShell.svelte';
  import HistoricalPanel from '$lib/components/HistoricalPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { SITE_URL } from '$lib/seo/i18n.js';
  import { appTabs, observationTabs, stationStripe } from '$lib/tabs.js';

  let { data } = $props();
  const { station, meta, lang, slug, mode } = $derived(data);

  // El periodo ya no viaja en enlaces: lo manda el formulario del panel.
  const base = $derived(`/${lang}/historical/${slug}`);

  // Se recuerda con qué estación se está trabajando, para que la barra
  // siga apuntando a ella al pasar por el mapa o el ranking.
  // `$effect`, no `onMount`: al saltar de una estación a otra el componente
  // se reutiliza y el montaje no vuelve a ocurrir; lo que cambia son los datos.
  $effect(() => {
    rememberConnection({ slug, name: meta.name, provider: meta.provider });
  });

  onMount(() => recordSection('historical'));
</script>

<svelte:head>
  <title>{ui(lang, 'historical_title')} · {meta.name} | MeteoLabX</title>
  <meta name="description" content={meta.description} />
  <meta name="robots" content="noindex, follow" />
  <link rel="canonical" href={`${SITE_URL}${base}`} />
</svelte:head>

<AppShell
  language={lang}
  tabs={appTabs({ language: lang, slug })}
  subtabs={observationTabs({ language: lang, slug })}
  active="historical"
  station={stationStripe(station, meta)}
  alternates={meta.alternates}
  disconnectHref="/"
  onDisconnect={forgetConnection}
>
  <HistoricalPanel
    summary={data.summary}
    language={lang}
    {mode}
    unsupported={!data.supported}
    stationName={meta.name}
    selection={data.selection}
    requested={data.requested}
    warning={data.warning}
    period={data.period}
    maxBlocks={data.maxBlocks}
    failure={data.failure}
    provider={meta.provider || data.provider}
  />
  <SiteFooter language={lang} />
</AppShell>

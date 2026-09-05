<script>
  import { forgetConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { onMount } from 'svelte';
  import AppShell from '$lib/components/AppShell.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import TrendsPanel from '$lib/components/TrendsPanel.svelte';
  import { isNumber, stationTime } from '$lib/format.js';
  import { trendsModel } from '$lib/observation/trends.js';
  import { SITE_URL } from '$lib/seo/i18n.js';
  import { appTabs, observationTabs, stationStripe } from '$lib/tabs.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { unitPreferences } from '$lib/units.svelte.js';

  let { data } = $props();
  const { station, meta, lang, slug, series, range } = $derived(data);

  const charts = $derived(
    trendsModel(series, station, lang, {
      span: range === 'today' ? 'day' : 'days',
      preferences: unitPreferences
    })
  );

  const lastEpoch = $derived(series?.epochs?.[series.epochs.length - 1]);
  const measuredAt = $derived(
    isNumber(lastEpoch) ? stationTime(lastEpoch, { language: lang, timeZone: station.tz }) : ''
  );

  const ranges = $derived({
    days: data.daysBack,
    synoptic: `/${lang}/trends/${slug}?rango=sinoptica`,
    today: `/${lang}/trends/${slug}`
  });

  // Se recuerda con qué estación se está trabajando, para que la barra
  // siga apuntando a ella al pasar por el mapa o el ranking.
  // `$effect`, no `onMount`: al saltar de una estación a otra el componente
  // se reutiliza y el montaje no vuelve a ocurrir; lo que cambia son los datos.
  $effect(() => {
    rememberConnection({ slug, name: meta.name, provider: meta.provider });
  });

  onMount(() => recordSection('trends'));
</script>

<svelte:head>
  <title>{ui(lang, 'trends_title')} · {meta.name} | MeteoLabX</title>
  <meta name="description" content={meta.description} />
  <!-- La ficha indexable de cada estación es la de observación. Tendencias es
       la misma estación vista de otra forma: se sigue enlazando, pero no se
       indexa, para no multiplicar por seis las URLs que compiten entre sí. -->
  <meta name="robots" content="noindex, follow" />
  <link rel="canonical" href={`${SITE_URL}/${lang}/trends/${slug}`} />
</svelte:head>

<AppShell
  language={lang}
  tabs={appTabs({ language: lang, slug })}
  subtabs={observationTabs({ language: lang, slug })}
  active="trends"
  station={stationStripe(station, meta)}
  alternates={meta.alternates}
  disconnectHref="/"
  onDisconnect={forgetConnection}
  {measuredAt}
  live={Boolean(measuredAt)}
>
  <TrendsPanel
    {charts}
    language={lang}
    {range}
    {ranges}
    timeZone={station.tz || 'UTC'}
    stationName={meta.name}
  />
  <SiteFooter language={lang} />
</AppShell>

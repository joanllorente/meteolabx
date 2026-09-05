<script>
  import { forgetConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { onMount } from 'svelte';

  import AppShell from '$lib/components/AppShell.svelte';
  import ObservationPanel from '$lib/components/ObservationPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { ui } from '$lib/i18n/ui.js';
  import { observationModel } from '$lib/observation/model.js';
  import { unavailableKey } from '$lib/observation/unavailable.js';
  import { recordConnectionError, recordSeoView, recordVisit } from '$lib/stats.js';
  import { startLiveObservation } from '$lib/live.svelte.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { appTabs, observationTabs, stationStripe } from '$lib/tabs.js';

  let { data } = $props();

  const { station, meta, lang, slug } = $derived(data);
  /**
   * Última observación conocida.
   *
   * Nace de lo que trajo el servidor y se va sustituyendo con los refrescos:
   * cambian las tarjetas y la hora de la cinta, no la página entera.
   */
  let live = $state(null);
  const observation = $derived(live || data.observation);
  const model = $derived(observationModel(observation, station, lang, unitPreferences));

  $effect(() =>
    startLiveObservation(
      {
        provider: station.provider,
        stationId: station.station_id,
        elevation: station.elevation ?? null
      },
      (payload) => (live = payload)
    )
  );
  const tabs = $derived(appTabs({ language: lang, slug }));
  const stripe = $derived(stationStripe(station, meta));

  onMount(() => {
    const estacion = { provider: station.provider, stationId: station.station_id, name: meta.name };
    // El contador de visitas SEO que ya alimentaban las páginas estáticas.
    recordSeoView({ ...estacion, language: lang });
    // Y la conexión en sí, que hasta ahora solo contaba la aplicación
    // anterior: sin esto el panel interno se queda sin la mitad de la foto.
    recordVisit(estacion);
    if (!model.available) {
      recordConnectionError({
        ...estacion,
        kind: unavailableKey(observation?.unavailable),
        status: observation?.unavailable?.status ?? null
      });
    }
  });

  // Se recuerda con qué estación se está trabajando, para que la barra
  // siga apuntando a ella al pasar por el mapa o el ranking.
  // `$effect`, no `onMount`: al saltar de una estación a otra el componente
  // se reutiliza y el montaje no vuelve a ocurrir; lo que cambia son los datos.
  $effect(() => {
    rememberConnection({ slug, name: meta.name, provider: meta.provider });
  });
</script>

<svelte:head>
  <title>{meta.title}</title>
  <meta name="description" content={meta.description} />
  <meta
    name="robots"
    content={station.indexable ? 'index, follow, max-image-preview:large' : 'noindex, follow'}
  />
  <link rel="canonical" href={meta.canonical} />
  {#each meta.alternates as entry (entry.code)}
    <link rel="alternate" hreflang={entry.code} href={entry.url} />
  {/each}
  <link rel="alternate" hreflang="x-default" href={meta.xDefault} />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="MeteoLabX" />
  <meta property="og:locale" content={meta.ogLocale} />
  {#each meta.ogLocaleAlternates as other (other)}
    <meta property="og:locale:alternate" content={other} />
  {/each}
  <meta property="og:url" content={meta.canonical} />
  <meta property="og:title" content={meta.title} />
  <meta property="og:description" content={meta.description} />
  <meta property="og:image" content={meta.ogImage} />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content={meta.title} />
  <meta name="twitter:description" content={meta.description} />
  <meta name="twitter:image" content={meta.ogImage} />
  {#each meta.structuredData as item, index (index)}
    {@html `<script type="application/ld+json">${JSON.stringify(item).replace(/</g, '\\u003c')}</script>`}
  {/each}
</svelte:head>

<AppShell
  language={lang}
  {tabs}
  subtabs={observationTabs({ language: lang, slug })}
  active="observation"
  station={stripe}
  alternates={meta.alternates}
  measuredAt={model.measuredAt}
  timeZone={model.timeZone}
  timestamp={model.timestamp}
  live={model.available}
  disconnectHref="/"
  onDisconnect={forgetConnection}
>
  {#if !model.available}
    <!-- El motivo importa: un 401 de la red no es una estación callada, y
         decirlo igual manda a buscar el fallo donde no está. -->
    <p class="offline">{ui(lang, unavailableKey(observation?.unavailable))}</p>
  {/if}

  <ObservationPanel {model} language={lang} stationName={meta.name} />

  <SiteFooter language={lang} />
</AppShell>

<style>
  .offline {
    margin: 0 0 20px; padding: 12px 15px;
    border: 1px solid var(--border); border-radius: var(--r-sm);
    background: var(--chip-warn-bg); color: var(--chip-warn-fg);
    font-size: 0.84rem; font-weight: 600;
  }

</style>

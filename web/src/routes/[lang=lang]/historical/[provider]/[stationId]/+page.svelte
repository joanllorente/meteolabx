<script>
  /**
   * Histórico de una estación sin ficha indexable.
   *
   * Igual que la vista de slug, con una diferencia: las estaciones propias se
   * consultan desde aquí, porque su credencial vive en este navegador. La
   * selección sigue viajando en la URL —el formulario navega—, así que una
   * consulta concreta se puede enlazar y recargar sin repetirla a ciegas.
   */
  import { onMount } from 'svelte';

  import AppShell from '$lib/components/AppShell.svelte';
  import HistoricalPanel from '$lib/components/HistoricalPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { forgetConnection, currentConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { credentialsFor, loadCredentials } from '$lib/credentials.svelte.js';
  import { isNumber } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { fetchPersonalClimoSummary } from '$lib/personal.js';
  import { displayName, providerLabel } from '$lib/seo/i18n.js';
  import { appTabs, observationTabs } from '$lib/tabs.js';

  // `localStorage` no existe en el servidor: las credenciales se leen al
  // montar. Sin esto, entrar directo por esta URL —un enlace, un favorito—
  // decía que faltaba la credencial teniéndola guardada.
  onMount(() => recordSection('historical'));

  onMount(loadCredentials);

  let { data } = $props();
  const lang = $derived(data.lang);
  const observationHref = $derived(
    `/${lang}/observation/${encodeURIComponent(data.provider)}/${encodeURIComponent(data.stationId)}`
  );

  /** Consulta hecha desde el navegador, para las redes con credencial propia. */
  let personal = $state({ summary: null, failure: '', loading: false });

  $effect(() => {
    if (!data.personal) return;
    // Sin botón pulsado no se consulta nada, igual que en el servidor.
    if (!data.requested || !data.supported || data.warning) {
      personal = { summary: null, failure: '', loading: false };
      return;
    }

    const credentials = credentialsFor(data.provider);
    if (!credentials) {
      personal = { summary: null, failure: 'missing', loading: false };
      return;
    }

    // Nada de leer `personal` aquí dentro: se re-dispararía sin fin.
    personal = { summary: null, failure: '', loading: true };

    const { provider, stationId, mode, selection, blocks } = data;
    let cancelled = false;
    fetchPersonalClimoSummary({
      provider,
      stationId,
      apiKey: credentials.apiKey,
      apiSecret: credentials.apiSecret || '',
      language: lang,
      summaryMode: mode,
      selectedMonths: mode === 'monthly' ? selection.months : [],
      selectedYears: selection.years,
      blocks
    })
      .then((summary) => {
        if (cancelled) return;
        personal = { summary, failure: '', loading: false };
      })
      .catch((cause) => {
        if (cancelled) return;
        // Un plazo agotado se cuenta como tal: la consulta era demasiado
        // grande, no el periodo demasiado vacío.
        const failure =
          cause?.name === 'AbortError' || cause?.name === 'TimeoutError'
            ? 'timeout'
            : String(cause?.message || 'error');
        personal = { summary: null, failure, loading: false };
      });

    return () => {
      cancelled = true;
    };
  });

  const summary = $derived(data.personal ? personal.summary : data.summary);
  const raw = $derived(data.personal ? personal.failure : data.failure);

  /**
   * Un problema de credencial no es un fallo del proveedor.
   *
   * El panel escribe «WU: <lo que sea>», que para una clave ausente o
   * rechazada no dice qué hacer. Esos dos casos se avisan aparte, con el
   * texto que sí lo dice, y no llegan al panel.
   */
  const credentialIssue = $derived(
    raw === 'missing' ? 'credentials_missing' : raw.includes('unauthorized') ? 'credentials_rejected' : ''
  );
  const failure = $derived(credentialIssue ? '' : raw);
  const warning = $derived(
    data.warning || (summary && !summary.has_data && !failure ? 'no_data_selected_period' : '')
  );

  /**
   * El nombre con el que se conectó esta estación.
   *
   * Se lee UNA vez, al montar. Leerlo con `currentConnection()` dentro de un
   * derivado lo enlazaba con el efecto que más abajo llama a
   * `rememberConnection()`: el efecto escribía el estado que el derivado leía,
   * volvía a dispararse, y Svelte abortaba el render con «maximum update
   * depth». La página quedaba congelada en su último estado —el de carga—,
   * que es justo lo que se veía aquí: el aviso de «consultando» clavado y
   * ningún gráfico, aunque la respuesta del proveedor hubiera llegado bien.
   */
  let remembered = $state('');
  onMount(() => {
    const connection = currentConnection();
    if (connection?.path === observationHref) remembered = connection.name;
  });

  const station = $derived(
    data.station || {
      provider: data.provider,
      station_id: data.stationId,
      name: remembered || data.stationId,
      elevation: credentialsFor(data.provider)?.elevation ?? null,
      lat: null,
      lon: null
    }
  );
  const name = $derived(data.station ? displayName(station.name) : station.name);

  const stripe = $derived({
    provider: providerLabel(station.provider),
    name,
    place: [station.locality, station.region].filter(Boolean).join(', '),
    id: station.station_id,
    altitude: isNumber(station.elevation) ? `${Math.round(station.elevation)} m` : '',
    lat: station.lat?.toFixed(4),
    lon: station.lon?.toFixed(4)
  });

  // Se recuerda con qué estación se está trabajando, para que la barra siga
  // apuntando a ella al pasar por el mapa o el ranking.
  $effect(() =>
    rememberConnection({ path: observationHref, name, provider: station.provider })
  );
</script>

<svelte:head>
  <title>{ui(lang, 'historical_title')} · {name} | MeteoLabX</title>
  <!-- Sin slug no hay ficha canónica que indexar. -->
  <meta name="robots" content="noindex, follow" />
</svelte:head>

<AppShell
  language={lang}
  tabs={appTabs({ language: lang, observationPath: observationHref })}
  subtabs={observationTabs({ language: lang, provider: data.provider, stationId: data.stationId })}
  active="historical"
  station={stripe}
  alternates={[]}
  disconnectHref="/"
  onDisconnect={forgetConnection}
>
  {#if credentialIssue}
    <p class="notice">{ui(lang, credentialIssue)}</p>
  {/if}

  <HistoricalPanel
    {summary}
    language={lang}
    mode={data.mode}
    unsupported={!data.supported}
    stationName={name}
    selection={data.selection}
    requested={data.requested}
    {warning}
    period={data.period}
    maxBlocks={data.maxBlocks}
    {failure}
    provider={station.provider}
    busy={personal.loading}
  />
  <SiteFooter language={lang} />
</AppShell>

<style>
  .notice {
    margin: 12px 0 0;
    padding: 10px 14px;
    border: 1px solid var(--border-2);
    border-radius: var(--r-sm);
    background: var(--panel-2);
    color: var(--muted);
    font-size: 0.82rem;
  }
</style>

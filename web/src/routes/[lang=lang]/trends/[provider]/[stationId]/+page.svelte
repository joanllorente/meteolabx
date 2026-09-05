<script>
  /**
   * Tendencias de una estación sin ficha indexable.
   *
   * Misma vista que la de slug; lo que cambia es de dónde salen los datos.
   * Las estaciones propias se piden desde aquí con la credencial guardada en
   * este navegador —el servidor no la tiene—, y las demás llegan ya resueltas
   * desde el servidor.
   */
  import { onMount } from 'svelte';

  import AppShell from '$lib/components/AppShell.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import TrendsPanel from '$lib/components/TrendsPanel.svelte';
  import { forgetConnection, currentConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { calibrationPayload, loadCalibrations } from '$lib/calibration.svelte.js';
  import { credentialsFor, loadCredentials } from '$lib/credentials.svelte.js';
  import { isNumber, stationTime } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { fetchPersonalRecentSeries, fetchPersonalTodaySeries } from '$lib/personal.js';
  import { displayName, providerLabel } from '$lib/seo/i18n.js';
  import { trendsModel } from '$lib/observation/trends.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { appTabs, observationTabs } from '$lib/tabs.js';
  import { browserTimeZone } from '$lib/timezone.js';

  // `localStorage` no existe en el servidor: las credenciales se leen al
  // montar. Sin esto, entrar directo por esta URL —un enlace, un favorito—
  // decía que faltaba la credencial teniéndola guardada.
  onMount(() => recordSection('trends'));

  onMount(() => {
    loadCredentials();
    loadCalibrations();
  });

  let { data } = $props();
  const lang = $derived(data.lang);
  const base = $derived(
    `/${lang}/trends/${encodeURIComponent(data.provider)}/${encodeURIComponent(data.stationId)}`
  );
  const observationHref = $derived(
    `/${lang}/observation/${encodeURIComponent(data.provider)}/${encodeURIComponent(data.stationId)}`
  );

  /** Serie pedida desde el navegador, para las redes con credencial propia. */
  let personal = $state({ series: null, error: '', loading: false });

  $effect(() => {
    if (!data.personal) return;
    const { provider, stationId, range, daysBack } = data;
    const credentials = credentialsFor(provider);

    if (!credentials) {
      personal = { series: null, error: 'missing', loading: false };
      return;
    }

    // Ni una lectura de `personal` aquí dentro: el efecto se re-dispararía
    // con cada escritura suya y pediría a la red sin parar.
    personal = { series: null, error: '', loading: true };

    const query = {
      provider,
      stationId,
      apiKey: credentials.apiKey,
      apiSecret: credentials.apiSecret || '',
      elevation: Number.isFinite(credentials.elevation) ? credentials.elevation : null,
      calibration: calibrationPayload(stationId)
    };

    let cancelled = false;
    (range === 'today'
      ? fetchPersonalTodaySeries(query)
      : fetchPersonalRecentSeries({ ...query, daysBack })
    )
      .then((series) => {
        if (cancelled) return;
        personal = { series, error: '', loading: false };
      })
      .catch((cause) => {
        if (cancelled) return;
        personal = { series: null, error: String(cause?.message || 'failed'), loading: false };
      });

    return () => {
      cancelled = true;
    };
  });

  const series = $derived(data.personal ? personal.series : data.series);

  /**
   * Lo que impide enseñar las tendencias, si es que hay algo.
   *
   * Una credencial ausente, una rechazada y una consulta en marcha no son
   * «esta estación no publica»: en los tres casos aún no se ha llegado a
   * preguntar.
   */
  const issue = $derived(
    !data.personal
      ? ''
      : personal.loading
        ? 'loading_station'
        : personal.error === 'missing'
          ? 'credentials_missing'
          : personal.error.includes('unauthorized')
            ? 'credentials_rejected'
            : personal.error
              ? 'personal_failed'
              : ''
  );

  const elevation = $derived(
    data.station?.elevation ?? credentialsFor(data.provider)?.elevation ?? null
  );

  /**
   * La estación, con lo poco que se sabe de ella.
   *
   * Una estación propia no está en ningún catálogo: su nombre es el que se le
   * puso al conectarla y su huso el de quien mira, que es quien la tiene en
   * casa. Sin ese huso las lecturas del día se colocaban en hora UTC.
   */
  const station = $derived(
    data.station || {
      provider: data.provider,
      station_id: data.stationId,
      name: remembered || data.stationId,
      lat: null,
      lon: null,
      elevation,
      tz: browserTimeZone()
    }
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

  const name = $derived(data.station ? displayName(station.name) : station.name);

  const charts = $derived(
    trendsModel(series, station, lang, {
      span: data.range === 'today' ? 'day' : 'days',
      preferences: unitPreferences
    })
  );

  const lastEpoch = $derived(series?.epochs?.[series.epochs.length - 1]);
  const measuredAt = $derived(
    isNumber(lastEpoch) ? stationTime(lastEpoch, { language: lang, timeZone: station.tz }) : ''
  );

  const ranges = $derived({
    days: data.daysBack,
    synoptic: `${base}?rango=sinoptica`,
    today: base
  });

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
  <title>{ui(lang, 'trends_title')} · {name} | MeteoLabX</title>
  <!-- Sin slug no hay ficha canónica que indexar. -->
  <meta name="robots" content="noindex, follow" />
</svelte:head>

<AppShell
  language={lang}
  tabs={appTabs({ language: lang, observationPath: observationHref })}
  subtabs={observationTabs({ language: lang, provider: data.provider, stationId: data.stationId })}
  active="trends"
  station={stripe}
  alternates={[]}
  disconnectHref="/"
  onDisconnect={forgetConnection}
  {measuredAt}
  live={Boolean(measuredAt)}
>
  <!-- Mientras no se ha podido preguntar, el panel no se pinta: con las
       series vacías diría «la estación no publica serie suficiente», que
       culpa a una estación a la que todavía no se le ha preguntado. -->
  {#if issue}
    <p class="notice">{ui(lang, issue)}</p>
  {:else}
    <TrendsPanel
      {charts}
      language={lang}
      range={data.range}
      {ranges}
      timeZone={station.tz || 'UTC'}
      stationName={name}
    />
  {/if}
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

<script>
  import { forgetConnection, rememberConnection } from '$lib/connection.svelte.js';
  import { onMount } from 'svelte';

  onMount(() => recordSection('observation'));

  onMount(() => {
    loadCredentials();
    loadCalibrations();
  });
  /**
   * El mismo panel de observación, para estaciones sin URL indexable.
   *
   * Sin metadatos de posicionamiento —no hay ficha que indexar— pero con
   * exactamente los mismos datos: estas redes publican igual que las demás.
   */
  import AppShell from '$lib/components/AppShell.svelte';
  import ObservationPanel from '$lib/components/ObservationPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { calibrationPayload, loadCalibrations } from '$lib/calibration.svelte.js';
  import { credentialsFor, loadCredentials } from '$lib/credentials.svelte.js';
  import { fetchPersonalObservation } from '$lib/personal.js';
  import { startLiveObservation } from '$lib/live.svelte.js';
  import { ui } from '$lib/i18n/ui.js';
  import { recordSection } from '$lib/stats.js';
  import { observationModel } from '$lib/observation/model.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { unavailableKey } from '$lib/observation/unavailable.js';
  import { displayName, providerLabel } from '$lib/seo/i18n.js';
  import { appTabs, observationTabs } from '$lib/tabs.js';

  let { data } = $props();
  const lang = $derived(data.lang);

  /**
   * Weather Underground y WeatherLink se piden desde aquí.
   *
   * Su credencial vive en este navegador, así que el servidor no puede pedir
   * el dato: manda la página vacía y la rellenamos. La respuesta trae también
   * los metadatos de la estación, así que no hace falta el catálogo —que
   * además no tiene las estaciones privadas de nadie.
   */
  let personal = $state({ observation: null, station: null, error: '', loading: false });

  /** Huso de este navegador, o UTC si no se puede saber. */
  function browserTimeZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
      return 'UTC';
    }
  }

  $effect(() => {
    if (!data.personal) return;
    const provider = data.provider;
    const stationId = data.stationId;
    const credentials = credentialsFor(provider);

    if (!credentials) {
      personal = { observation: null, station: null, error: 'missing', loading: false };
      return;
    }

    // Nada de leer `personal` aquí dentro: el efecto se re-dispararía con cada
    // escritura suya y la página se quedaría pidiendo a la red en bucle.
    personal = { observation: null, station: null, error: '', loading: true };

    let cancelled = false;
    // La calibración es de la estación, no de la credencial: se guarda por
    // identificador y viaja con cada consulta.
    const calibration = calibrationPayload(stationId);

    fetchPersonalObservation({
      provider,
      stationId,
      apiKey: credentials.apiKey,
      apiSecret: credentials.apiSecret || '',
      elevation: Number.isFinite(credentials.elevation) ? credentials.elevation : null,
      calibration
    })
      .then((payload) => {
        if (cancelled) return;
        personal = {
          observation: payload,
          station: payload?.station || null,
          error: '',
          loading: false
        };
      })
      .catch((cause) => {
        if (cancelled) return;
        // El motivo importa: una clave rechazada y una estación que no publica
        // se arreglan de formas muy distintas.
        personal = {
          observation: null,
          station: null,
          error: String(cause?.message || 'failed'),
          loading: false
        };
      });

    // Y a partir de aquí se refresca sola, con la misma credencial.
    const stop = startLiveObservation(
      {
        provider,
        stationId,
        apiKey: credentials.apiKey,
        apiSecret: credentials.apiSecret || '',
        elevation: Number.isFinite(credentials.elevation) ? credentials.elevation : null,
        calibration
      },
      (payload) => {
        personal = { observation: payload, station: payload?.station || null, error: '', loading: false };
      }
    );

    // Al cambiar de estación, la respuesta anterior ya no vale.
    return () => {
      cancelled = true;
      stop();
    };
  });

  const station = $derived(
    data.personal
      ? {
          provider: data.provider,
          station_id: data.stationId,
          name: personal.station?.name || data.stationId,
          lat: personal.station?.lat ?? null,
          lon: personal.station?.lon ?? null,
          elevation: personal.station?.elevation ?? credentialsFor(data.provider)?.elevation ?? null,
          // Las estaciones propias no están en el catálogo, así que el
          // backend no sabe su huso. Sin él, las gráficas del día colocaban
          // cada lectura en hora UTC: dos horas antes de la real en verano.
          // La zona de quien mira es la mejor apuesta para su propia estación.
          tz: personal.station?.tz || browserTimeZone(),
          locality: personal.station?.locality || '',
          region: personal.station?.region || ''
        }
      : data.station
  );
  // Lo último que ha llegado por refresco, para las redes públicas.
  let live = $state(null);
  const observation = $derived(
    data.personal ? personal.observation : live || data.observation
  );

  $effect(() => {
    // Las redes con credencial ya tienen su propio ciclo más abajo.
    if (data.personal || !data.station) return;
    return startLiveObservation(
      {
        provider: data.station.provider,
        stationId: data.station.station_id,
        elevation: data.station.elevation ?? null
      },
      (payload) => (live = payload)
    );
  });
  const model = $derived(observationModel(observation, station, lang, unitPreferences));

  // El identificador se enseña tal cual: «ILHOSP26» no es un nombre propio
  // que haya que capitalizar a «Ilhosp26».
  const name = $derived(
    personal.station?.name || data.station?.name
      ? displayName(station.name)
      : station.station_id
  );

  const stripe = $derived({
    provider: providerLabel(station.provider),
    name,
    place: [station.locality, station.region].filter(Boolean).join(', '),
    id: station.station_id,
    altitude:
      station.elevation === null || station.elevation === undefined
        ? ''
        : `${Math.round(station.elevation)} m`,
    lat: station.lat?.toFixed(4),
    lon: station.lon?.toFixed(4)
  });

  // Redes sin ficha indexable: se recuerda la ruta, que es lo que las
  // identifica.
  // `$effect`, no `onMount`: al saltar de una estación a otra el componente
  // se reutiliza y el montaje no vuelve a ocurrir; lo que cambia son los datos.
  $effect(() =>
    rememberConnection({
      path: `/${lang}/observation/${encodeURIComponent(station.provider)}/${encodeURIComponent(station.station_id)}`,
      name,
      provider: station.provider
    })
  );
</script>

<svelte:head>
  <title>{name} ({providerLabel(station.provider)}) | MeteoLabX</title>
  <!-- Sin slug no hay ficha canónica que indexar: estas redes repiten nombres
       a millares y no son únicas a nivel mundial. -->
  <meta name="robots" content="noindex, follow" />
</svelte:head>

<AppShell
  language={lang}
  tabs={appTabs({
    language: lang,
    observationPath: `/${lang}/observation/${encodeURIComponent(data.provider)}/${encodeURIComponent(data.stationId)}`
  })}
  subtabs={observationTabs({ language: lang, provider: data.provider, stationId: data.stationId })}
  active="observation"
  station={stripe}
  alternates={[]}
  measuredAt={model.measuredAt}
  timeZone={model.timeZone}
  timestamp={model.timestamp}
  live={model.available}
  disconnectHref="/"
  onDisconnect={forgetConnection}
>
  {#if data.personal && personal.loading}
    <!-- Mientras se consulta no se sabe si hay datos: decir que no los hay es
         mentir a medias, y es lo que se veía al conectar una estación propia. -->
    <p class="offline">{ui(lang, 'loading_station')}</p>
  {:else if !model.available && !(data.personal && personal.error)}
    <!-- Igual que en la ficha por slug: si la red rechazó la petición, se
         dice, en vez de culpar a una estación que sí está publicando. -->
    <p class="offline">{ui(lang, unavailableKey(observation?.unavailable))}</p>
  {/if}

  {#if data.personal && personal.error}
    <p class="warn">
      {#if personal.error === 'missing'}
        {ui(lang, 'credentials_missing')}
      {:else if personal.error.includes('unauthorized')}
        {ui(lang, 'credentials_rejected')}
      {:else if personal.error.includes('no_current_data')}
        {ui(lang, 'data_unavailable')}
      {:else}
        {ui(lang, 'personal_failed')}
      {/if}
    </p>
  {/if}

  <ObservationPanel {model} language={lang} stationName={name} />
  <SiteFooter language={lang} />
</AppShell>

<style>
  .warn {
    margin: 0 0 14px; padding: 10px 14px;
    border: 1px solid var(--chip-warn-bg); border-radius: var(--r-sm);
    background: var(--chip-warn-bg); color: var(--chip-warn-fg);
    font-size: 0.82rem;
  }

  .offline {
    margin: 0 0 20px; padding: 12px 15px;
    border: 1px solid var(--border); border-radius: var(--r-sm);
    background: var(--chip-warn-bg); color: var(--chip-warn-fg);
    font-size: 0.84rem; font-weight: 600;
  }
</style>

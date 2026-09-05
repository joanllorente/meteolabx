<script>
  /**
   * Conectar la estación propia: Weather Underground y WeatherLink.
   *
   * Va en la barra y no en la caja de búsqueda porque esa caja solo existe en
   * la portada: en cuanto te conectas a algo, desaparece. Y esto hay que poder
   * hacerlo desde cualquier pantalla.
   *
   * Las credenciales se quedan en este navegador. Salen de él solo para pedir
   * el dato, que es la llamada que el backend hace al proveedor.
   */
  import { onMount } from 'svelte';

  import app from '$lib/i18n/app-i18n.generated.js';
  import { closeOnOutside } from '$lib/close-on-outside.js';
  import {
    CALIBRATION_ORDER,
    CALIBRATION_SPECS,
    calibrationFor,
    loadCalibrations,
    normalizeCalibration,
    saveCalibration
  } from '$lib/calibration.svelte.js';
  import {
    credentialsFor,
    forgetCredentials,
    loadCredentials,
    isRemembered,
    saveCredentials
  } from '$lib/credentials.svelte.js';
  import {
    autoconnectSlug,
    loadFavourites,
    setAutoconnect,
    upsertFavourite
  } from '$lib/favourites.svelte.js';
  import { ui } from '$lib/i18n/ui.js';
  import { fetchMyWeatherLinkStations } from '$lib/personal.js';

  let { language } = $props();

  let panel;
  let network = $state('WU');

  // Guardar una clave de terceros no debería pasar solo: se pregunta.
  let remember = $state(true);
  let autoconnect = $state(false);

  let elevation = $state('');
  let wuStation = $state('');
  let wuKey = $state('');

  /**
   * Calibración de la estación de WU que se está configurando.
   *
   * Las casillas guardan texto, no números: mientras se escribe «-0,» no hay
   * número que valga, y convertir a cada tecla borraría el signo o la coma
   * recién escritos. La conversión y el recorte al rango los hace el módulo
   * al guardar.
   */
  let calibration = $state({});
  let calibrationSaved = $state(false);
  let calibrationOpen = $state(false);

  let wlKey = $state('');
  let wlSecret = $state('');
  let wlStations = $state([]);
  let wlLoading = $state(false);
  let wlError = $state('');

  /**
   * La barra superior usa desenfoque de fondo; en CSS eso convierte a la
   * cabecera en referencia de cualquier `position: fixed` descendiente. El
   * diálogo se mueve al `body` para que se mida contra toda la ventana y no
   * quede recortado por la altura de la barra.
   */
  function portal(node) {
    document.body.appendChild(node);
    return { destroy: () => node.remove() };
  }

  onMount(() => {
    loadCredentials();
    loadFavourites();
    remember = isRemembered('WU') || isRemembered('WEATHERLINK');
    autoconnect = autoconnectSlug().startsWith('/');
    loadCalibrations();
    const wu = credentialsFor('WU');
    if (wu) {
      wuStation = wu.stationId || '';
      wuKey = wu.apiKey || '';
      if (Number.isFinite(wu.elevation)) elevation = String(wu.elevation);
      showCalibration(wu.stationId);
    }
    const wl = credentialsFor('WEATHERLINK');
    if (wl) {
      wlKey = wl.apiKey || '';
      wlSecret = wl.apiSecret || '';
    }
  });

  const saved = $derived({
    WU: Boolean(credentialsFor('WU')),
    WEATHERLINK: Boolean(credentialsFor('WEATHERLINK'))
  });

  // No basta con que haya alguna WU guardada: la calibración se habilita
  // únicamente cuando el ID visible es el de esa conexión. Al escribir otro
  // ID queda desactivada hasta conectarlo, evitando editar por accidente los
  // offsets de una estación distinta.
  const calibrationStationId = $derived.by(() => {
    const connected = credentialsFor('WU');
    const typed = wuStation.trim();
    if (!connected || !typed) return '';
    return typed.toUpperCase() === String(connected.stationId || '').trim().toUpperCase()
      ? typed
      : '';
  });

  /** Guarda la credencial y, si se recuerda, deja la estación en favoritos. */
  /** Altitud escrita, o `null` si no se ha puesto. */
  const height = $derived.by(() => {
    const value = Number(String(elevation).replace(',', '.'));
    return Number.isFinite(value) ? value : null;
  });

  function keep(provider, credentials, { name }) {
    saveCredentials(provider, { ...credentials, elevation: height }, { remember });
    const path = `/${language}/observation/${provider}/${encodeURIComponent(credentials.stationId)}`;
    if (remember) upsertFavourite({ path, name, provider });
    // Abrirse sola al entrar solo tiene sentido si la credencial se recuerda:
    // sin ella, la ficha no podría cargar mañana.
    if (autoconnect && remember) setAutoconnect(path);
    else if (!autoconnect && autoconnectSlug() === path) setAutoconnect('');
    return path;
  }

  function connectWu(event) {
    event.preventDefault();
    const stationId = wuStation.trim();
    if (!stationId || !wuKey.trim()) return;
    location.href = keep('WU', { stationId, apiKey: wuKey.trim() }, { name: stationId });
  }

  async function listWeatherLink(event) {
    event.preventDefault();
    if (!wlKey.trim() || !wlSecret.trim()) return;
    wlLoading = true;
    wlError = '';
    try {
      wlStations = await fetchMyWeatherLinkStations({
        apiKey: wlKey.trim(),
        apiSecret: wlSecret.trim()
      });
      if (!wlStations.length) wlError = 'empty';
    } catch {
      wlError = 'failed';
      wlStations = [];
    } finally {
      wlLoading = false;
    }
  }

  function connectWeatherLink(station) {
    const stationId = String(station.station_id);
    // Si la cuenta declara la altitud, esa manda sobre lo que se haya escrito.
    const own = Number(station.elevation ?? station.elevation_m);
    if (Number.isFinite(own)) elevation = String(Math.round(own));
    location.href = keep(
      'WEATHERLINK',
      { stationId, apiKey: wlKey.trim(), apiSecret: wlSecret.trim() },
      { name: station.station_name || stationId }
    );
  }

  const texts = $derived(app.calibration?.[language] || app.calibration?.es || {});
  const fields = $derived(texts.fields || {});

  /** Trae a las casillas lo guardado para esa estación. */
  function showCalibration(stationId) {
    const values = calibrationFor(stationId);
    calibration = Object.fromEntries(
      CALIBRATION_ORDER.map((key) => [key, values[key] ? String(values[key]) : ''])
    );
    calibrationSaved = false;
  }

  /** ¿Hay en las casillas algo distinto de lo guardado? */
  const calibrationDirty = $derived.by(() => {
    const saved = calibrationFor(wuStation.trim());
    return CALIBRATION_ORDER.some((key) => {
      const typed = normalizeCalibration(calibration)[key];
      return typed !== (saved[key] || 0);
    });
  });

  function rangeHelp(key) {
    const spec = CALIBRATION_SPECS[key];
    return String(texts.range_help || '')
      .replace('{min}', String(spec.min))
      .replace('{max}', String(spec.max))
      .replace('{unit}', spec.unit);
  }

  function storeCalibration() {
    const stationId = wuStation.trim();
    if (!stationId) return;
    // Se reescriben las casillas con lo que ha quedado guardado: así se ve al
    // momento que un 40 en el barómetro se ha recortado a 20, en vez de dejar
    // en pantalla un número que el backend nunca va a aplicar.
    saveCalibration(stationId, calibration);
    showCalibration(stationId);
    calibrationSaved = true;
  }

  function openCalibration() {
    if (!calibrationStationId) return;
    showCalibration(calibrationStationId);
    calibrationOpen = true;
  }

  function forget(provider) {
    forgetCredentials(provider);
    if (provider === 'WU') {
      wuStation = '';
      wuKey = '';
      showCalibration('');
    } else {
      wlKey = '';
      wlSecret = '';
      wlStations = [];
    }
  }
</script>

<details class="mine" bind:this={panel} use:closeOnOutside>
  <summary title={ui(language, 'my_station')}>
    <span aria-hidden="true">+</span>
    <span class="txt">{ui(language, 'my_station')}</span>
  </summary>

  <div class="panel">
    <div class="tabs" role="group">
      <button type="button" class:on={network === 'WU'} onclick={() => (network = 'WU')}>
        Weather Underground
      </button>
      <button type="button" class:on={network === 'WEATHERLINK'} onclick={() => (network = 'WEATHERLINK')}>
        WeatherLink
      </button>
    </div>

    {#if network === 'WU'}
      <form onsubmit={connectWu}>
        <label>
          <span>{ui(language, 'station_id')}</span>
          <input
            bind:value={wuStation}
            placeholder="IBARCE12345"
            autocomplete="off"
            spellcheck="false"
            onchange={() => showCalibration(wuStation.trim())}
          />
        </label>
        <label>
          <span>{ui(language, 'api_key')}</span>
          <input bind:value={wuKey} type="password" autocomplete="off" spellcheck="false" />
        </label>
        <label>
          <span>{ui(language, 'elevation_m')}</span>
          <input bind:value={elevation} inputmode="numeric" placeholder="120" autocomplete="off" />
        </label>
        <label class="remember">
          <input type="checkbox" bind:checked={remember} />
          <span>{ui(language, 'remember_credentials')}</span>
        </label>
        <label class="remember">
          <input type="checkbox" bind:checked={autoconnect} disabled={!remember} />
          <span>{ui(language, 'autoconnect')}</span>
        </label>
        <div class="row">
          <button class="go" type="submit">{ui(language, 'connect')}</button>
          {#if saved.WU}
            <button type="button" class="forget" onclick={() => forget('WU')}>
              {ui(language, 'forget_credentials')}
            </button>
          {/if}
        </div>

        <!-- La calibración es una acción de gestión de la estación. Se abre
             en una ventana propia para no comprimir siete ajustes dentro de
             este panel estrecho. Solo se habilita cuando hay una WU guardada. -->
        <button
          type="button"
          class="calibration-action"
          disabled={!calibrationStationId}
          onclick={openCalibration}
        >
          <span class="calibration-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6" />
            </svg>
          </span>
          <span class="calibration-copy">
            <strong>{texts.title}</strong>
            <small>{calibrationStationId ? texts.description : texts.requires_connection}</small>
          </span>
          <span class="calibration-chevron" aria-hidden="true">›</span>
        </button>
      </form>
    {:else}
      <form onsubmit={listWeatherLink}>
        <label>
          <span>{ui(language, 'api_key')}</span>
          <input bind:value={wlKey} type="password" autocomplete="off" spellcheck="false" />
        </label>
        <label>
          <span>{ui(language, 'api_secret')}</span>
          <input bind:value={wlSecret} type="password" autocomplete="off" spellcheck="false" />
        </label>
        <label>
          <span>{ui(language, 'elevation_m')}</span>
          <input bind:value={elevation} inputmode="numeric" placeholder="120" autocomplete="off" />
        </label>
        <label class="remember">
          <input type="checkbox" bind:checked={remember} />
          <span>{ui(language, 'remember_credentials')}</span>
        </label>
        <label class="remember">
          <input type="checkbox" bind:checked={autoconnect} disabled={!remember} />
          <span>{ui(language, 'autoconnect')}</span>
        </label>
        <div class="row">
          <button class="go" type="submit" disabled={wlLoading}>
            {wlLoading ? ui(language, 'searching') : ui(language, 'list_my_stations')}
          </button>
          {#if saved.WEATHERLINK}
            <button type="button" class="forget" onclick={() => forget('WEATHERLINK')}>
              {ui(language, 'forget_credentials')}
            </button>
          {/if}
        </div>
      </form>

      {#if wlError}
        <p class="warn">
          {ui(language, wlError === 'empty' ? 'no_stations_in_account' : 'credentials_rejected')}
        </p>
      {/if}

      {#if wlStations.length}
        <ul>
          {#each wlStations as station (station.station_id)}
            <li>
              <button type="button" onclick={() => connectWeatherLink(station)}>
                <strong>{station.station_name || station.station_id}</strong>
                <span>{station.station_id}</span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}

    <p class="note">{ui(language, 'credentials_note')}</p>
  </div>
</details>

{#if calibrationOpen}
  <div
    class="calibration-backdrop"
    role="presentation"
    use:portal
    onclick={(event) => event.target === event.currentTarget && (calibrationOpen = false)}
  >
    <div class="calibration-modal" role="dialog" aria-modal="true" aria-labelledby="calibration-title">
      <header>
        <div>
          <p class="calibration-kicker">Weather Underground · {calibrationStationId}</p>
          <h2 id="calibration-title">{texts.title}</h2>
          <p>{texts.description}</p>
        </div>
        <button
          type="button"
          class="calibration-close"
          aria-label={texts.close}
          title={texts.close}
          onclick={() => (calibrationOpen = false)}
        >×</button>
      </header>

      <div class="calib-fields">
        {#each CALIBRATION_ORDER as key (key)}
          <label class="calib-field">
            <span>{fields[key]}</span>
            <input
              bind:value={calibration[key]}
              inputmode="decimal"
              placeholder="0"
              autocomplete="off"
              title={rangeHelp(key)}
            />
            <small>{rangeHelp(key)}</small>
          </label>
        {/each}
      </div>

      <footer>
        <div>
          {#if calibrationDirty}
            <p class="calib-note">{texts.unsaved}</p>
          {:else if calibrationSaved}
            <p class="calib-note ok">{texts.saved}</p>
          {/if}
        </div>
        <button type="button" class="keep" onclick={storeCalibration} disabled={!wuStation.trim()}>
          {texts.save}
        </button>
      </footer>
    </div>
  </div>
{/if}

<style>
  .mine { position: relative; }
  summary {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 11px; list-style: none; cursor: pointer;
    border: 1px solid var(--border); border-radius: 9px;
    background: var(--card); color: var(--muted);
    font-size: 0.74rem; font-weight: 600;
  }
  summary::-webkit-details-marker { display: none; }
  summary:hover { color: var(--ink); border-color: var(--border-2); }

  .panel {
    position: absolute; z-index: 40; top: calc(100% + 7px); right: 0;
    width: min(340px, calc(100vw - 28px));
    padding: 12px; border: 1px solid var(--border-2); border-radius: 12px;
    background: var(--panel); box-shadow: var(--shadow);
  }

  .tabs { display: flex; gap: 3px; padding: 3px; margin-bottom: 10px; border-radius: 9px; background: var(--panel-2); }
  .tabs button {
    flex: 1; padding: 6px 8px; border: 0; border-radius: 7px;
    background: transparent; color: var(--muted); font: inherit; font-size: 0.68rem; font-weight: 650;
  }
  .tabs button.on { color: var(--ink); background: var(--card); }

  form { display: flex; flex-direction: column; gap: 8px; }
  label { display: flex; flex-direction: column; gap: 3px; }
  label span { font-size: 0.66rem; font-weight: 650; color: var(--muted); }
  input {
    padding: 7px 9px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel-2); color: var(--ink); font: inherit; font-size: 0.78rem;
  }
  input:focus { outline: none; border-color: var(--accent); }

  .remember { flex-direction: row; align-items: center; gap: 7px; cursor: pointer; }
  .remember span { font-size: 0.7rem; font-weight: 600; color: var(--ink-2); }
  .remember input { width: 14px; height: 14px; padding: 0; }

  .row { display: flex; gap: 6px; align-items: center; }

  .calibration-action {
    width: 100%; display: grid; grid-template-columns: auto 1fr auto;
    align-items: center; gap: 9px; padding: 10px;
    border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
    border-radius: 10px; background: color-mix(in srgb, var(--accent) 8%, var(--card));
    color: var(--ink); text-align: left; font: inherit;
  }
  .calibration-action:not(:disabled):hover {
    border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
    background: color-mix(in srgb, var(--accent) 13%, var(--card));
  }
  .calibration-action:disabled { cursor: not-allowed; opacity: 0.58; }
  .calibration-icon {
    width: 31px; height: 31px; display: grid; place-items: center;
    border-radius: 8px; background: color-mix(in srgb, var(--accent) 15%, var(--panel));
    color: var(--accent);
  }
  .calibration-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .calibration-copy strong { font-size: 0.72rem; line-height: 1.2; }
  .calibration-copy small { color: var(--muted); font-size: 0.61rem; line-height: 1.3; }
  .calibration-chevron { color: var(--accent); font-size: 1.3rem; line-height: 1; }

  .calibration-backdrop {
    position: fixed; z-index: 100; inset: 0; display: grid; place-items: center;
    padding: 20px; background: rgb(4 10 20 / 0.58); backdrop-filter: blur(3px);
  }
  .calibration-modal {
    width: min(620px, 100%); max-height: min(720px, calc(100vh - 40px)); overflow-y: auto;
    padding: 20px; border: 1px solid var(--border-2); border-radius: 16px;
    background: var(--panel); color: var(--ink); box-shadow: 0 24px 80px rgb(0 0 0 / 0.3);
  }
  .calibration-modal header {
    display: flex; justify-content: space-between; gap: 20px; align-items: flex-start;
    padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  .calibration-modal h2 { margin: 2px 0 4px; font-size: 1.15rem; }
  .calibration-modal header p { margin: 0; color: var(--muted); font-size: 0.72rem; line-height: 1.45; }
  .calibration-modal .calibration-kicker {
    color: var(--accent); font-size: 0.62rem; font-weight: 750; letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .calibration-close {
    flex: 0 0 auto; width: 32px; height: 32px; border: 1px solid var(--border);
    border-radius: 9px; background: var(--panel-2); color: var(--ink-2);
    font: inherit; font-size: 1.25rem; line-height: 1;
  }
  .calibration-close:hover { color: var(--ink); border-color: var(--border-2); }
  .calib-fields {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 18px 0;
  }
  .calib-field input { padding: 9px 10px; font-size: 0.78rem; }
  .calib-field span { font-size: 0.68rem; }
  .calib-field small { color: var(--muted-2); font-size: 0.58rem; line-height: 1.3; }
  .keep {
    padding: 8px 14px; border: 1px solid transparent; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 0.72rem; font-weight: 700;
  }
  .keep:disabled { opacity: 0.55; }
  .calib-note { margin: 0; font-size: 0.66rem; color: var(--muted); }
  .calib-note.ok { color: var(--accent); }
  .calibration-modal footer {
    min-height: 36px; display: flex; justify-content: space-between; gap: 16px;
    align-items: center; padding-top: 14px; border-top: 1px solid var(--border);
  }
  .go {
    padding: 7px 14px; border: 1px solid transparent; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 0.74rem; font-weight: 700;
  }
  .go:disabled { opacity: 0.65; }
  .forget {
    padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--card); color: var(--muted); font-size: 0.68rem; font-weight: 600;
  }
  .forget:hover { color: var(--ink); border-color: var(--border-2); }

  ul { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 2px; max-height: 220px; overflow-y: auto; }
  ul button {
    width: 100%; display: flex; flex-direction: column; gap: 1px;
    padding: 7px 9px; border: 0; border-radius: 8px; background: transparent; text-align: left;
  }
  ul button:hover { background: var(--card); }
  ul strong { font-size: 0.78rem; color: var(--ink); }
  ul span { font-size: 0.64rem; color: var(--muted); font-family: var(--mono); }

  .warn { margin-top: 8px; font-size: 0.72rem; color: var(--chip-warn-fg); }
  .note { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--border); font-size: 0.66rem; line-height: 1.45; color: var(--muted-2); }

  @media (max-width: 900px) {
    .txt { display: none; }
  }
  @media (max-width: 560px) {
    .calibration-backdrop { padding: 10px; }
    .calibration-modal { padding: 16px; border-radius: 13px; }
    .calib-fields { grid-template-columns: 1fr; gap: 9px; }
  }
</style>

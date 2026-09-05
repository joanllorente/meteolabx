<script>
  /**
   * Panel de filtros del mapa.
   *
   * Los mismos que la pestaña actual: países, sensores exigidos y los cuatro
   * interruptores de tipo de estación. Los textos no están escritos aquí,
   * salen de `locales/*.json` vía `scripts/export_app_i18n.py`, que es de
   * donde los lee Streamlit.
   *
   * Es un formulario GET dentro de un `<details>`: se abre y se envía sin
   * JavaScript, y cada combinación queda en la URL.
   */
  import { onMount } from 'svelte';

  import { Filter } from '@lucide/svelte';

  import app from '$lib/i18n/app-i18n.generated.js';
  import { num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { sensorLabel } from '$lib/seo/i18n.js';

  let { language, filters, sensorKeys, countries, countryName } = $props();

  const texts = $derived(app.map[language] || app.map.es);

  /**
   * Países disponibles, con los elegidos arriba.
   *
   * Son casi cien: sacar los seleccionados a la cabecera evita tener que
   * buscarlos por la lista cada vez que se abre el panel.
   */
  const options = $derived(
    Object.entries(countries || {})
      .filter(([code, count]) => code && code !== 'UN' && count > 0)
      .map(([code, count]) => ({
        code,
        count,
        name: countryName(code),
        chosen: filters.countries.includes(code)
      }))
      .sort((a, b) => {
        if (a.chosen !== b.chosen) return a.chosen ? -1 : 1;
        return a.name.localeCompare(b.name, language);
      })
  );

  let panel;
  let search = $state('');

  /**
   * Cerrar al pinchar fuera y con Escape.
   *
   * Un `<details>` abierto se queda abierto hasta que se vuelve a pulsar el
   * resumen, y aquí tapa media pantalla. Los dos gestos son lo que espera
   * cualquiera de un panel flotante.
   */
  onMount(() => {
    const closeOutside = (event) => {
      if (panel?.open && !panel.contains(event.target)) panel.open = false;
    };
    const closeOnEscape = (event) => {
      if (event.key === 'Escape' && panel?.open) panel.open = false;
    };
    // En la fase de captura: si el clic cae sobre el mapa, MapLibre lo
    // consume antes de que llegue al documento.
    document.addEventListener('pointerdown', closeOutside, true);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside, true);
      document.removeEventListener('keydown', closeOnEscape);
    };
  });
  const visible = $derived(
    search.trim()
      ? options.filter((option) =>
          option.name.toLowerCase().includes(search.trim().toLowerCase())
        )
      : options
  );

  /** Los sensores llegan en minúscula porque nacieron dentro de una frase. */
  const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);

  /** Los tres interruptores que llevan ayuda en la aplicación actual. */
  const toggles = $derived([
    {
      name: 'ocultar-archivadas',
      label: texts.hide_historical_only,
      help: texts.hide_historical_only_help,
      checked: filters.hideArchived
    },
    {
      name: 'ocultar-manuales',
      label: texts.hide_manual,
      help: texts.hide_manual_help,
      checked: filters.hideManual
    },
    {
      name: 'ocultar-particulares',
      label: texts.hide_pws,
      help: texts.hide_pws_help,
      checked: filters.hideAmateur
    }
  ]);

  const active = $derived(
    filters.sensors.length +
      [filters.onlyHistorical, filters.hideArchived, filters.hideManual, filters.hideAmateur]
        .filter(Boolean).length
  );

  const chosen = $derived(
    filters.countries.map((code) => countryName(code)).join(', ')
  );
</script>

<details class="filters" bind:this={panel}>
  <summary>
    <!-- «Filtros» a secas: el panel filtra por país, sensores, tipo de
         estación y disponibilidad de histórico, así que enumerar dos de las
         cuatro cosas engañaba. -->
    <Filter size={14} />
    <span>{ui(language, 'filters')}</span>
    {#if active}<b class="badge">{active}</b>{/if}
  </summary>

  <form method="GET" class="panel">
    <input type="hidden" name="capa" value="estaciones" />

    <section>
      <h3>{texts.country_filter}</h3>
      <input
        class="search"
        type="search"
        bind:value={search}
        placeholder={ui(language, 'search')}
        aria-label={texts.country_filter}
      />
      <div class="countries">
        {#each visible as option (option.code)}
          <label>
            <input type="checkbox" name="pais" value={option.code} checked={option.chosen} />
            <span>{option.name}</span>
            <small>{num(option.count, { language, decimals: 0 })}</small>
          </label>
        {/each}
      </div>
      <p class="hint">{chosen}</p>
    </section>

    <section>
      <h3>{ui(language, 'station_type_filters')}</h3>
      <label>
        <input type="checkbox" name="solo-historico" value="si" checked={filters.onlyHistorical} />
        <span>{texts.historical_only}</span>
      </label>
      {#each toggles as toggle (toggle.name)}
        <label class="toggle">
          <input type="checkbox" name={toggle.name} value="si" checked={toggle.checked} />
          <span>{toggle.label}</span>
          <!-- Tooltip propio, no `title`: el nativo tarda unos tres segundos
               en aparecer y para una ayuda que se consulta de pasada eso es
               como no tenerla. Sale al posarse y al enfocar con teclado. -->
          <span class="help" tabindex="0" role="note" aria-label={toggle.help}>?</span>
          <span class="bubble">{toggle.help}</span>
        </label>
      {/each}
    </section>

    <section>
      <h3>{texts.sensor_filter}</h3>
      <p class="hint">{texts.sensor_filter_caption}</p>
      <div class="sensors">
        {#each sensorKeys as key (key)}
          <label>
            <input type="checkbox" name="sensores" value={key} checked={filters.sensors.includes(key)} />
            <span>{capitalize(sensorLabel(language, key))}</span>
          </label>
        {/each}
      </div>
    </section>

    <div class="actions">
      <button class="apply" type="submit">{ui(language, 'apply_filters')}</button>
      <a class="clear" href="?capa=estaciones">{texts.sensor_filter_clear}</a>
    </div>
  </form>
</details>

<style>
  .filters { position: relative; }
  summary {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 7px 13px; border: 1px solid var(--border); border-radius: 9px;
    background: var(--card); color: var(--ink-2);
    font-size: 0.74rem; font-weight: 600; cursor: pointer; list-style: none;
  }
  summary::-webkit-details-marker { display: none; }
  summary:hover { color: var(--ink); border-color: var(--border-2); }
  .badge {
    min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px;
    background: var(--accent); color: var(--accent-ink);
    font: 800 10px/16px var(--font); text-align: center;
  }

  .panel {
    position: absolute; right: 0; z-index: 30; margin-top: 8px;
    width: min(340px, calc(100vw - 40px));
    max-height: 70vh; overflow-y: auto;
    padding: 16px 18px;
    border: 1px solid var(--border-2); border-radius: var(--r-md);
    background: var(--panel); box-shadow: var(--shadow);
  }
  section + section { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border); }
  h3 { font-size: 0.78rem; font-weight: 700; margin-bottom: 8px; }
  .hint { color: var(--muted); font-size: 0.7rem; line-height: 1.45; margin-bottom: 8px; }

  .search {
    width: 100%; padding: 7px 10px; margin-bottom: 8px;
    border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel-2); color: var(--ink); font: inherit; font-size: 0.76rem;
  }
  .countries {
    max-height: 190px; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 8px;
    padding: 4px 8px; background: var(--panel-2);
  }
  .countries label { justify-content: flex-start; gap: 8px; }
  .countries small { margin-left: auto; color: var(--muted-2); font-size: 0.68rem; font-variant-numeric: tabular-nums; }

  label { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 0.78rem; color: var(--ink-2); cursor: pointer; }
  label input { accent-color: var(--accent); }

  .help {
    display: grid; place-items: center;
    width: 15px; height: 15px; flex: none;
    border: 1px solid var(--border-2); border-radius: 999px;
    color: var(--muted); font-size: 0.62rem; font-weight: 700;
    cursor: help;
  }
  .help:hover, .help:focus-visible { color: var(--ink); border-color: var(--accent); outline: none; }

  /* El panel tiene scroll, así que recorta lo que se salga por los lados.
     La burbuja se ancla a la fila entera —no al interrogante— y ocupa el
     ancho disponible: así nunca se sale ni hay que adivinar hacia qué lado
     abrirla. */
  .toggle { position: relative; }
  .bubble {
    position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; z-index: 40;
    padding: 8px 10px;
    border: 1px solid var(--border-2); border-radius: 8px;
    background: var(--panel-2); color: var(--ink-2);
    font-size: 0.72rem; font-weight: 500; line-height: 1.45; text-align: left;
    box-shadow: var(--shadow);
    opacity: 0; visibility: hidden;
    transition: opacity 0.12s;
  }
  .help:hover ~ .bubble,
  .help:focus-visible ~ .bubble,
  .toggle:hover .bubble { opacity: 1; visibility: visible; }

  .sensors { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
  .apply { padding: 8px 15px; border: 0; border-radius: 8px; background: var(--accent); color: var(--accent-ink); font-size: 0.78rem; font-weight: 680; }
  .clear { font-size: 0.76rem; color: var(--muted); text-decoration: underline; }
  .clear:hover { color: var(--ink); }
</style>

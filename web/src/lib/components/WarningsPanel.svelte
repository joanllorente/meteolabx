<script>
  /**
   * Pestaña de avisos: filtros, mapa, lista por país y detalle de la zona.
   *
   * Los filtros viven en el propio componente, no en la URL: mientras la
   * pestaña esté en construcción no hay nada que enlazar ni que indexar.
   */
  import { CloudLightning, CloudRain, Layers, Thermometer, Wind } from '@lucide/svelte';

  import WarningsMap from './WarningsMap.svelte';
  import { locale, num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import { forecastHref } from '$lib/tabs.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { convertUnit, unitLabel } from '$lib/units.js';
  import zones from '$lib/warnings/demo-zones.json';
  import {
    HAZARD_UNIT,
    HAZARDS,
    LEVEL_COLORS,
    activeSpan,
    countryTimeZone,
    filterWarnings,
    groupByCountry,
    warningKey,
    zoneCountry,
    zoneLevels
  } from '$lib/warnings/warnings.js';

  let { data, language = 'es' } = $props();

  const ICONS = { all: Layers, temperature: Thermometer, wind: Wind, rain: CloudRain, storms: CloudLightning };
  const NAMES = Object.fromEntries(zones.features.map((feature) => [feature.properties.zone, feature.properties.name]));

  // Día elegido; mientras no se elija ninguno, o si deja de existir, el primero.
  let chosenDay = $state('');
  const day = $derived(
    data.days.some((item) => item.date === chosenDay) ? chosenDay : data.days[0]?.date || ''
  );
  let hazard = $state('all');
  let country = $state('all');
  let query = $state('');
  let selectedKey = $state('');

  const visible = $derived(filterWarnings(data.warnings, { day, hazard, country, query, names: NAMES }));
  const levels = $derived(zoneLevels(visible));
  const groups = $derived(groupByCountry(visible, NAMES));
  const selected = $derived(visible.find((warning) => warningKey(warning) === selectedKey) || null);
  const countries = $derived(
    [...new Set(data.warnings.map((warning) => zoneCountry(warning.zone)))]
      .map((code) => ({ code, name: countryName(code) }))
      .sort((a, b) => a.name.localeCompare(b.name, locale(language)))
  );

  function countryName(code) {
    try {
      return new Intl.DisplayNames([locale(language)], { type: 'region' }).of(code) || code;
    } catch {
      return code;
    }
  }

  function dayLabel(date, index) {
    const when = new Date(`${date}T12:00:00`);
    const short = new Intl.DateTimeFormat(locale(language), { weekday: 'short', day: 'numeric' }).format(when);
    if (index === 0) return `${ui(language, 'warnings_today')} · ${short}`;
    if (index === 1) return `${ui(language, 'warnings_tomorrow')} · ${short}`;
    return short;
  }

  const runLabel = $derived(
    new Intl.DateTimeFormat(locale(language), { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(
      new Date(data.run)
    ) + ' UTC'
  );

  /** Valor en las unidades elegidas por el usuario, con su etiqueta. */
  function measure(value, kind) {
    const family = HAZARD_UNIT[kind];
    if (!family) return `${num(value, { language, decimals: 0 })} J/kg`;
    const converted = convertUnit(value, family, unitPreferences);
    const decimals = family === 'precip' && unitPreferences.precip === 'in' ? 1 : 0;
    return `${num(converted, { language, decimals })} ${unitLabel(family, unitPreferences)}`;
  }

  function summary(warning) {
    return ui(language, `warn_peak_${warning.hazard}`, {
      value: measure(warning.peak, warning.hazard),
      period: warning.period === '12h' ? '12 h' : '24 h'
    });
  }

  function span(warning) {
    const range = activeSpan(warning.hourly);
    return range ? `${String(range[0]).padStart(2, '0')}–${String(range[1] + 1).padStart(2, '0')} h` : '';
  }

  function zoneLabel(warning) {
    try {
      return (
        new Intl.DateTimeFormat(locale(language), { timeZone: countryTimeZone(zoneCountry(warning.zone)), timeZoneName: 'short' })
          .formatToParts(new Date(`${warning.day}T12:00:00Z`))
          .find((part) => part.type === 'timeZoneName')?.value || ''
      );
    } catch {
      return '';
    }
  }

  function selectZone(zone) {
    const candidates = visible.filter((warning) => warning.zone === zone).sort((a, b) => b.level - a.level);
    if (candidates.length) selectedKey = warningKey(candidates[0]);
  }
</script>

<section class="warnings">
  <header class="head">
    <div>
      <h2>{ui(language, 'warnings_title')}</h2>
      <p>{ui(language, 'warnings_subtitle')}</p>
    </div>
    <div class="meta">
      {#if data.demo}<span class="demo">{ui(language, 'warnings_demo')}</span>{/if}
      <span>AROME · {ui(language, 'warnings_run', { run: runLabel })}</span>
    </div>
  </header>

  <div class="controls">
    <div class="seg" role="group">
      {#each data.days as item, index (item.date)}
        <button type="button" class:active={item.date === day} onclick={() => (chosenDay = item.date)}>
          {dayLabel(item.date, index)}
          {#if item.source !== 'arome'}<small>{item.source.toUpperCase()}</small>{/if}
        </button>
      {/each}
    </div>
    <div class="seg" role="group">
      {#each ['all', ...HAZARDS] as item (item)}
        {@const Icon = ICONS[item]}
        <button type="button" class:active={item === hazard} onclick={() => (hazard = item)}>
          <Icon size={14} strokeWidth={1.9} aria-hidden="true" />
          {ui(language, `hazard_${item}`)}
        </button>
      {/each}
    </div>
  </div>

  <WarningsMap {zones} {levels} selected={selected?.zone || ''} {language} onSelect={selectZone} />

  <div class="legend">
    {#each [1, 2, 3] as level (level)}
      <span><i style:background={LEVEL_COLORS[level]}></i>{ui(language, `level_${level}`)}</span>
    {/each}
    <span><i class="outside"></i>{ui(language, 'warnings_outside')}</span>
  </div>

  <div class="grid">
    <div class="list">
      <div class="filters">
        <input type="search" placeholder={ui(language, 'warnings_search')} bind:value={query} />
        <select bind:value={country}>
          <option value="all">{ui(language, 'warnings_all_countries')}</option>
          {#each countries as item (item.code)}
            <option value={item.code}>{item.name}</option>
          {/each}
        </select>
      </div>

      {#if !groups.length}
        <p class="empty">{ui(language, 'warnings_empty')}</p>
      {/if}
      {#each groups as group (group.country)}
        <h3>{countryName(group.country)} · {group.warnings.length}</h3>
        <ul>
          {#each group.warnings as warning (warningKey(warning))}
            {@const Icon = ICONS[warning.hazard]}
            <li>
              <button
                type="button"
                class:selected={warningKey(warning) === selectedKey}
                onclick={() => (selectedKey = warningKey(warning))}
              >
                <Icon size={17} strokeWidth={1.9} aria-hidden="true" />
                <span class="who">
                  <strong>{NAMES[warning.zone]}</strong>
                  <small>{summary(warning)} · {span(warning)}</small>
                </span>
                <span class="badge" style:background={LEVEL_COLORS[warning.level]}>{ui(language, `level_${warning.level}`)}</span>
              </button>
            </li>
          {/each}
        </ul>
      {/each}
    </div>

    <div class="detail">
      {#if selected}
        {@const Icon = ICONS[selected.hazard]}
        <div class="detail-head">
          <Icon size={22} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <h3>{NAMES[selected.zone]} · {ui(language, `hazard_${selected.hazard}`)}</h3>
            <p>{countryName(zoneCountry(selected.zone))} · {summary(selected)}</p>
          </div>
          <span class="badge" style:background={LEVEL_COLORS[selected.level]}>{ui(language, `level_${selected.level}`)}</span>
        </div>

        <div class="timeline" aria-hidden="true">
          {#each selected.hourly as level, hour (hour)}
            <span style:background={level ? LEVEL_COLORS[level] : null} title={`${hour} h`}></span>
          {/each}
        </div>
        <div class="ticks">
          <span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span>
          <span>{ui(language, 'warnings_local_time', { zone: zoneLabel(selected) })}</span>
        </div>

        <dl class="facts">
          <div>
            <dt>{ui(language, 'warnings_peak')}</dt>
            <dd>{measure(selected.peak, selected.hazard)}</dd>
          </div>
          <div>
            <dt>{ui(language, 'warnings_confidence')}</dt>
            <dd>
              {selected.runs
                ? ui(language, 'warnings_runs', { n: selected.runs[0], m: selected.runs[1] })
                : ui(language, 'warnings_ecmwf_only')}
            </dd>
          </div>
          <div class="wide">
            <dt>{ui(language, selected.thresholds ? 'warnings_thresholds' : 'warnings_convective')}</dt>
            <dd>
              {#if selected.thresholds}
                {selected.thresholds.map((value) => measure(value, selected.hazard)).join(' · ')}
              {:else}
                —
              {/if}
            </dd>
          </div>
        </dl>

        <div class="actions">
          <a href={forecastHref(language)} data-sveltekit-reload>
            {ui(language, 'warnings_view_forecast')}
          </a>
          <a href={`/${language}/map`}>{ui(language, 'warnings_view_stations')}</a>
        </div>
      {:else}
        <p class="empty">{ui(language, 'warnings_pick')}</p>
      {/if}
    </div>
  </div>

  <p class="disclaimer">
    {ui(language, 'warnings_disclaimer')}
    <a href="https://meteoalarm.org" target="_blank" rel="noopener noreferrer">Meteoalarm</a>.
  </p>
</section>

<style>
  .warnings { display: flex; flex-direction: column; gap: 14px; }

  .head { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; flex-wrap: wrap; }
  .head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted); text-wrap: balance; }
  .meta { display: flex; align-items: center; gap: 10px; font-size: 0.74rem; color: var(--muted); }
  .demo {
    padding: 3px 9px; border-radius: 999px; font-weight: 650;
    color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent);
  }

  .controls { display: flex; gap: 10px; flex-wrap: wrap; }
  .seg { display: flex; flex-wrap: wrap; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg button {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 13px; border: 0; border-radius: 7px; background: transparent;
    font: inherit; font-size: 0.76rem; font-weight: 640; color: var(--muted); cursor: pointer;
  }
  .seg button:hover { color: var(--ink-2); }
  .seg button.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }
  .seg small { font-size: 0.62rem; font-weight: 700; color: var(--muted); }

  .legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.72rem; color: var(--muted); margin-top: -4px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend i { width: 11px; height: 11px; border-radius: 3px; }
  .legend i.outside {
    border: 1px dashed var(--muted);
    background: color-mix(in srgb, var(--muted) 35%, transparent);
  }

  .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 16px; align-items: start; }

  .list, .detail { padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .filters { display: flex; gap: 8px; margin-bottom: 4px; }
  .filters input, .filters select {
    padding: 8px 12px; border: 1px solid var(--border); border-radius: 9px;
    background: var(--panel-2); color: var(--ink); font: inherit; font-size: 0.78rem;
  }
  .filters input { flex: 1; min-width: 0; }
  .list h3 { margin: 14px 0 4px; font-size: 0.7rem; font-weight: 650; color: var(--muted); }
  ul { list-style: none; margin: 0; padding: 0; }
  li button {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 9px 8px; border: 0; border-bottom: 1px solid var(--border); border-radius: 0;
    background: transparent; color: var(--ink-2); font: inherit; text-align: left; cursor: pointer;
  }
  li:last-child button { border-bottom: 0; }
  li button:hover, li button.selected { background: var(--panel-2); color: var(--ink); }
  .who { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
  .who strong { font-size: 0.84rem; font-weight: 650; color: var(--ink); }
  .who small { font-size: 0.72rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .badge {
    padding: 2px 9px; border-radius: 7px; white-space: nowrap;
    font-size: 0.7rem; font-weight: 700; color: #1b1206;
  }

  .detail-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; color: var(--ink-2); }
  .detail-head div { flex: 1; min-width: 0; }
  .detail-head h3 { font-size: 0.95rem; font-weight: 700; color: var(--ink); }
  .detail-head p { margin-top: 2px; font-size: 0.76rem; color: var(--muted); }

  .timeline { display: flex; gap: 2px; height: 22px; border-radius: 6px; overflow: hidden; }
  .timeline span { flex: 1; background: var(--panel-2); }
  .ticks { display: flex; justify-content: space-between; margin: 5px 0 14px; font-size: 0.66rem; color: var(--muted); }

  .facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0; }
  .facts div { padding: 9px 11px; border-radius: 10px; background: var(--panel-2); }
  .facts .wide { grid-column: 1 / -1; }
  .facts dt { font-size: 0.68rem; color: var(--muted); }
  .facts dd { margin: 2px 0 0; font-size: 0.92rem; font-weight: 680; font-variant-numeric: tabular-nums; }

  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .actions a {
    padding: 7px 12px; border: 1px solid var(--border-2); border-radius: 8px;
    background: var(--card); color: var(--ink-2); font-size: 0.74rem; font-weight: 650; text-decoration: none;
  }
  .actions a:hover { color: var(--ink); border-color: var(--accent); }

  .empty { padding: 18px 0; font-size: 0.82rem; color: var(--muted); }
  .disclaimer { font-size: 0.72rem; color: var(--muted); }
  .disclaimer a { color: var(--ink-2); }

  @media (max-width: 820px) {
    .grid { grid-template-columns: 1fr; }
  }
</style>

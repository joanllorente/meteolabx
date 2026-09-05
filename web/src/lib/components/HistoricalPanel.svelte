<script>
  /**
   * Histórico: selector del periodo, hitos, resumen, climograma y tabla.
   *
   * La consulta no se dispara sola. Cada bloque mes×año es una petición al
   * proveedor —y hay redes que tardan segundos por bloque—, así que abrir la
   * pestaña prepara el formulario y espera al botón. La selección viaja por
   * la URL, con lo que una consulta concreta se puede enlazar y volver a ella
   * no vuelve a preguntar al proveedor.
   */
  import Climogram from './Climogram.svelte';
  import TemperatureDistribution from './TemperatureDistribution.svelte';
  import WindHistoryChart from './WindHistoryChart.svelte';
  import Icon from './Icon.svelte';
  import { num } from '$lib/format.js';
  import app from '$lib/i18n/app-i18n.generated.js';
  import { cardinals, ui } from '$lib/i18n/ui.js';
  import { milestoneCards, summaryCards } from '$lib/historical/cards.js';
  import { navigating } from '$app/state';
  import { closeOnOutside } from '$lib/close-on-outside.js';
  import { families } from '$lib/families.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import {
    convertRadiationEnergy,
    convertSeries,
    convertUnit,
    radiationEnergyLabel,
    unitLabel
  } from '$lib/units.js';

  let {
    summary,
    language,
    mode,
    selection,
    requested = false,
    warning = '',
    period = null,
    maxBlocks = 12,
    unsupported = false,
    stationName = '',
    failure = '',
    provider = '',
    busy = false
  } = $props();

  /**
   * Consultando.
   *
   * El formulario navega, y esa navegación tarda lo que tarde el proveedor
   * —seis segundos es normal para un mes—. Sin señal, pulsar el botón parece
   * no hacer nada y se acaba pulsando otra vez.
   */
  // Las estaciones propias no navegan para consultar: la petición sale de
  // este navegador con su credencial, y quien sabe que está en marcha es la
  // página, no el enrutador. De ahí `busy`.
  const loading = $derived(busy || Boolean(navigating?.to));

  const texts = $derived(app.historical?.[language] || app.historical?.es || {});
  const cards = $derived(texts.cards || {});
  const tick = $derived((value, decimals = 0) => num(value, { language, decimals }));

  // El modo se cambia dentro del formulario: es parte de la consulta, no una
  // navegación. Cambiarlo no dispara nada, solo decide si hay meses que elegir.
  let formMode = $state(mode);
  $effect(() => {
    formMode = mode;
  });

  function parsedNumber(value) {
    const number = Number(String(value ?? '').replace(',', '.'));
    return Number.isFinite(number) ? number : null;
  }

  function convertedPart(part, { delta = false } = {}) {
    if (!part) return part;
    const sourceUnit = String(part.unit || '');
    const raw = parsedNumber(part.value);
    if (raw === null) return part;
    let family = '';
    if (/°C|\bK\b/i.test(sourceUnit)) family = 'temperature';
    else if (/km\/h|m\/s|mph|\bkt\b/i.test(sourceUnit)) family = 'wind';
    else if (/hPa|mmHg|inHg/i.test(sourceUnit)) family = 'pressure';
    else if (/\bmm\b|\bin\b/i.test(sourceUnit)) family = 'precip';

    if (/MJ\/m²|kWh\/m²/i.test(sourceUnit)) {
      return {
        ...part,
        value: num(convertRadiationEnergy(raw, unitPreferences), { language, decimals: 2 }),
        unit: radiationEnergyLabel(unitPreferences)
      };
    }
    if (/W\/m²/i.test(sourceUnit)) family = 'radiation';
    if (!family) return part;
    // «Por hora» solo tiene sentido en la lluvia: mm/h es una intensidad, y al
    // cambiar de unidad hay que reponer el «/h». En el viento la unidad ES
    // km/h, así que reponerlo daba «99,0 km/h/h».
    const perHour = family === 'precip' && /\/h\s*$/i.test(sourceUnit);
    const decimals = family === 'precip' && unitPreferences.precip === 'in'
      ? 2
      : family === 'radiation' && unitPreferences.radiation !== 'wm2'
        ? 2
        : 1;
    return {
      ...part,
      value: num(convertUnit(raw, family, unitPreferences, { delta }), { language, decimals }),
      unit: `${unitLabel(family, unitPreferences)}${perHour ? '/h' : ''}`
    };
  }

  function convertedText(text, options = {}) {
    const source = String(text || '');
    const match = source.match(/^(-?[\d.,]+)\s*(°C|K|km\/h|m\/s|mph|kt|hPa|mmHg|inHg|mm\/h|in\/h|mm|in|W\/m²|MJ\/m²|kWh\/m²)(.*)$/i);
    if (!match) return source;
    const converted = convertedPart({ value: match[1], unit: match[2] }, options);
    return `${converted.value} ${converted.unit}${match[3] || ''}`;
  }

  function convertMilestone(card) {
    if (card.kind === 'pair') {
      return {
        ...card,
        primary: convertedPart(card.primary),
        secondary: convertedPart(card.secondary),
        footer: card.footer ? { ...card.footer, value: convertedText(card.footer.value, { delta: true }) } : null,
        footerItems: card.footerItems.map((item) => ({ ...item, value: convertedText(item.value) }))
      };
    }
    if (card.kind === 'wind') {
      return { ...card, day: convertedPart(card.day), month: convertedPart(card.month) };
    }
    return {
      ...convertedPart(card),
      extras: (card.extras || []).map((item) => ({ ...item, value: convertedText(item.value) }))
    };
  }

  const milestones = $derived(milestoneCards(summary, cards).map(convertMilestone));
  const groups = $derived(
    summaryCards(summary, cards).map((group) => ({
      ...group,
      items: group.items.map((item) => convertedPart(item))
    }))
  );
  const chart = $derived(summary?.chart && ({
    ...summary.chart,
    temp_max: convertSeries(summary.chart.temp_max, 'temperature', unitPreferences),
    temp_min: convertSeries(summary.chart.temp_min, 'temperature', unitPreferences),
    temp_mean: convertSeries(summary.chart.temp_mean, 'temperature', unitPreferences),
    precip_total: convertSeries(summary.chart.precip_total, 'precip', unitPreferences)
  }));
  const hasChart = $derived(Boolean(chart?.labels?.length));

  /**
   * Cómo se llaman las tres curvas del climograma.
   *
   * Depende de qué agrega cada punto. En la vista diaria son la máxima, la
   * media y la mínima DE ESE DÍA; llamarlas «media de máximas» —el nombre que
   * les toca cuando cada punto resume un mes o un año— era decir que el 6 de
   * agosto promedia algo. La leyenda de arriba y la lectura del cursor salen
   * ahora del mismo sitio, que además se contradecían entre sí.
   */
  const chartLabels = $derived(
    summary?.granularity === 'daily'
      ? {
          precip: ui(language, 'legend_precip'),
          tmax: ui(language, 'legend_temp_max'),
          tmean: ui(language, 'legend_temp_mean'),
          tmin: ui(language, 'legend_temp_min')
        }
      : {
          precip: texts.chart?.legend?.precip || ui(language, 'legend_precip'),
          tmax: texts.chart?.legend?.temp_max || ui(language, 'legend_temp_max'),
          tmean: texts.chart?.legend?.temp_mean || ui(language, 'legend_temp_mean'),
          tmin: texts.chart?.legend?.temp_min || ui(language, 'legend_temp_min')
        }
  );

  // El viento se dibuja si la red publica algo: media, racha o ambas. Sin
  // nada que enseñar, la sección entera desaparece en vez de salir vacía.
  const wind = $derived(summary?.wind && ({
    ...summary.wind,
    wind_mean: convertSeries(summary.wind.wind_mean, 'wind', unitPreferences),
    gust_max: convertSeries(summary.wind.gust_max, 'wind', unitPreferences),
    unit: unitLabel('wind', unitPreferences)
  }));
  const hasWind = $derived(
    Boolean(wind?.labels?.length) &&
      [...(wind.wind_mean || []), ...(wind.gust_max || [])].some((value) => Number.isFinite(value))
  );

  // La distribución usa los días originales, no los puntos agregados del
  // climograma. Por eso sigue siendo informativa al pedir muchos años.
  const temperatureDistribution = $derived(summary?.temperature_distribution);
  const hasTemperatureDistribution = $derived(
    ['temp_max', 'temp_min', 'temp_mean'].some(
      (key) => (temperatureDistribution?.[key]?.sample_count || 0) > 0
    )
  );
  // Se conserva la implementación para poder recuperarla más adelante, pero
  // la distribución queda temporalmente fuera de la interfaz hasta cerrar
  // una representación que resulte útil con todos los proveedores.
  const SHOW_TEMPERATURE_DISTRIBUTION = false;

  /** «⚠️ En modo mensual el máximo es {max_blocks}…» con sus huecos rellenos. */
  const warningText = $derived(() => {
    if (!warning) return '';
    const templates = { ...(texts.warnings || {}), ...(texts.info || {}) };
    const raw = templates[warning] || '';
    return raw
      .replace('{max_blocks}', String(maxBlocks))
      .replace('{selected_blocks}', String(period?.blocks ?? 0));
  });

  const periodText = $derived(() => {
    const raw = texts.caption?.period_summary || '';
    if (!raw || !period?.range) return '';
    return raw
      .replace('{period_range}', period.range)
      .replace('{blocks}', String(period.blocks))
      .replace('{days}', String(period.days));
  });

  // Cada tarjeta lleva el color y el icono de su familia: la temperatura
  // naranja, el viento turquesa… el mismo criterio que en observación.
  const KIND = {
    temp: { icon: 'Thermometer', family: 'temperature' },
    temp_cold: { icon: 'ThermometerSnowflake', family: 'dewpoint' },
    temp_night: { icon: 'Moon', family: 'thermo' },
    wind: { icon: 'Wind', family: 'wind' },
    rain: { icon: 'CloudRain', family: 'precip' },
    solar: { icon: 'Sun', family: 'radiation' }
  };
  const kindOf = (name) => KIND[name] || KIND.temp;
  const familyOf = (name) => families[kindOf(name).family] || families.temperature;

  const convertedTable = $derived.by(() => {
    const source = summary?.table || { columns: [], rows: [] };
    const familiesByColumn = source.columns.map((column) => {
      if (/°C|\bK\b/i.test(column)) return 'temperature';
      if (/km\/h|m\/s|mph|\bkt\b/i.test(column)) return 'wind';
      if (/hPa|mmHg|inHg/i.test(column)) return 'pressure';
      if (/\(mm\)|\(in\)/i.test(column)) return 'precip';
      return '';
    });
    const columns = source.columns.map((column, index) => {
      const family = familiesByColumn[index];
      return family ? column.replace(/\([^)]*\)\s*$/, `(${unitLabel(family, unitPreferences)})`) : column;
    });
    const rows = source.rows.map((row) => row.map((cell, index) => {
      const family = familiesByColumn[index];
      const raw = parsedNumber(cell);
      if (!family || raw === null) return cell;
      return num(convertUnit(raw, family, unitPreferences), {
        language,
        decimals: family === 'precip' && unitPreferences.precip === 'in' ? 2 : 1
      });
    }));
    return { columns, rows };
  });
</script>

<div class="hist-head">
  <div>
    <h2>{ui(language, 'historical_title')}</h2>
    <p>{ui(language, 'historical_subtitle')}</p>
  </div>
</div>

{#if unsupported}
  <p class="empty">{ui(language, 'historical_unsupported')}</p>
{:else}
  <!-- Formulario GET: la selección acaba en la URL y la consulta la hace el
       servidor al cargar esa URL. Funciona igual sin JavaScript. -->
  <form class="query" method="GET">
    <input type="hidden" name="consulta" value="1" />

    <div class="field">
      <span class="field-label">{texts.summary?.label || ui(language, 'historical_mode')}</span>
      <div class="seg">
        <label class:active={formMode === 'monthly'}>
          <input
            type="radio" name="modo" value="mensual"
            checked={formMode === 'monthly'}
            onchange={() => (formMode = 'monthly')} />
          <span>{texts.summary?.options?.monthly || ui(language, 'mode_monthly')}</span>
        </label>
        <label class:active={formMode === 'annual'}>
          <input
            type="radio" name="modo" value="anual"
            checked={formMode === 'annual'}
            onchange={() => (formMode = 'annual')} />
          <span>{texts.summary?.options?.annual || ui(language, 'mode_annual')}</span>
        </label>
      </div>
    </div>

    {#if formMode === 'monthly'}
      <div class="field">
        <span class="field-label">{texts.inputs?.months}</span>
        <details class="picker" use:closeOnOutside>
          <summary>
            {selection.months.length === 1
              ? selection.monthOptions.find((item) => item.value === selection.months[0])?.label
              : `${selection.months.length} · ${texts.inputs?.months}`}
          </summary>
          <div class="options months">
            {#each selection.monthOptions as month (month.value)}
              <label>
                <input
                  type="checkbox" name="meses" value={month.value}
                  checked={selection.months.includes(month.value)} />
                <span>{month.label}</span>
              </label>
            {/each}
          </div>
        </details>
      </div>
    {/if}

    <div class="field">
      <span class="field-label">{texts.inputs?.years}</span>
      <details class="picker" use:closeOnOutside>
        <summary>
          {selection.years.length === 1
            ? selection.years[0]
            : `${selection.years.length} · ${texts.inputs?.years}`}
        </summary>
        <div class="options years">
          {#each selection.yearOptions as year (year)}
            <label>
              <input
                type="checkbox" name="anios" value={year}
                checked={selection.years.includes(year)} />
              <span>{year}</span>
            </label>
          {/each}
        </div>
      </details>
    </div>

    <button class="go" type="submit" disabled={loading}>
      {#if loading}<span class="spin" aria-hidden="true"></span>{/if}
      {texts.actions?.query}
    </button>
  </form>

  {#if periodText()}<p class="period">{periodText()}</p>{/if}
  {#if warningText() && !loading}<p class="warn">{warningText()}</p>{/if}
  {#if failure && !loading}
    <p class="warn">{(texts.errors?.provider_generic || '{provider}: {error}')
      .replace('{provider}', provider)
      .replace('{error_type}: ', '')
      .replace('{error_type}', '')
      .replace('{error}', failure)}</p>
  {/if}

  {#if loading}
    <!-- Esqueleto: ocupa el sitio de lo que viene, para que se vea que la
         página está trabajando y no que se ha quedado en blanco. -->
    <div class="sec-head"><h3>{texts.sections?.extremes}</h3><span class="rule"></span></div>
    <div class="cards">
      {#each Array(6) as _, index (index)}
        <article class="card ghost">
          <header><span class="ic"></span><span class="bar w60"></span></header>
          <span class="bar big"></span>
          <span class="bar w40"></span>
        </article>
      {/each}
    </div>
    <p class="loading">{(texts.spinner?.loading || '…')
      .replace('{provider}', provider)
      .replace(/\s+(de|of|di|da|d’)?\s*\.\.\.$/u, provider ? '$&' : '…')}</p>
  {:else if !requested}
    <p class="empty">{texts.info?.select_month_and_year}</p>
  {:else if !summary?.has_data && !warningText() && !failure}
    <p class="empty">{texts.warnings?.no_data_selected_period || ui(language, 'historical_empty')}</p>
  {/if}

  {#if summary?.has_data && !loading}
    {#if milestones.length}
      <div class="sec-head"><h3>{texts.sections?.extremes}</h3><span class="rule"></span></div>
      <div class="cards">
        {#each milestones as card (card.key)}
          <article
            class="card"
            style:--fam={familyOf(card.icon).color}
            style:--fam-soft={familyOf(card.icon).soft}
          >
            <header>
              <span class="ic"><Icon name={kindOf(card.icon).icon} size={17} /></span>
              <h3>{card.title}</h3>
            </header>

            {#if card.kind === 'pair'}
              <div class="readout duo">
                {#each [card.primary, card.secondary] as side, index (index)}
                  <div class="side">
                    <div class="val tnum">{side.value}<span class="unit">{side.unit}</span></div>
                    {#if side.date}<span class="date">{side.date}</span>{/if}
                  </div>
                {/each}
              </div>
              {#if card.footer || card.footerItems.length}
                <ul class="sub">
                  {#if card.footer}
                    <li><span class="lbl">{card.footer.label}</span><span class="v tnum">{card.footer.value}</span></li>
                  {/if}
                  {#each card.footerItems as item, index (index)}
                    <li><span class="lbl">{item.label}</span><span class="v tnum">{item.value}</span></li>
                  {/each}
                </ul>
              {/if}
            {:else if card.kind === 'wind'}
              <div class="readout duo">
                {#each [card.day, card.month] as side, index (index)}
                  <div class="side">
                    <span class="lbl">{side.label}</span>
                    <div class="val tnum">{side.value}<span class="unit">{side.unit}</span></div>
                    <span class="date">
                      {#if side.direction}<b class="dir">{side.direction.cardinal}</b> {side.direction.degrees} · {/if}{side.date}
                    </span>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="readout">
                <div class="val tnum">{card.value}<span class="unit">{card.unit}</span></div>
                {#if card.direction}
                  <div class="compass">
                    <b class="dir">{card.direction.cardinal}</b>
                    <small>{card.direction.degrees}</small>
                  </div>
                {/if}
              </div>
              {#if card.date}<span class="date">{card.date}</span>{/if}
              {#if card.extras.length}
                <ul class="sub">
                  {#each card.extras as extra, index (index)}
                    <li><span class="lbl">{extra.label}</span><span class="v tnum">{extra.value}</span></li>
                  {/each}
                </ul>
              {/if}
            {/if}
          </article>
        {/each}
      </div>
    {/if}

    {#if groups.length}
      <div class="sec-head"><h3>{texts.sections?.summary}</h3><span class="rule"></span></div>
      <div class="cards">
        {#each groups as group (group.key)}
          <article
            class="card"
            style:--fam={familyOf(group.icon).color}
            style:--fam-soft={familyOf(group.icon).soft}
          >
            <header>
              <span class="ic"><Icon name={kindOf(group.icon).icon} size={17} /></span>
              <h3>{group.title}</h3>
            </header>
            <div class="readout">
              <div class="val tnum">
                {group.items[0].value}<span class="unit">{group.items[0].unit}</span>
              </div>
              <span class="first-label">{group.items[0].label}</span>
            </div>
            {#if group.items.length > 1}
              <ul class="sub">
                {#each group.items.slice(1) as item, index (index)}
                  <li>
                    <span class="lbl">{item.label}</span>
                    <span class="v tnum">{item.value} <small>{item.unit}</small></span>
                  </li>
                {/each}
              </ul>
            {/if}
          </article>
        {/each}
      </div>
    {/if}

    {#if hasChart}
      <div class="sec-head">
        <h3>{ui(language, 'section_climogram')}</h3>
        <span class="rule"></span>
        <div class="legend">
          <span><i class="bar"></i>{chartLabels.precip}</span>
          <span><i style:background="#ff8a4c"></i>{chartLabels.tmax}</span>
          <span><i class="dash"></i>{chartLabels.tmean}</span>
          <span><i style:background="#4db6e8"></i>{chartLabels.tmin}</span>
        </div>
      </div>
      <section class="chart-card">
        <Climogram
          months={chart.labels}
          tmax={chart.temp_max}
          tmin={chart.temp_min}
          tmean={chart.temp_mean}
          precip={chart.precip_total}
          temperatureUnit={unitLabel('temperature', unitPreferences)}
          precipUnit={unitLabel('precip', unitPreferences)}
          formatTick={tick}
          formatValue={(value) => tick(value, 1)}
          label={ui(language, 'section_climogram')}
          exportName={`meteolabx ${ui(language, 'section_climogram')} ${stationName}`}
          exportLabel={ui(language, 'download_png')}
          seriesLabels={chartLabels}
        />
      </section>
    {/if}

    {#if SHOW_TEMPERATURE_DISTRIBUTION && hasTemperatureDistribution}
      <div class="sec-head">
        <h3>{ui(language, 'temperature_distribution')}</h3>
        <span class="rule"></span>
      </div>
      <section class="chart-card">
        <TemperatureDistribution
          distribution={temperatureDistribution}
          labels={{
            max: ui(language, 'distribution_tmax'),
            min: ui(language, 'distribution_tmin'),
            mean: ui(language, 'distribution_tmean')
          }}
          frequencyLabel={ui(language, 'distribution_frequency')}
          daysLabel={ui(language, 'distribution_days')}
          coverageTemplate={ui(language, 'distribution_coverage')}
          samplesTemplate={ui(language, 'distribution_samples')}
          formatNumber={tick}
          label={ui(language, 'temperature_distribution')}
          exportName={`meteolabx ${ui(language, 'temperature_distribution')} ${stationName}`}
          exportLabel={ui(language, 'download_png')}
        />
      </section>
    {/if}

    {#if hasWind}
      <div class="sec-head">
        <h3>{ui(language, 'wind')}</h3>
        <span class="rule"></span>
        <div class="legend">
          {#if (wind.wind_mean || []).some((value) => Number.isFinite(value))}
            <span><i style:background="var(--wind, #37c8d6)"></i>{ui(language, 'wind')}</span>
          {/if}
          {#if (wind.gust_max || []).some((value) => Number.isFinite(value))}
            <span><i class="soft"></i>{ui(language, 'gust')}</span>
          {/if}
          {#if wind.direction_kind}
            <!-- No es lo mismo el rumbo predominante que el de la racha: hay
                 redes que solo publican el segundo, y decirlo evita leer una
                 cosa por otra. -->
            <span>↑ {wind.direction_kind === 'gust'
              ? `${ui(language, 'direction')} · ${ui(language, 'gust')}`
              : cards.summary_labels?.predominant_direction || ui(language, 'direction')}</span>
          {/if}
        </div>
      </div>
      <section class="chart-card">
        <WindHistoryChart
          labels={wind.labels}
          mean={wind.wind_mean}
          gust={wind.gust_max}
          direction={wind.direction}
          directionKind={wind.direction_kind}
          unit={wind.unit}
          cardinals={cardinals(language)}
          seriesLabels={{
            mean: ui(language, 'wind'),
            gust: ui(language, 'gust'),
            direction: ui(language, 'direction')
          }}
          formatTick={tick}
          formatValue={(value) => tick(value, 1)}
          label={ui(language, 'wind')}
          exportName={`meteolabx ${ui(language, 'wind')} ${stationName}`}
          exportLabel={ui(language, 'download_png')}
        />
      </section>
    {/if}

    {#if convertedTable.rows.length}
      <div class="sec-head"><h3>{ui(language, 'section_table')}</h3><span class="rule"></span></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>{#each convertedTable.columns as column (column)}<th>{column}</th>{/each}</tr>
          </thead>
          <tbody>
            {#each convertedTable.rows as row, index (index)}
              <tr>{#each row as cell, cellIndex (cellIndex)}<td class:tnum={cellIndex > 0}>{cell}</td>{/each}</tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
{/if}

<style>
  .hist-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .hist-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .hist-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted);  text-wrap: balance; }

  .query {
    display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap;
    padding: 14px 16px; margin-bottom: 12px;
    border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel);
  }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-label { font-size: 0.7rem; font-weight: 650; color: var(--muted); }

  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg label { padding: 6px 13px; border-radius: 7px; font-size: 0.76rem; font-weight: 600; color: var(--muted); cursor: pointer; }
  .seg label.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }
  .seg input { position: absolute; opacity: 0; pointer-events: none; }

  .picker { position: relative; }
  .picker summary {
    min-width: 148px; padding: 8px 12px; list-style: none; cursor: pointer;
    border: 1px solid var(--border); border-radius: 9px; background: var(--panel-2);
    font-size: 0.78rem; font-weight: 600; color: var(--ink);
  }
  .picker summary::-webkit-details-marker { display: none; }
  .picker summary::after { content: '▾'; float: right; color: var(--muted); }
  .picker[open] summary { border-color: var(--border-2); }
  .options {
    position: absolute; z-index: 12; top: calc(100% + 6px); left: 0;
    display: grid; gap: 2px; max-height: 260px; overflow-y: auto;
    padding: 8px; border: 1px solid var(--border-2); border-radius: 10px;
    background: var(--panel); box-shadow: var(--shadow);
  }
  .options.months { grid-template-columns: repeat(2, minmax(112px, 1fr)); }
  .options.years { grid-template-columns: repeat(3, minmax(64px, 1fr)); }
  .options label {
    display: flex; align-items: center; gap: 7px; padding: 5px 7px;
    border-radius: 7px; font-size: 0.76rem; color: var(--ink-2); cursor: pointer;
    white-space: nowrap;
  }
  .options label:hover { background: var(--card); color: var(--ink); }

  .go {
    padding: 9px 18px; border: 1px solid transparent; border-radius: 9px;
    background: var(--accent); color: #fff; font-size: 0.79rem; font-weight: 700;
  }
  .go { display: inline-flex; align-items: center; gap: 8px; }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: 0.72; cursor: progress; }
  .spin {
    width: 13px; height: 13px; flex: none;
    border: 2px solid rgba(255, 255, 255, 0.35); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Esqueleto de carga: las mismas cajas, sin contenido. */
  .card.ghost { pointer-events: none; }
  .card.ghost::before { background: var(--border-2); }
  .card.ghost .ic { background: var(--panel-2); }
  .bar { display: block; height: 11px; border-radius: 6px; background: var(--panel-2); }
  .bar.w60 { width: 60%; }
  .bar.w40 { width: 40%; height: 9px; }
  .bar.big { width: 52%; height: 26px; }
  .card.ghost .ic, .card.ghost .bar { animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.45; } }
  .loading { margin-top: 14px; font-size: 0.8rem; color: var(--muted); }

  @media (prefers-reduced-motion: reduce) {
    .spin, .card.ghost .ic, .card.ghost .bar { animation: none; }
  }

  .period { font-size: 0.74rem; color: var(--muted); margin-bottom: 6px; }
  .warn { font-size: 0.8rem; color: var(--ink-2); margin-bottom: 10px; }

  .sec-head { display: flex; align-items: center; gap: 14px; margin: 26px 0 13px; flex-wrap: wrap; }
  .sec-head h3 { font-size: 0.96rem; font-weight: 700; }
  .rule { height: 1px; flex: 1; background: var(--border); min-width: 30px; }

  .legend { display: flex; gap: 12px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; font-size: 0.68rem; color: var(--muted); }
  .legend i { width: 10px; height: 3px; border-radius: 2px; }
  .legend i.soft { width: 9px; height: 9px; border-radius: 2px; background: var(--wind, #37c8d6); opacity: 0.4; }
  .legend i.bar { width: 9px; height: 9px; border-radius: 2px; background: #5b9bff; opacity: 0.55; }
  .legend i.dash { background: repeating-linear-gradient(90deg, #c9d2dc 0 4px, transparent 4px 7px); }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(248px, 1fr)); gap: 12px; }

  /* Las mismas piezas que una tarjeta de observación: filo de color de la
     familia, icono en su tono suave, lectura grande y lista de apoyo. */
  .card {
    position: relative;
    display: flex; flex-direction: column; gap: 12px;
    padding: 16px 16px 15px;
    border: 1px solid var(--border); border-radius: var(--r-md);
    background: var(--card); overflow: hidden;
    transition: border-color 0.18s, transform 0.18s, background 0.18s;
  }
  .card::before {
    content: ''; position: absolute; inset: 0 auto 0 0;
    width: 3px; background: var(--fam); opacity: 0.85;
  }
  .card:hover { border-color: var(--border-2); background: var(--card-hover); transform: translateY(-2px); }

  .card header { display: flex; align-items: center; gap: 9px; }
  .ic {
    display: grid; place-items: center; width: 30px; height: 30px; flex: none;
    border-radius: 9px; color: var(--fam); background: var(--fam-soft);
  }
  .card h3 { font-size: 0.82rem; font-weight: 600; letter-spacing: -0.01em; color: var(--ink); }

  .readout { display: flex; align-items: flex-end; justify-content: space-between; gap: 10px; }
  .readout.duo { justify-content: flex-start; gap: 26px; }
  .side { display: flex; flex-direction: column; gap: 3px; }
  .val { font-size: 2.05rem; font-weight: 680; line-height: 1; letter-spacing: -0.03em; }
  .duo .val { font-size: 1.6rem; }
  .unit { margin-left: 4px; font-size: 0.86rem; font-weight: 600; color: var(--muted); letter-spacing: 0; }
  .first-label { font-size: 0.7rem; color: var(--muted); padding-bottom: 3px; }

  .compass { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; padding-bottom: 3px; }
  .dir { font-size: 0.92rem; font-weight: 700; color: var(--fam); }
  .compass small { font-size: 0.6rem; color: var(--muted); font-weight: 600; }
  .date { font-size: 0.7rem; color: var(--muted); font-variant-numeric: tabular-nums; }

  .sub {
    list-style: none; margin: 0; padding: 11px 0 0;
    border-top: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 7px;
  }
  .sub li { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .lbl { font-size: 0.72rem; color: var(--muted); }
  .sub .v { font-size: 0.76rem; font-weight: 600; color: var(--ink-2); }
  .sub .v small { font-weight: 600; color: var(--muted); }

  .chart-card { padding: 16px 18px 10px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }

  .table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  th, td { padding: 9px 14px; text-align: left; white-space: nowrap; }
  th { position: sticky; top: 0; background: var(--panel-2); color: var(--ink-2); font-weight: 650; font-size: 0.74rem; border-bottom: 1px solid var(--border); }
  td { border-bottom: 1px solid var(--border); color: var(--ink-2); }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover td { background: var(--card-hover); }

  .empty { padding: 32px 0; color: var(--muted); font-size: 0.9rem; }
</style>

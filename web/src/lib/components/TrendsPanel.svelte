<script>
  import TrendChart from './TrendChart.svelte';
  import { num, stationTime } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';

  let { charts, language, range, ranges, timeZone = 'UTC', stationName = '' } = $props();

  const tick = $derived((value, decimals = 1) => num(value, { language, decimals }));

  /**
   * Instante señalado por el cursor, compartido por los cuatro gráficos.
   *
   * Es lo que permite comparar: al pasar el ratón por uno, los cuatro marcan
   * el mismo momento y enseñan su valor ahí.
   */
  let activeEpoch = $state(null);

  const hourAt = $derived((epoch) =>
    epoch === null ? '' : stationTime(epoch, { language, timeZone })
  );
</script>

<div class="trend-head">
  <div>
    <h2>{ui(language, 'trends_title')}</h2>
    <p>
      {range === 'today'
        ? ui(language, 'trends_subtitle')
        : ui(language, 'trends_subtitle_days', { days: ranges.days })}
    </p>
  </div>
  <div class="seg" role="group">
    <a href={ranges.synoptic} class:active={range !== 'today'}>{ui(language, 'range_synoptic')}</a>
    <a href={ranges.today} class:active={range === 'today'}>{ui(language, 'range_today')}</a>
  </div>
</div>

{#if charts.length}
  <div class="charts">
    {#each charts as item (item.key)}
      <section class="chart-card">
        <header>
          <h3>
            {item.title}
            {#if item.help}
              <!-- La misma explicación que da la app actual, al instante y
                   también al enfocar con teclado. -->
              <span class="help" tabindex="0" role="note" aria-label={item.help}>?</span>
              <span class="bubble">{item.help}</span>
            {/if}
          </h3>
          <div class="legend">
            {#each item.series as line (line.label)}
              <span><i style:background={line.color}></i>{line.label}</span>
            {/each}
          </div>
        </header>
        <span class="axis-name">
          {item.axis}{item.minutes ? ` · ${ui(language, 'interval_note', { minutes: item.minutes })}` : ''}
          {#if activeEpoch !== null}<b class="at">{hourAt(activeEpoch)}</b>{/if}
        </span>
        <TrendChart
          series={item.series}
          labels={item.labels}
          zeroLine={item.zero}
          range={item.range}
          formatTick={tick}
          epochs={item.epochs}
          {activeEpoch}
          onHover={(epoch) => (activeEpoch = epoch)}
          formatValue={(value) => tick(value, 1)}
          exportName={`meteolabx ${item.title} ${stationName}`}
          exportLabel={ui(language, 'download_png')}
          width={1180}
          height={190}
          fillArea={item.series.length === 1 && !item.zero}
        />
      </section>
    {/each}
  </div>
{:else}
  <p class="empty">{ui(language, 'no_trend_data')}</p>
{/if}

<style>
  .trend-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .trend-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .trend-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted); }

  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg a { padding: 6px 12px; border-radius: 7px; font-size: 0.74rem; font-weight: 600; color: var(--muted); text-decoration: none; }
  .seg a:hover { color: var(--ink-2); }
  .seg a.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }

  .charts { display: grid; grid-template-columns: 1fr; gap: 14px; }
  .chart-card { padding: 18px 18px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .chart-card header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 2px; }
  .chart-card { position: relative; }
  .chart-card h3 { display: inline-flex; align-items: center; gap: 6px; font-size: 0.82rem; font-weight: 600; max-width: 76%; }

  .help {
    display: grid; place-items: center;
    width: 15px; height: 15px; flex: none;
    border: 1px solid var(--border-2); border-radius: 999px;
    color: var(--muted); font-size: 0.62rem; font-weight: 700; cursor: help;
  }
  .help:hover, .help:focus-visible { color: var(--ink); border-color: var(--accent); outline: none; }

  /* Anclada a la tarjeta, no al interrogante: así nunca se sale por el lado. */
  .bubble {
    position: absolute; top: 44px; left: 18px; right: 18px; z-index: 20;
    padding: 11px 13px;
    border: 1px solid var(--border-2); border-radius: 9px;
    background: var(--panel-2); color: var(--ink-2);
    font-size: 0.74rem; font-weight: 400; line-height: 1.5; text-align: left;
    box-shadow: var(--shadow);
    opacity: 0; visibility: hidden; transition: opacity 0.12s;
  }
  .help:hover ~ .bubble, .help:focus-visible ~ .bubble { opacity: 1; visibility: visible; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; font-size: 0.68rem; color: var(--muted); }
  .legend i { width: 9px; height: 9px; border-radius: 3px; }
  .at { margin-left: 8px; color: var(--ink-2); font-weight: 700; }
  .axis-name { display: block; font-size: 0.64rem; color: var(--muted-2); font-family: var(--mono); margin: 6px 0 2px; }

  .empty { padding: 40px 0; color: var(--muted); font-size: 0.9rem; }
</style>

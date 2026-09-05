<script>
  /**
   * Histograma de temperaturas diarias del periodo.
   *
   * Aunque el resumen abarque veinte años, cada día conserva un voto. La
   * altura se expresa en porcentaje —no en número bruto— para que dos
   * periodos de distinta duración sigan siendo comparables.
   */
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';

  let {
    distribution,
    labels = { max: 'Máximas diarias', min: 'Mínimas diarias', mean: 'Medias diarias' },
    frequencyLabel = 'Frecuencia',
    daysLabel = 'días',
    coverageTemplate = '{count} de {expected} días con datos · cobertura {coverage} %',
    samplesTemplate = '{count} días con datos',
    formatNumber = (value, decimals = 0) => Number(value).toFixed(decimals),
    label = 'Distribución de temperaturas',
    exportName = 'meteolabx-distribucion-temperaturas',
    exportLabel = 'Descargar PNG'
  } = $props();

  const choices = $derived([
    { key: 'temp_max', label: labels.max, color: '#ff8a4c' },
    { key: 'temp_min', label: labels.min, color: '#4db6e8' },
    { key: 'temp_mean', label: labels.mean, color: '#aeb9c8' }
  ]);
  let selected = $state('temp_max');

  $effect(() => {
    if ((distribution?.[selected]?.sample_count || 0) > 0) return;
    const available = choices.find((item) => (distribution?.[item.key]?.sample_count || 0) > 0);
    if (available) selected = available.key;
  });

  const choice = $derived(choices.find((item) => item.key === selected) || choices[0]);
  const series = $derived(distribution?.[selected] || {});
  const count = $derived(Number(series.sample_count || 0));
  const expected = $derived(Number(distribution?.expected_days || 0));
  const coverage = $derived(expected > 0 ? Math.min(100, (count * 100) / expected) : null);
  const coverageText = $derived.by(() => {
    if (expected <= 0) return samplesTemplate.replace('{count}', formatNumber(count));
    return coverageTemplate
      .replace('{count}', formatNumber(count))
      .replace('{expected}', formatNumber(expected))
      .replace('{coverage}', formatNumber(coverage, 1));
  });

  const starts = $derived(series.bin_start || []);
  const ends = $derived(series.bin_end || []);
  const counts = $derived(series.counts || []);
  const percentages = $derived(series.percentages || []);
  const unit = $derived(distribution?.unit || '°C');

  const W = 640;
  const H = 250;
  const pad = { t: 21, r: 22, b: 48, l: 43 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const n = $derived(Math.max(1, percentages.length));
  const slot = $derived(iw / n);
  const barWidth = $derived(Math.max(3, slot * 0.76));
  const observedMax = $derived(Math.max(0, ...percentages));
  const yMax = $derived(Math.max(5, Math.ceil((observedMax * 1.12) / 5) * 5));
  const ticks = $derived(Array.from({ length: 5 }, (_, index) => (yMax * index) / 4));
  const labelEvery = $derived(Math.max(1, Math.ceil(percentages.length / 11)));

  const xCenter = (index) => pad.l + (index + 0.5) * slot;
  const yValue = (value) => pad.t + ih - (Number(value || 0) / yMax) * ih;

  let svg;
  let active = $state(null);

  function pointerMove(event) {
    const rect = svg?.getBoundingClientRect();
    if (!rect?.width || !percentages.length) return;
    const x = ((event.clientX - rect.left) / rect.width) * W;
    active = Math.max(0, Math.min(percentages.length - 1, Math.floor((x - pad.l) / slot)));
  }

  const rangeText = (index) =>
    `${formatNumber(starts[index])}–<${formatNumber(ends[index])} ${unit}`;
</script>

<div class="distribution" style:--series={choice.color}>
  <div class="toolbar">
    <div class="seg" aria-label={label}>
      {#each choices as item (item.key)}
        <button
          type="button"
          class:active={selected === item.key}
          disabled={(distribution?.[item.key]?.sample_count || 0) === 0}
          onclick={() => (selected = item.key)}
        >{item.label}</button>
      {/each}
    </div>
    <p>{coverageText}</p>
  </div>

  <ChartFrame name={`${exportName}-${selected}`} label={exportLabel}>
    <svg
      viewBox="0 0 {W} {H}"
      class="histogram"
      role="img"
      aria-label={`${label} · ${choice.label}`}
      bind:this={svg}
      onpointermove={pointerMove}
      onpointerleave={() => (active = null)}
    >
      {#each ticks as tick}
        <line x1={pad.l} x2={W - pad.r} y1={yValue(tick)} y2={yValue(tick)} stroke="var(--grid-line)" />
        <text x={pad.l - 8} y={yValue(tick) + 3.5} class="axis" text-anchor="end">{formatNumber(tick)}%</text>
      {/each}
      <text x={pad.l} y={12} class="axis-title">{frequencyLabel}</text>

      {#each percentages as value, index}
        <rect
          x={xCenter(index) - barWidth / 2}
          y={yValue(value)}
          width={barWidth}
          height={Math.max(0, pad.t + ih - yValue(value))}
          rx="3"
          class:highlight={active === index}
        />
      {/each}

      {#each starts as start, index}
        {#if index % labelEvery === 0 || index === starts.length - 1}
          <text x={xCenter(index)} y={H - 27} class="axis" text-anchor="middle">{formatNumber(start)}</text>
        {/if}
      {/each}
      <text x={pad.l + iw / 2} y={H - 7} class="axis-title" text-anchor="middle">{unit}</text>

      {#if active !== null && Number.isFinite(percentages[active])}
        {@const boxWidth = 174}
        {@const boxX = Math.max(4, Math.min(W - boxWidth - 4, xCenter(active) > W / 2 ? xCenter(active) - boxWidth - 9 : xCenter(active) + 9))}
        <line x1={xCenter(active)} x2={xCenter(active)} y1={pad.t} y2={pad.t + ih} stroke="var(--border-2)" />
        <g transform="translate({boxX}, {pad.t + 8})">
          <rect width={boxWidth} height="49" rx="8" class="box" />
          <text x="10" y="16" class="box-title">{rangeText(active)}</text>
          <text x="10" y="36" class="box-label">{formatNumber(percentages[active], 1)}%</text>
          <text x={boxWidth - 10} y="36" class="box-value" text-anchor="end">{formatNumber(counts[active])} {daysLabel}</text>
        </g>
      {/if}

      <Watermark x={W - pad.r - 3} y={pad.t + ih - 6} />
    </svg>
  </ChartFrame>
</div>

<style>
  .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
  .toolbar p { margin: 0; font-size: 0.7rem; color: var(--muted); font-variant-numeric: tabular-nums; }
  .seg { display: inline-flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg button { padding: 6px 11px; border: 0; border-radius: 7px; background: transparent; color: var(--muted); font-size: 0.72rem; font-weight: 650; }
  .seg button.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }
  .seg button:disabled { opacity: 0.36; cursor: not-allowed; }
  .histogram { width: 100%; height: auto; display: block; }
  .histogram > rect { fill: var(--series); opacity: 0.58; transition: opacity 0.12s; }
  .histogram > rect.highlight { opacity: 0.92; }
  .axis { fill: var(--muted); font-size: 9.5px; font-family: var(--mono); }
  .axis-title { fill: var(--muted-2); font-size: 9px; font-weight: 650; }
  .box { fill: var(--panel); stroke: var(--border-2); stroke-width: 1; }
  .box-title { fill: var(--ink); font-size: 10px; font-weight: 700; }
  .box-label { fill: var(--series); font-size: 10px; font-weight: 700; }
  .box-value { fill: var(--ink-2); font-size: 9.5px; font-weight: 700; font-variant-numeric: tabular-nums; }

  @media (max-width: 640px) {
    .seg { width: 100%; }
    .seg button { flex: 1; padding-inline: 6px; }
  }
</style>

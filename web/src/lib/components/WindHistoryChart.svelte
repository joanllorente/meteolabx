<script>
  /**
   * Viento del periodo: media, racha máxima y rumbo.
   *
   * Las tres cosas no siempre están. Hay redes que dan media y racha con su
   * veleta, otras que solo publican el rumbo de la racha y otras que no miden
   * viento en su histórico. El componente dibuja lo que llega: sin medias no
   * hay línea, sin rumbos no hay flechas, y si no llega nada no se pinta.
   *
   * Las flechas apuntan hacia donde sopla —el convenio de los mapas—, y el
   * rumbo que se nombra es el de origen, que es como se dice el viento: un
   * «NNE» viene del nornoreste.
   */
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  import { niceStep, niceTicks, tickDecimals } from '$lib/observation/scale.js';

  let {
    labels = [],
    mean = [],
    gust = [],
    direction = [],
    directionKind = '',
    unit = 'km/h',
    cardinals = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'],
    seriesLabels = { mean: 'Viento', gust: 'Racha', direction: 'Dirección' },
    formatTick = (value) => value.toFixed(0),
    formatValue = null,
    label = 'Viento',
    exportName = 'meteolabx-viento',
    exportLabel = 'Descargar PNG'
  } = $props();

  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  const clean = (values) => values.filter(isNumber);

  const W = 640;
  const CHART_H = 190;
  const ARROWS_H = 22; // franja de flechas bajo el área de trazado
  const LEFT_PAD = 52;
  const RIGHT_PAD = 44;
  const AXIS_STEPS = 6;

  const iw = W - LEFT_PAD - RIGHT_PAD;
  const labelChars = $derived(
    labels.reduce((longest, item) => Math.max(longest, String(item).length), 0)
  );
  const labelWidth = $derived(labelChars * 6 + 8);
  const upright = $derived(labels.length > 1 && iw / labels.length < labelWidth);
  const axisSpace = $derived(upright ? Math.round(labelChars * 6) + 16 : 30);

  const hasArrows = $derived(clean(direction).length > 0);
  const arrowBand = $derived(hasArrows ? ARROWS_H : 0);
  const pad = $derived({ t: 16, r: RIGHT_PAD, b: axisSpace + arrowBand, l: LEFT_PAD });
  const H = $derived(CHART_H + axisSpace + arrowBand);
  const ih = CHART_H - 16;

  const speeds = $derived(clean([...mean, ...gust]));
  const vMax = $derived((speeds.length ? Math.max(...speeds) : 10) * 1.15 || 1);

  const n = $derived(labels.length || 1);
  const bw = $derived((iw / n) * 0.56);
  const xCenter = (i) => pad.l + (i + 0.5) * (iw / n);
  const yV = (v) => 16 + ih - (v / vMax) * ih;

  const ticks = $derived(niceTicks(0, vMax, AXIS_STEPS));
  const decimals = $derived(tickDecimals(niceStep(vMax, AXIS_STEPS)));

  /** Traza saltando los huecos. */
  function line(values) {
    let path = '';
    let drawing = false;
    values.forEach((value, index) => {
      if (!isNumber(value)) {
        drawing = false;
        return;
      }
      path += `${drawing ? 'L' : 'M'}${xCenter(index).toFixed(1)} ${yV(value).toFixed(1)} `;
      drawing = true;
    });
    return path.trim();
  }

  /** «NNE» a partir de los grados de origen. */
  const cardinalOf = (degrees) => cardinals[Math.round(((degrees % 360) / 22.5)) % 16];

  // --- Lectura al pasar el ratón -------------------------------------------
  let svg;
  let active = $state(null);
  const readValue = $derived(formatValue || formatTick);

  function pointerMove(event) {
    const rect = svg?.getBoundingClientRect();
    if (!rect?.width || !labels.length) return;
    const x = ((event.clientX - rect.left) / rect.width) * W;
    const slot = Math.floor(((x - pad.l) / iw) * n);
    active = Math.max(0, Math.min(n - 1, slot));
  }

  const reading = $derived.by(() => {
    if (active === null) return null;
    const entries = [];
    if (isNumber(mean[active])) {
      entries.push({ key: 'mean', color: 'var(--wind, #37c8d6)', text: `${readValue(mean[active])} ${unit}` });
    }
    if (isNumber(gust[active])) {
      entries.push({ key: 'gust', color: '#8fd8e2', text: `${readValue(gust[active])} ${unit}` });
    }
    if (isNumber(direction[active])) {
      const degrees = direction[active];
      entries.push({
        key: 'direction',
        color: 'var(--muted)',
        text: `${cardinalOf(degrees)} · ${Math.round(degrees)}°`
      });
    }
    return entries.length ? { label: labels[active] || '', entries } : null;
  });

  const boxWidth = $derived.by(() => {
    if (!reading) return 150;
    const name = Math.max(...reading.entries.map((entry) => seriesLabels[entry.key]?.length || 0));
    const value = Math.max(reading.label.length, ...reading.entries.map((entry) => entry.text.length));
    return Math.min(320, Math.max(150, 23 + name * 5.3 + 16 + value * 6.1 + 10));
  });

  const boxX = $derived.by(() => {
    if (active === null) return 0;
    const x = xCenter(active);
    const left = x > pad.l + iw / 2 ? x - boxWidth - 10 : x + 10;
    return Math.max(4, Math.min(W - boxWidth - 4, left));
  });
</script>

<ChartFrame name={exportName} label={exportLabel}>
<svg
  viewBox="0 0 {W} {H}"
  class="wind"
  role="img"
  aria-label={label}
  bind:this={svg}
  onpointermove={pointerMove}
  onpointerleave={() => (active = null)}
>
  {#each ticks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yV(tv)} y2={yV(tv)} stroke="var(--grid-line)" />
    <text x={pad.l - 8} y={yV(tv) + 3.5} class="axis" text-anchor="end">{formatTick(tv, decimals)}</text>
  {/each}
  <text
    class="axis-unit"
    transform="rotate(-90 11 {pad.t + ih / 2})"
    x="11" y={pad.t + ih / 2} text-anchor="middle">{unit}</text>

  <!-- Racha: es un máximo puntual, se lee mejor como barra que como línea. -->
  {#each gust as value, i}
    {#if isNumber(value)}
      <rect
        x={xCenter(i) - bw / 2} y={yV(value)}
        width={bw} height={Math.max(0, 16 + ih - yV(value))}
        rx="3" fill="var(--wind, #37c8d6)" opacity="0.28" />
    {/if}
  {/each}

  <path d={line(mean)} fill="none" stroke="var(--wind, #37c8d6)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
  {#if labels.length <= 14}
    {#each mean as value, i}
      {#if isNumber(value)}<circle cx={xCenter(i)} cy={yV(value)} r="2.6" fill="var(--wind, #37c8d6)" />{/if}
    {/each}
  {/if}

  <!-- Rumbo: una flecha por punto, apuntando hacia donde sopla. -->
  {#if hasArrows}
    {#each direction as degrees, i}
      {#if isNumber(degrees)}
        <g transform="translate({xCenter(i)}, {16 + ih + 13}) rotate({degrees})" class="arrow">
          <path d="M0 -6 L0 6 M0 6 L-3 1 M0 6 L3 1" />
        </g>
      {/if}
    {/each}
  {/if}

  {#each labels as item, i}
    {#if upright}
      <text
        x={xCenter(i)} y={16 + ih + arrowBand + 10}
        class="axis" text-anchor="end"
        transform="rotate(-90, {xCenter(i)}, {16 + ih + arrowBand + 10})">{item}</text>
    {:else}
      <text x={xCenter(i)} y={H - 9} class="axis" text-anchor="middle">{item}</text>
    {/if}
  {/each}

  {#if reading}
    <line x1={xCenter(active)} x2={xCenter(active)} y1={16} y2={16 + ih} stroke="var(--border-2)" stroke-width="1.4" />
    <g transform="translate({boxX}, {22})">
      <rect width={boxWidth} height={18 + reading.entries.length * 15} rx="8" class="box" />
      <text x="10" y="15" class="box-title">{reading.label}</text>
      {#each reading.entries as entry, index (entry.key)}
        <circle cx="14" cy={28 + index * 15} r="3.2" fill={entry.color} />
        <text x="23" y={31 + index * 15} class="box-label">{seriesLabels[entry.key]}</text>
        <text x={boxWidth - 10} y={31 + index * 15} class="box-value" text-anchor="end">{entry.text}</text>
      {/each}
    </g>
  {/if}

  <Watermark x={W - pad.r - 3} y={16 + ih - 6} />
</svg>
</ChartFrame>

<style>
  .wind { width: 100%; height: auto; display: block; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .axis-unit { fill: var(--muted-2); font-size: 9.5px; font-family: var(--mono); letter-spacing: 0.04em; }
  .arrow path { stroke: var(--muted); stroke-width: 1.6; fill: none; stroke-linecap: round; }
  .box { fill: var(--panel); stroke: var(--border-2); stroke-width: 1; }
  .box-title { fill: var(--ink); font-size: 10px; font-weight: 700; }
  .box-label { fill: var(--muted); font-size: 9.5px; }
  .box-value { fill: var(--ink-2); font-size: 9.5px; font-weight: 700; font-variant-numeric: tabular-nums; }
</style>

<script>
  import { nearestIndex } from '$lib/observation/cursor.js';
  import { niceStep, niceTicks, tickDecimals } from '$lib/observation/scale.js';
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  // Viento y rachas: viento medio + racha (eje izq., km/h) y dirección
  // como puntos en eje secundario derecho (0-360°). Réplica del chart real.
  let {
    labels = [], speed = [], gust = [], dir = [], height = 200,
    exportName = 'meteolabx-viento',
    exportLabel = 'Descargar PNG',
    // Instantes de cada punto y cursor compartido con el resto de gráficas
    // del día: al posarse en una, todas marcan el mismo momento.
    epochs = [],
    activeEpoch = null,
    onHover = null,
    seriesLabels = { speed: 'Viento', gust: 'Racha', dir: 'Dirección' },
    formatValue = (value) => value.toFixed(1),
    formatEpoch = null,
    // Qué series se pintan. Se apagan desde la leyenda: con tres magnitudes
    // encima, aislar una es la única forma de leerla.
    visible = { speed: true, gust: true, dir: true },
    unit = ''
  } = $props();

  const W = 620, H = height;
  const pad = { t: 14, r: 46, b: 26, l: 54 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  // Eje fijo de 24 h: lo que aún no ha ocurrido llega como `null`.
  const sMax = Math.max(...[...gust, ...speed].filter(isNumber), 0) * 1.12 || 1;
  const n = labels.length;
  const xOf = (i) => pad.l + (i / (n - 1)) * iw;
  const yS = (v) => pad.t + ih - (v / sMax) * ih;
  const yD = (v) => pad.t + ih - (v / 360) * ih;

  const activeIndex = $derived(nearestIndex(epochs, activeEpoch));

  let svg;

  /** Instante de la ranura señalada, o el de la más cercana con dato. */
  function nearestFilled(index) {
    for (let offset = 0; offset < epochs.length; offset += 1) {
      for (const candidate of [index - offset, index + offset]) {
        if (candidate < 0 || candidate >= epochs.length) continue;
        if (isNumber(epochs[candidate])) return epochs[candidate];
      }
    }
    return null;
  }

  function pointerMove(event) {
    if (!onHover || !svg || !epochs.length) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const x = ((event.clientX - rect.left) / rect.width) * W;
    const position = Math.round(((x - pad.l) / iw) * (epochs.length - 1));
    const epoch = nearestFilled(Math.max(0, Math.min(epochs.length - 1, position)));
    if (epoch !== null) onHover(epoch);
  }

  /** Lo que se lee en el punto señalado: las series que tengan dato ahí. */
  const reading = $derived.by(() => {
    if (activeIndex === null) return null;
    const entries = [
      { key: 'speed', color: 'var(--fam, #37c8d6)', value: speed[activeIndex], unit: unit ? ` ${unit}` : '' },
      { key: 'gust', color: '#8fd9e2', value: gust[activeIndex], unit: unit ? ` ${unit}` : '' },
      { key: 'dir', color: 'var(--muted)', value: dir[activeIndex], unit: '°', decimals: 0 }
    ].filter((entry) => isNumber(entry.value));
    const shown = entries.filter((entry) => visible[entry.key] !== false);
    if (!shown.length) return null;
    const when =
      (formatEpoch && isNumber(epochs[activeIndex]) ? formatEpoch(epochs[activeIndex]) : '') ||
      labels[activeIndex] ||
      '';
    return { label: when, entries: shown };
  });

  /**
   * Ancho de la caja, medido por su contenido.
   *
   * «Viento medio» y «Dirección» no ocupan lo mismo, ni lo mismo en cada
   * idioma: con ancho fijo la etiqueta se mete debajo del número.
   */
  const boxWidth = $derived.by(() => {
    if (!reading) return 150;
    const label = Math.max(
      ...reading.entries.map((entry) => (seriesLabels[entry.key] || '').length)
    );
    const value = Math.max(
      reading.label.length,
      ...reading.entries.map(
        (entry) => `${formatValue(entry.value, entry.decimals ?? 1)}${entry.unit}`.length
      )
    );
    return Math.min(320, Math.max(150, 23 + label * 5.3 + 16 + value * 6.1 + 10));
  });

  const boxX = $derived(
    activeIndex === null
      ? 0
      : Math.max(
          4,
          Math.min(
            W - boxWidth - 4,
            xOf(activeIndex) + (xOf(activeIndex) > pad.l + iw / 2 ? -boxWidth - 10 : 10)
          )
        )
  );

  /**
   * Ranuras vacías que la línea puede puentear.
   *
   * El eje del día son 96 ranuras de quince minutos y la estación publica cada
   * media hora: entre dos lecturas seguidas queda siempre una ranura vacía que
   * NO es un corte de datos. Cortando en cada una, la línea desaparece y solo
   * quedan los puntos sueltos.
   */
  function bridgeLimit(values) {
    const gaps = [];
    let previous = -1;
    values.forEach((value, index) => {
      if (!isNumber(value)) return;
      if (previous >= 0) gaps.push(index - previous);
      previous = index;
    });
    if (!gaps.length) return 1;
    gaps.sort((a, b) => a - b);
    return Math.max(1, gaps[Math.floor(gaps.length / 2)] * 2);
  }

  /** Traza uniendo lecturas seguidas y cortando en los huecos de verdad. */
  const line = (values) => {
    const bridge = bridgeLimit(values);
    let d = '';
    let previous = -1;
    values.forEach((value, index) => {
      if (!isNumber(value)) return;
      const command = previous >= 0 && index - previous <= bridge ? 'L' : 'M';
      d += `${command}${xOf(index).toFixed(1)} ${yS(value).toFixed(1)} `;
      previous = index;
    });
    return d.trim();
  };
  // Saltos redondos, los mismos que el resto de gráficas: repartir el máximo
  // en cuatro partes daba ejes con 9, 17 y 26 km/h.
  const AXIS_STEPS = 6;
  const sTicks = $derived(niceTicks(0, sMax, AXIS_STEPS));
  const sDecimals = $derived(tickDecimals(niceStep(sMax, AXIS_STEPS)));
  const dTicks = [0, 90, 180, 270, 360];
  const dLabels = { 0: 'N', 90: 'E', 180: 'S', 270: 'W', 360: 'N' };
  const firstGust = gust.findIndex(isNumber);
  const lastGust = gust.findLastIndex(isNumber);
</script>

<ChartFrame name={exportName} label={exportLabel}>
<svg
  viewBox="0 0 {W} {H}"
  class="wchart"
  bind:this={svg}
  onpointermove={pointerMove}
  onpointerleave={() => onHover?.(null)} role="img" aria-label="Viento y rachas">
  {#each sTicks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yS(tv)} y2={yS(tv)} stroke="var(--grid-line)" />
    <text x={pad.l - 7} y={yS(tv) + 3.5} class="axis" text-anchor="end">{tv.toFixed(sDecimals)}</text>
  {/each}
  {#each dTicks as dv}
    <text x={W - pad.r + 8} y={yD(dv) + 3.5} class="axis dir" text-anchor="start">{dLabels[dv]}</text>
  {/each}

  {#if unit}
    <text
      class="axis-unit"
      transform="rotate(-90 12 {pad.t + ih / 2})"
      x="12" y={pad.t + ih / 2} text-anchor="middle">{unit}</text>
  {/if}

  {#each labels as lb, i}
    {#if i % 2 === 0}<text x={xOf(i)} y={H - 8} class="axis" text-anchor="middle">{lb}</text>{/if}
  {/each}

  <!-- dirección: puntos en eje secundario -->
  {#each visible.dir === false ? [] : dir as d, i}
    {#if isNumber(d)}
      <circle cx={xOf(i)} cy={yD(d)} r="2.4" fill="var(--muted)" opacity="0.7" />
    {/if}
  {/each}

  <!-- racha (relleno) y viento -->
  {#if visible.gust !== false}
    <path d="{line(gust)} L{xOf(lastGust)} {yS(0)} L{xOf(firstGust)} {yS(0)} Z" fill="#37c8d6" opacity="0.08" />
    <path d={line(gust)} fill="none" stroke="#37c8d6" stroke-width="1.8" stroke-dasharray="4 3" opacity="0.85" stroke-linejoin="round" />
  {/if}
  {#if visible.speed !== false}
    <path d={line(speed)} fill="none" stroke="#2f7fd6" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
    {#each speed as v, i}{#if isNumber(v)}<circle cx={xOf(i)} cy={yS(v)} r="2" fill="#2f7fd6" />{/if}{/each}
  {/if}

  <!-- Lectura del punto señalado -->
  {#if reading}
    <line x1={xOf(activeIndex)} x2={xOf(activeIndex)} y1={pad.t} y2={pad.t + ih}
      stroke="var(--border-2)" stroke-width="1.4" />
    <g transform="translate({boxX}, {pad.t + 6})">
      <rect width={boxWidth} height={24 + reading.entries.length * 15} rx="8" class="box" />
      <text x="10" y="15" class="box-title">{reading.label}</text>
      {#each reading.entries as entry, index (entry.key)}
        <circle cx="14" cy={28 + index * 15} r="3.2" fill={entry.color} />
        <text x="23" y={31 + index * 15} class="box-label">{seriesLabels[entry.key]}</text>
        <text x={boxWidth - 10} y={31 + index * 15} class="box-value" text-anchor="end">
          {formatValue(entry.value, entry.decimals ?? 1)}{entry.unit}
        </text>
      {/each}
    </g>
  {/if}

  <Watermark x={W - pad.r - 3} y={pad.t + ih - 6} />
</svg>
</ChartFrame>

<style>
  .box { fill: var(--panel); stroke: var(--border-2); stroke-width: 1; }
  .box-title { fill: var(--ink); font-size: 10px; font-weight: 700; }
  .box-label { fill: var(--muted); font-size: 9.5px; }
  .box-value { fill: var(--ink-2); font-size: 9.5px; font-weight: 700; font-variant-numeric: tabular-nums; }

  .wchart { width: 100%; height: auto; display: block; }
  .axis-unit { fill: var(--muted-2); font-size: 9.5px; font-family: var(--mono); letter-spacing: 0.04em; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .axis.dir { fill: var(--muted-2); }
</style>

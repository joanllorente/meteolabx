<script>
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  import { nearestIndex } from '$lib/observation/cursor.js';
  import { niceStep, niceTicks, tickDecimals } from '$lib/observation/scale.js';
  // Gráfica de líneas con ejes, rejilla y relleno. Admite 1-2 series.
  // series: [{ data:[], color, label }]
  let {
    series = [],
    labels = [],
    height = 200,
    unit = '',
    zeroLine = false, // dibuja la línea y=0 (para tendencias con signo)
    fillArea = true,  // relleno bajo la línea cuando hay una sola serie
    // Formateo del eje Y. Por defecto punto decimal; la ficha le pasa el
    // de su idioma para que el eje no diga 21.2 mientras la tarjeta de al
    // lado dice 21,2.
    formatTick = (value) => value.toFixed(1),
    // Rango vertical impuesto `[min, max]`. Las tendencias fijan un suelo
    // simétrico para que una serie plana no se amplifique hasta parecer
    // una tormenta.
    range = null,
    // Instantes de cada punto y cursor compartido. Los cuatro gráficos de
    // tendencias se sincronizan por TIEMPO: cada uno descarta sus propios
    // huecos, así que el mismo índice no señala el mismo momento.
    epochs = [],
    activeEpoch = null,
    onHover = null,
    formatValue = (value) => value.toFixed(1),
    // Hora del punto señalado. Sin ella el valor no dice de cuándo es, y en
    // un eje de veinticuatro horas eso es la mitad de la información.
    formatEpoch = null,
    // Ancho del lienzo. Apilados a una columna los gráficos son mucho más
    // panorámicos, y con el viewBox estrecho el texto saldría gigante.
    width = 620,
    // Nombre del PNG que se descarga. Cada panel sabe de qué estación
    // habla; la gráfica, no.
    exportName = 'meteolabx-grafica',
    exportLabel = 'Descargar PNG'
  } = $props();

  const W = width;
  const H = height;
  const pad = { t: 14, r: 16, b: 26, l: 54 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  // Las series del día llegan sobre un eje fijo de 24 h: lo que aún no ha
  // ocurrido viaja como `null` y no puede entrar en la escala ni en el trazo.
  const all = $derived(series.flatMap((s) => s.data).filter(isNumber));
  let lo = $derived(Math.min(...all));
  let hi = $derived(Math.max(...all));
  const min = $derived(range ? range[0] : zeroLine ? Math.min(lo, 0) : lo - (hi - lo) * 0.08);
  const max = $derived(range ? range[1] : zeroLine ? Math.max(hi, 0) : hi + (hi - lo) * 0.08);
  const span = $derived(max - min || 1);

  const xOf = (i, n) => pad.l + (i / (n - 1)) * iw;
  const yOf = (v) => pad.t + ih - ((v - min) / span) * ih;

  // Seis tramos en vez de cuatro: con cuatro, un gráfico de diez grados
  // salía con líneas cada cinco y no se podía leer un valor de un vistazo.
  const AXIS_STEPS = 6;
  const ticks = $derived(niceTicks(min, min + span, AXIS_STEPS));
  const decimals = $derived(tickDecimals(niceStep(span, AXIS_STEPS)));

  const activeIndex = $derived(nearestIndex(epochs, activeEpoch));

  let svg;

  /** Instante de la ranura señalada, o el de la que tenga dato más cerca. */
  function nearestFilled(index) {
    for (let offset = 0; offset < epochs.length; offset += 1) {
      for (const candidate of [index - offset, index + offset]) {
        if (candidate < 0 || candidate >= epochs.length) continue;
        if (isNumber(epochs[candidate])) return epochs[candidate];
      }
    }
    return null;
  }

  /** Traduce la posición del ratón al instante del punto más cercano. */
  function pointerMove(event) {
    if (!onHover || !svg || !epochs.length) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    // El SVG se escala al ancho del contenedor: hay que volver a sus unidades.
    const x = ((event.clientX - rect.left) / rect.width) * W;
    const position = Math.round(((x - pad.l) / iw) * (epochs.length - 1));
    const index = Math.max(0, Math.min(epochs.length - 1, position));
    const epoch = nearestFilled(index);
    if (epoch !== null) onHover(epoch);
  }

  /** Traza saltando los huecos: cada tramo continuo arranca con su propio M. */
  function path(data) {
    let d = '';
    let drawing = false;
    const bridge = bridgeLimit(data);
    let previous = -1;
    data.forEach((value, index) => {
      if (!isNumber(value)) return;
      // Se une con el punto anterior salvo que el hueco sea mayor que lo que
      // esta serie tarda normalmente entre lectura y lectura.
      const command = drawing && index - previous <= bridge ? 'L' : 'M';
      d += `${command}${xOf(index, data.length).toFixed(1)} ${yOf(value).toFixed(1)} `;
      drawing = true;
      previous = index;
    });
    return d.trim();
  }

  /**
   * Ranuras vacías que la línea puede puentear.
   *
   * El eje del día son 96 ranuras de quince minutos, pero una tendencia se
   * publica cada veinte o cada tres horas: entre dos lecturas seguidas quedan
   * ranuras vacías que NO son un corte de datos. Cortando en cada una, la
   * gráfica se convierte en una nube de puntos sueltos. El umbral sale de la
   * propia serie —el doble de su paso habitual—, así que un sensor caído de
   * verdad sigue partiendo la línea.
   */
  function bridgeLimit(data) {
    const gaps = [];
    let previous = -1;
    data.forEach((value, index) => {
      if (!isNumber(value)) return;
      if (previous >= 0) gaps.push(index - previous);
      previous = index;
    });
    if (!gaps.length) return 1;
    gaps.sort((a, b) => a - b);
    return Math.max(1, gaps[Math.floor(gaps.length / 2)] * 2);
  }
  function areaPath(data) {
    const last = data.findLastIndex(isNumber);
    const first = data.findIndex(isNumber);
    if (last < 0) return '';
    return (
      `${path(data)} L${xOf(last, data.length).toFixed(1)} ${yOf(min)} ` +
      `L${xOf(first, data.length).toFixed(1)} ${yOf(min)} Z`
    );
  }
</script>

<ChartFrame name={exportName} label={exportLabel}>
<svg
  viewBox="0 0 {W} {H}"
  class="chart"
  role="img"
  bind:this={svg}
  onpointermove={pointerMove}
  onpointerleave={() => onHover?.(null)}
>
  <!-- rejilla + eje Y -->
  {#each ticks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yOf(tv)} y2={yOf(tv)} stroke="var(--grid-line)" stroke-width="1" />
    <text x={pad.l - 8} y={yOf(tv) + 3.5} class="axis" text-anchor="end">{tv === 0 ? formatTick(0, 0) : formatTick(tv, decimals)}</text>
  {/each}
  {#if zeroLine && min < 0 && max > 0}
    <line x1={pad.l} x2={W - pad.r} y1={yOf(0)} y2={yOf(0)} stroke="var(--border-2)" stroke-width="1.2" stroke-dasharray="3 3" />
  {/if}

  <!-- Unidad del eje, girada a la izquierda de los números: así ocupa el
       margen que ya existe y no roba altura al dibujo. -->
  {#if unit}
    <text
      class="axis-unit"
      transform="rotate(-90 12 {pad.t + ih / 2})"
      x="12" y={pad.t + ih / 2} text-anchor="middle">{unit}</text>
  {/if}

  <!-- eje X -->
  {#each labels as lb, i}
    {#if i % 2 === 0}
      <text x={xOf(i, labels.length)} y={H - 8} class="axis" text-anchor="middle">{lb}</text>
    {/if}
  {/each}

  <!-- cursor compartido -->
  {#if activeIndex !== null}
    <line
      x1={xOf(activeIndex, epochs.length)} x2={xOf(activeIndex, epochs.length)}
      y1={pad.t} y2={pad.t + ih}
      stroke="var(--border-2)" stroke-width="1.4" />
    {#if formatEpoch && isNumber(epochs[activeIndex])}
      <text
        x={Math.max(pad.l + 16, Math.min(W - pad.r - 16, xOf(activeIndex, epochs.length)))}
        y={pad.t + 9}
        class="cursor-time" text-anchor="middle">{formatEpoch(epochs[activeIndex])}</text>
    {/if}
  {/if}

  <!-- series -->
  {#each series as s}
    {#if series.length === 1 && fillArea}
      <path d={areaPath(s.data)} fill={s.color} opacity="0.1" />
    {/if}
    <path
      d={path(s.data)} fill="none" stroke={s.color} stroke-width="2.2"
      stroke-linecap="round" stroke-linejoin="round"
      stroke-dasharray={s.dash ? '5 4' : 'none'} opacity={s.dash ? 0.75 : 1}
    />
    {#if !s.dash}
      {#each s.data as v, i}
        {#if isNumber(v)}
          <circle cx={xOf(i, s.data.length)} cy={yOf(v)} r="2" fill={s.color} opacity="0.9" />
        {/if}
      {/each}
    {/if}
  {/each}

  <!-- Lectura bajo el cursor: un punto marcado y su valor por serie. -->
  {#if activeIndex !== null}
    {#each series as s, index (index)}
      {#if isNumber(s.data[activeIndex])}
        <circle
          cx={xOf(activeIndex, s.data.length)}
          cy={yOf(s.data[activeIndex])}
          r="3.6" fill={s.color} stroke="var(--panel)" stroke-width="1.5" />
        <text
          x={xOf(activeIndex, s.data.length) + (activeIndex > epochs.length / 2 ? -8 : 8)}
          y={yOf(s.data[activeIndex]) - 8}
          class="readout"
          text-anchor={activeIndex > epochs.length / 2 ? 'end' : 'start'}
          fill={s.color}>{formatValue(s.data[activeIndex])}</text>
      {/if}
    {/each}
  {/if}

  <Watermark x={W - pad.r - 3} y={pad.t + ih - 6} />
</svg>
</ChartFrame>

<style>
  .chart { width: 100%; height: auto; display: block; }
  .axis-unit { fill: var(--muted-2); font-size: 9.5px; font-family: var(--mono); letter-spacing: 0.04em; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .cursor-time { fill: var(--ink-2); font-size: 10px; font-weight: 700; font-family: var(--mono); paint-order: stroke; stroke: var(--panel); stroke-width: 3px; }
  .readout { font-size: 11px; font-weight: 700; font-family: var(--font); paint-order: stroke; stroke: var(--panel); stroke-width: 3px; }
</style>

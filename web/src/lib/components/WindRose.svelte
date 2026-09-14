<script>
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  // Rosa de viento de 16 sectores. data: [{ dir, pct, bands? }]
  //
  // Con `bands` (el porcentaje de cada tramo de intensidad dentro del sector)
  // y su leyenda en `bandLegend`, cada cuña se apila por intensidades desde
  // el centro: lo flojo dentro, lo fuerte fuera, como en una rosa clásica.
  let {
    data = [],
    bandLegend = [],
    size = 240,
    color = 'var(--wind, #37c8d6)',
    // En castellano el oeste es O, no W. Las letras llegan desde fuera
    // porque la rosa se publica en los seis idiomas del sitio.
    cardinals = ['N', 'E', 'S', 'W'],
    frequencyLabel = '',
    formatPct = (value) => `${Math.round(value)} %`,
    exportName = 'meteolabx-rosa-de-viento',
    exportLabel = 'Descargar PNG'
  } = $props();

  const cx = $derived(size / 2);
  const cy = $derived(size / 2);
  const rMax = $derived(size / 2 - 26);
  const maxPct = $derived(Math.max(...data.map((d) => d.pct)) || 1);

  const radius = (pct) => 10 + (pct / maxPct) * (rMax - 10);

  // Sector i centrado en su rumbo; 16 sectores de 22.5°. Sin `from`, la cuña
  // sale del centro; con él, es el tramo de corona entre dos porcentajes.
  function wedge(i, pct, from = 0) {
    const r = radius(pct);
    const r0 = radius(from);
    const half = (Math.PI / 16) * 0.72; // hueco entre cuñas
    const a = (i / 16) * 2 * Math.PI - Math.PI / 2; // N arriba
    const a0 = a - half;
    const a1 = a + half;
    const x0 = cx + Math.cos(a0) * r0, y0 = cy + Math.sin(a0) * r0;
    const x1 = cx + Math.cos(a0) * r, y1 = cy + Math.sin(a0) * r;
    const x2 = cx + Math.cos(a1) * r, y2 = cy + Math.sin(a1) * r;
    const x3 = cx + Math.cos(a1) * r0, y3 = cy + Math.sin(a1) * r0;
    return `M${x0} ${y0} L${x1} ${y1} A${r} ${r} 0 0 1 ${x2} ${y2} L${x3} ${y3} A${r0} ${r0} 0 0 0 ${x0} ${y0} Z`;
  }

  const stacked = $derived(bandLegend.length > 0 && data.some((d) => Array.isArray(d.bands)));

  /** Tramos visibles de un sector: [{ from, to, color }], del centro hacia fuera. */
  function segments(d) {
    const out = [];
    let cumulative = 0;
    (d.bands || []).forEach((pct, index) => {
      if (!(pct > 0)) return;
      const fill = bandLegend.find((band) => band.index === index)?.color || color;
      out.push({ from: cumulative, to: cumulative + pct, color: fill });
      cumulative += pct;
    });
    return out;
  }
  const rings = [0.33, 0.66, 1];
  const marks = $derived(
    cardinals.map((letter, index) => ({ l: letter, a: index * 90 }))
  );
  function labelPos(a) {
    const rad = (a * Math.PI) / 180 - Math.PI / 2;
    return { x: cx + Math.cos(rad) * (rMax + 14), y: cy + Math.sin(rad) * (rMax + 14) + 4 };
  }

  /**
   * Sector bajo el puntero.
   *
   * Se busca por el ángulo respecto al centro, no por el trazo de cada cuña:
   * las de poca frecuencia son astillas de pocos píxeles y habría que
   * clavarles el ratón encima. Así responde todo el sector, tenga el tamaño
   * que tenga, que es lo que se quiere leer.
   */
  let active = $state(null);

  function onMove(event) {
    // El lienzo es el que recibe el gesto: la rosa se dibuja dos veces cuando
    // el visor a pantalla completa está abierto, y una referencia guardada
    // apuntaría a la copia equivocada.
    const svg = event.currentTarget;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const x = ((event.clientX - rect.left) / rect.width) * size;
    const y = ((event.clientY - rect.top) / rect.height) * size;
    const dx = x - cx;
    const dy = y - cy;
    const radius = Math.hypot(dx, dy);
    // Fuera de la rosa —y en el ojo del centro, donde no hay sector que
    // valga— no se lee nada.
    if (radius < 9 || radius > rMax + 14) {
      active = null;
      return;
    }
    const degrees = (Math.atan2(dy, dx) * 180) / Math.PI + 90;
    active = Math.round((((degrees % 360) + 360) % 360) / 22.5) % 16;
  }

  const reading = $derived(active === null ? null : data[active] || null);
  // Desglose del sector bajo el puntero, solo con las bandas que soplaron.
  const breakdown = $derived(
    reading && stacked
      ? bandLegend
          .map((band) => ({ ...band, pct: reading.bands?.[band.index] || 0 }))
          .filter((band) => band.pct > 0)
      : []
  );
  // Ancho por contenido: una caja fija dejaría el rumbo apretado en «NNO» y
  // sobrada en «N».
  const boxWidth = $derived.by(() => {
    if (!reading) return 0;
    const head = String(reading.dir || '').length * 7 + 20;
    const line = String(frequencyLabel).length * 5.4 + String(formatPct(reading.pct)).length * 6.4 + 26;
    const bandLine = Math.max(0, ...breakdown.map((band) =>
      String(band.label).length * 5.4 + String(formatPct(band.pct)).length * 6.4 + 38
    ));
    return Math.max(74, head, line, bandLine);
  });
</script>

<ChartFrame name={exportName} label={exportLabel} wide={false}>
<svg
  class="rose"
  viewBox="0 0 {size} {size}"
  aria-label="Rosa de viento"
  role="img"
  onpointermove={onMove}
  onpointerleave={() => (active = null)}
>
  {#each rings as rr}
    <circle {cx} {cy} r={10 + rr * (rMax - 10)} fill="none" stroke="var(--grid-line)" stroke-width="1" />
  {/each}
  {#each data as d, i}
    {#if stacked}
      <!-- Con intensidades el color ya dice cuánto sopla: la opacidad por
           frecuencia de la rosa lisa lo enturbiaría. -->
      {#each segments(d) as segment}
        <path
          d={wedge(i, segment.to, segment.from)}
          fill={segment.color}
          opacity={active === null || active === i ? 0.92 : 0.5}
          stroke="var(--panel)"
          stroke-width={active === i ? 1 : 0.5}
        />
      {/each}
    {:else}
      <path
        d={wedge(i, d.pct)}
        fill={color}
        opacity={active === i ? 1 : 0.35 + 0.6 * (d.pct / maxPct)}
        stroke={active === i ? 'var(--panel)' : 'none'}
        stroke-width="1"
      />
    {/if}
  {/each}
  {#each marks as c}
    <text x={labelPos(c.a).x} y={labelPos(c.a).y} text-anchor="middle" class="card">{c.l}</text>
  {/each}

  {#if reading}
    <g transform="translate(2, 2)">
      <rect width={boxWidth} height={40 + breakdown.length * 13} rx="8" class="box" />
      <text x="10" y="16" class="box-title">{reading.dir}</text>
      <text x="10" y="31" class="box-label">{frequencyLabel}</text>
      <text x={boxWidth - 10} y="31" class="box-value" text-anchor="end">{formatPct(reading.pct)}</text>
      {#each breakdown as band, row}
        <circle cx="13" cy={41 + row * 13} r="3" fill={band.color} />
        <text x="21" y={44 + row * 13} class="box-label">{band.label}</text>
        <text x={boxWidth - 10} y={44 + row * 13} class="box-value" text-anchor="end">{formatPct(band.pct)}</text>
      {/each}
    </g>
  {/if}

  <Watermark x={size / 2} y={size - 4} anchor="middle" />
</svg>
</ChartFrame>

<style>
  .rose { width: 100%; height: auto; display: block; touch-action: none; }
  .card { fill: var(--ink-2); font-size: 12px; font-weight: 600; font-family: var(--font); }
  .box { fill: var(--panel); stroke: var(--border-2); stroke-width: 1; }
  .box-title { fill: var(--ink); font-size: 11px; font-weight: 700; font-family: var(--font); }
  .box-label { fill: var(--muted); font-size: 9.5px; font-family: var(--font); }
  .box-value { fill: var(--ink-2); font-size: 9.5px; font-weight: 700; font-family: var(--font); font-variant-numeric: tabular-nums; }
</style>

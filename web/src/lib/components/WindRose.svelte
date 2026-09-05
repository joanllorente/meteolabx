<script>
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  // Rosa de viento de 16 sectores. data: [{ dir, pct }]
  let {
    data = [],
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

  // Sector i centrado en su rumbo; 16 sectores de 22.5°.
  function wedge(i, pct) {
    const r = 10 + (pct / maxPct) * (rMax - 10);
    const half = (Math.PI / 16) * 0.72; // hueco entre cuñas
    const a = (i / 16) * 2 * Math.PI - Math.PI / 2; // N arriba
    const a0 = a - half;
    const a1 = a + half;
    const x0 = cx + Math.cos(a0) * 10, y0 = cy + Math.sin(a0) * 10;
    const x1 = cx + Math.cos(a0) * r, y1 = cy + Math.sin(a0) * r;
    const x2 = cx + Math.cos(a1) * r, y2 = cy + Math.sin(a1) * r;
    const x3 = cx + Math.cos(a1) * 10, y3 = cy + Math.sin(a1) * 10;
    return `M${x0} ${y0} L${x1} ${y1} A${r} ${r} 0 0 1 ${x2} ${y2} L${x3} ${y3} A10 10 0 0 0 ${x0} ${y0} Z`;
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
  let svg;
  let active = $state(null);

  function onMove(event) {
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
  // Ancho por contenido: una caja fija dejaría el rumbo apretado en «NNO» y
  // sobrada en «N».
  const boxWidth = $derived.by(() => {
    if (!reading) return 0;
    const head = String(reading.dir || '').length * 7 + 20;
    const line = String(frequencyLabel).length * 5.4 + String(formatPct(reading.pct)).length * 6.4 + 26;
    return Math.max(74, head, line);
  });
</script>

<ChartFrame name={exportName} label={exportLabel}>
<svg
  bind:this={svg}
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
    <path
      d={wedge(i, d.pct)}
      fill={color}
      opacity={active === i ? 1 : 0.35 + 0.6 * (d.pct / maxPct)}
      stroke={active === i ? 'var(--panel)' : 'none'}
      stroke-width="1"
    />
  {/each}
  {#each marks as c}
    <text x={labelPos(c.a).x} y={labelPos(c.a).y} text-anchor="middle" class="card">{c.l}</text>
  {/each}

  {#if reading}
    <g transform="translate(2, 2)">
      <rect width={boxWidth} height="40" rx="8" class="box" />
      <text x="10" y="16" class="box-title">{reading.dir}</text>
      <text x="10" y="31" class="box-label">{frequencyLabel}</text>
      <text x={boxWidth - 10} y="31" class="box-value" text-anchor="end">{formatPct(reading.pct)}</text>
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

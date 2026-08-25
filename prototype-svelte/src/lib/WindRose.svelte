<script>
  // Rosa de viento de 16 sectores. data: [{ dir, pct }]
  let { data = [], size = 240, color = 'var(--wind, #37c8d6)' } = $props();

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
  const cardinals = [
    { l: 'N', a: 0 }, { l: 'E', a: 90 }, { l: 'S', a: 180 }, { l: 'W', a: 270 }
  ];
  function labelPos(a) {
    const rad = (a * Math.PI) / 180 - Math.PI / 2;
    return { x: cx + Math.cos(rad) * (rMax + 14), y: cy + Math.sin(rad) * (rMax + 14) + 4 };
  }
</script>

<svg viewBox="0 0 {size} {size}" width={size} height={size} aria-label="Rosa de viento">
  {#each rings as rr}
    <circle {cx} {cy} r={10 + rr * (rMax - 10)} fill="none" stroke="var(--grid-line)" stroke-width="1" />
  {/each}
  {#each data as d, i}
    <path d={wedge(i, d.pct)} fill={color} opacity={0.35 + 0.6 * (d.pct / maxPct)} />
  {/each}
  {#each cardinals as c}
    <text x={labelPos(c.a).x} y={labelPos(c.a).y} text-anchor="middle" class="card">{c.l}</text>
  {/each}
</svg>

<style>
  .card { fill: var(--ink-2); font-size: 12px; font-weight: 600; font-family: var(--font); }
</style>

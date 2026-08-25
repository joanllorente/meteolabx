<script>
  // Gráfica de líneas con ejes, rejilla y relleno. Admite 1-2 series.
  // series: [{ data:[], color, label }]
  let {
    series = [],
    labels = [],
    height = 200,
    unit = '',
    zeroLine = false, // dibuja la línea y=0 (para tendencias con signo)
    nowIndex = null,  // índice donde pintar la línea vertical de "ahora"
    fillArea = true   // relleno bajo la línea cuando hay una sola serie
  } = $props();

  const W = 620;
  const H = height;
  const pad = { t: 14, r: 16, b: 26, l: 42 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const all = $derived(series.flatMap((s) => s.data));
  let lo = $derived(Math.min(...all));
  let hi = $derived(Math.max(...all));
  const min = $derived(zeroLine ? Math.min(lo, 0) : lo - (hi - lo) * 0.08);
  const max = $derived(zeroLine ? Math.max(hi, 0) : hi + (hi - lo) * 0.08);
  const span = $derived(max - min || 1);

  const xOf = (i, n) => pad.l + (i / (n - 1)) * iw;
  const yOf = (v) => pad.t + ih - ((v - min) / span) * ih;

  const ticks = $derived(Array.from({ length: 4 }, (_, i) => min + (span * i) / 3));

  function path(data) {
    return data.map((v, i) => `${i ? 'L' : 'M'}${xOf(i, data.length).toFixed(1)} ${yOf(v).toFixed(1)}`).join(' ');
  }
  function areaPath(data) {
    return `${path(data)} L${xOf(data.length - 1, data.length).toFixed(1)} ${yOf(min)} L${pad.l} ${yOf(min)} Z`;
  }
</script>

<svg viewBox="0 0 {W} {H}" class="chart" role="img">
  <!-- rejilla + eje Y -->
  {#each ticks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yOf(tv)} y2={yOf(tv)} stroke="var(--grid-line)" stroke-width="1" />
    <text x={pad.l - 8} y={yOf(tv) + 3.5} class="axis" text-anchor="end">{tv.toFixed(1)}</text>
  {/each}
  {#if zeroLine && min < 0 && max > 0}
    <line x1={pad.l} x2={W - pad.r} y1={yOf(0)} y2={yOf(0)} stroke="var(--border-2)" stroke-width="1.2" stroke-dasharray="3 3" />
  {/if}

  <!-- eje X -->
  {#each labels as lb, i}
    {#if i % 2 === 0}
      <text x={xOf(i, labels.length)} y={H - 8} class="axis" text-anchor="middle">{lb}</text>
    {/if}
  {/each}

  <!-- línea de "ahora" -->
  {#if nowIndex != null && labels.length}
    <line x1={xOf(nowIndex, labels.length)} x2={xOf(nowIndex, labels.length)} y1={pad.t} y2={pad.t + ih} stroke="var(--border-2)" stroke-width="1.2" stroke-dasharray="2 3" />
    <text x={xOf(nowIndex, labels.length)} y={pad.t + 8} class="now" text-anchor="middle">ahora</text>
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
        <circle cx={xOf(i, s.data.length)} cy={yOf(v)} r="2" fill={s.color} opacity="0.9" />
      {/each}
    {/if}
  {/each}
</svg>

<style>
  .chart { width: 100%; height: auto; display: block; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .now { fill: var(--muted); font-size: 9px; font-family: var(--mono); }
</style>

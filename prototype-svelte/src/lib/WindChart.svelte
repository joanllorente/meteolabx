<script>
  // Viento y rachas: viento medio + racha (eje izq., km/h) y dirección
  // como puntos en eje secundario derecho (0-360°). Réplica del chart real.
  let { labels = [], speed = [], gust = [], dir = [], height = 200 } = $props();

  const W = 620, H = height;
  const pad = { t: 14, r: 46, b: 26, l: 42 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const sMax = Math.max(...gust, ...speed) * 1.12 || 1;
  const n = labels.length;
  const xOf = (i) => pad.l + (i / (n - 1)) * iw;
  const yS = (v) => pad.t + ih - (v / sMax) * ih;
  const yD = (v) => pad.t + ih - (v / 360) * ih;

  const line = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${xOf(i).toFixed(1)} ${yS(v).toFixed(1)}`).join(' ');
  const sTicks = Array.from({ length: 4 }, (_, i) => Math.round((sMax * i) / 3));
  const dTicks = [0, 90, 180, 270, 360];
  const dLabels = { 0: 'N', 90: 'E', 180: 'S', 270: 'W', 360: 'N' };
</script>

<svg viewBox="0 0 {W} {H}" class="wchart" role="img" aria-label="Viento y rachas">
  {#each sTicks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yS(tv)} y2={yS(tv)} stroke="var(--grid-line)" />
    <text x={pad.l - 7} y={yS(tv) + 3.5} class="axis" text-anchor="end">{tv}</text>
  {/each}
  {#each dTicks as dv}
    <text x={W - pad.r + 8} y={yD(dv) + 3.5} class="axis dir" text-anchor="start">{dLabels[dv]}</text>
  {/each}

  {#each labels as lb, i}
    {#if i % 2 === 0}<text x={xOf(i)} y={H - 8} class="axis" text-anchor="middle">{lb}</text>{/if}
  {/each}

  <!-- dirección: puntos en eje secundario -->
  {#each dir as d, i}
    <circle cx={xOf(i)} cy={yD(d)} r="2.4" fill="var(--muted)" opacity="0.7" />
  {/each}

  <!-- racha (relleno) y viento -->
  <path d="{line(gust)} L{xOf(n - 1)} {yS(0)} L{pad.l} {yS(0)} Z" fill="#37c8d6" opacity="0.08" />
  <path d={line(gust)} fill="none" stroke="#37c8d6" stroke-width="1.8" stroke-dasharray="4 3" opacity="0.85" stroke-linejoin="round" />
  <path d={line(speed)} fill="none" stroke="#2f7fd6" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
  {#each speed as v, i}<circle cx={xOf(i)} cy={yS(v)} r="2" fill="#2f7fd6" />{/each}
</svg>

<style>
  .wchart { width: 100%; height: auto; display: block; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .axis.dir { fill: var(--muted-2); }
</style>

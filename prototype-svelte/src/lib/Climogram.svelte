<script>
  // Climograma: barras de precipitación (eje der.) + líneas Tmáx/Tmín (eje izq.).
  let { months = [], tmax = [], tmin = [], precip = [] } = $props();

  const W = 640, H = 260;
  const pad = { t: 16, r: 44, b: 30, l: 40 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const tLo = Math.min(...tmin) - 3;
  const tHi = Math.max(...tmax) + 3;
  const pMax = Math.max(...precip) * 1.15 || 1;

  const n = months.length;
  const bw = (iw / n) * 0.56;
  const xCenter = (i) => pad.l + (i + 0.5) * (iw / n);
  const yT = (v) => pad.t + ih - ((v - tLo) / (tHi - tLo)) * ih;
  const yP = (v) => pad.t + ih - (v / pMax) * ih;

  const tPath = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${xCenter(i).toFixed(1)} ${yT(v).toFixed(1)}`).join(' ');
  const tTicks = Array.from({ length: 4 }, (_, i) => Math.round(tLo + ((tHi - tLo) * i) / 3));
</script>

<svg viewBox="0 0 {W} {H}" class="climo" role="img" aria-label="Climograma anual">
  {#each tTicks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yT(tv)} y2={yT(tv)} stroke="var(--grid-line)" />
    <text x={pad.l - 7} y={yT(tv) + 3.5} class="axis" text-anchor="end">{tv}°</text>
  {/each}

  {#each precip as p, i}
    <rect x={xCenter(i) - bw / 2} y={yP(p)} width={bw} height={pad.t + ih - yP(p)} rx="3" fill="var(--precip, #5b9bff)" opacity="0.5" />
  {/each}

  <path d={tPath(tmax)} fill="none" stroke="#ff8a4c" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
  <path d={tPath(tmin)} fill="none" stroke="#4db6e8" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
  {#each tmax as v, i}<circle cx={xCenter(i)} cy={yT(v)} r="2.6" fill="#ff8a4c" />{/each}
  {#each tmin as v, i}<circle cx={xCenter(i)} cy={yT(v)} r="2.6" fill="#4db6e8" />{/each}

  {#each months as m, i}
    <text x={xCenter(i)} y={H - 9} class="axis" text-anchor="middle">{m}</text>
  {/each}
</svg>

<style>
  .climo { width: 100%; height: auto; display: block; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
</style>

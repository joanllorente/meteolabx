<script>
  import Icon from '../lib/Icon.svelte';
  import { ranking, rankingMetrics, providerColors } from '../data.js';

  let metric = $state('tmax');
  let scope = $state('country'); // country | global
  const current = $derived(rankingMetrics.find((m) => m.id === metric));
  const rows = $derived(ranking[metric]);
  const maxV = $derived(Math.max(...rows.map((r) => r.v)));
</script>

<div class="rank-head">
  <div>
    <h2>Ranking de estaciones</h2>
    <p>Top 10 del día por temperatura máx./mín., racha de viento y lluvia</p>
  </div>
  <div class="scope-seg">
    <button class:active={scope === 'country'} type="button" onclick={() => (scope = 'country')}>Tu país: España</button>
    <button class:active={scope === 'global'} type="button" onclick={() => (scope = 'global')}>Global</button>
  </div>
</div>

<div class="metric-tabs">
  {#each rankingMetrics as m}
    <button class:active={metric === m.id} type="button" onclick={() => (metric = m.id)}>
      <Icon name={m.icon} size={15} />{m.label}
    </button>
  {/each}
</div>

<section class="rank-card">
  <div class="rank-caption">
    <span>{current.label} · {scope === 'country' ? 'España' : 'Global'}</span>
    <span class="prov-note">Proveedores: AEMET, Meteocat, Euskalmet, MeteoGalicia</span>
  </div>

  <ol class="rank-list">
    {#each rows as r, i}
      <li class:podium={i < 3}>
        <span class="pos">{i + 1}</span>
        <div class="who">
          <strong>{r.name}</strong>
          <small>{r.place}</small>
        </div>
        <span class="prov" style:--pc={providerColors[r.provider] || '#888'}>{r.provider}</span>
        <div class="bar-wrap">
          <div class="bar" style:width="{(r.v / maxV) * 100}%"></div>
        </div>
        <span class="val tnum">{r.v}<i>{current.unit}</i></span>
      </li>
    {/each}
  </ol>
</section>

<style>
  .rank-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .rank-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .rank-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted); }

  .scope-seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .scope-seg button { padding: 7px 14px; border: 0; border-radius: 7px; font-size: 0.74rem; font-weight: 600; color: var(--muted); background: transparent; }
  .scope-seg button.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }

  .metric-tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .metric-tabs button { display: inline-flex; align-items: center; gap: 7px; padding: 9px 14px; border: 1px solid var(--border); border-radius: 10px; font-size: 0.78rem; font-weight: 600; color: var(--muted); background: var(--panel); }
  .metric-tabs button:hover { color: var(--ink-2); border-color: var(--border-2); }
  .metric-tabs button.active { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }

  .rank-card { padding: 18px 20px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .rank-caption { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; flex-wrap: wrap; gap: 6px; }
  .rank-caption > span:first-child { font-size: 0.82rem; font-weight: 640; }
  .prov-note { font-size: 0.68rem; color: var(--muted); }

  .rank-list { list-style: none; margin: 0; padding: 0; }
  .rank-list li { display: grid; grid-template-columns: 30px minmax(120px, 1.4fr) auto minmax(80px, 1fr) auto; align-items: center; gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--border); }
  .rank-list li:last-child { border-bottom: 0; }

  .pos { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px; font-size: 0.76rem; font-weight: 700; color: var(--muted); background: var(--panel-2); }
  .podium .pos { color: var(--accent-ink); background: var(--accent); }
  .rank-list li:nth-child(2) .pos { background: #c9d2dc; color: #10151d; }
  .rank-list li:nth-child(3) .pos { background: #d99a5b; color: #10151d; }

  .who { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .who strong { font-size: 0.84rem; font-weight: 640; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who small { font-size: 0.68rem; color: var(--muted); }

  .prov { padding: 3px 8px; border-radius: 6px; font-size: 0.62rem; font-weight: 700; color: var(--pc); background: color-mix(in srgb, var(--pc) 15%, transparent); white-space: nowrap; }

  .bar-wrap { height: 7px; border-radius: 999px; background: var(--panel-2); overflow: hidden; }
  .bar { height: 100%; border-radius: 999px; background: linear-gradient(90deg, color-mix(in srgb, var(--accent) 55%, transparent), var(--accent)); }

  .val { font-size: 0.92rem; font-weight: 700; text-align: right; white-space: nowrap; }
  .val i { font-size: 0.66rem; font-weight: 600; color: var(--muted); font-style: normal; margin-left: 2px; }

  @media (max-width: 680px) {
    .rank-list li { grid-template-columns: 26px 1fr auto; grid-auto-flow: row; }
    .bar-wrap { display: none; }
  }
</style>

<script>
  import {
    LayoutDashboard, TrendingUp, History, Map, Trophy,
    RefreshCw, Sun, Moon, ChevronDown, Radio
  } from '@lucide/svelte';
  import ObservationView from './views/ObservationView.svelte';
  import TrendsView from './views/TrendsView.svelte';
  import HistoricalView from './views/HistoricalView.svelte';
  import MapView from './views/MapView.svelte';
  import RankingView from './views/RankingView.svelte';
  import Isobars from './lib/Isobars.svelte';
  import { station } from './data.js';

  const tabs = [
    { id: 'observation', label: 'Observación', icon: LayoutDashboard },
    { id: 'trends', label: 'Tendencias', icon: TrendingUp },
    { id: 'historical', label: 'Histórico', icon: History },
    { id: 'map', label: 'Mapa', icon: Map },
    { id: 'ranking', label: 'Ranking', icon: Trophy }
  ];

  let active = $state('observation');
  let theme = $state(localStorage.getItem('mlx-proto-theme') || 'dark');
  const current = $derived(tabs.find((t) => t.id === active));

  $effect(() => localStorage.setItem('mlx-proto-theme', theme));

  function go(id) {
    active = id;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
</script>

<div class="shell theme-{theme}">
  <!-- ── BARRA SUPERIOR ── -->
  <header class="topnav">
    <a class="brand" href="#top" aria-label="MeteoLabx">
      <img src="/mlx-logo.png" alt="MeteoLabx" />
      <span class="brand-txt">
        <strong>MeteoLabx</strong>
        <small>v1.3.1 · prototipo</small>
      </span>
    </a>

    <nav class="tabs" aria-label="Secciones">
      {#each tabs as t}
        <button class:active={active === t.id} type="button" onclick={() => go(t.id)}>
          <t.icon size={16} /><span>{t.label}</span>
        </button>
      {/each}
    </nav>

    <div class="right">
      <button class="theme" type="button" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')} aria-label="Cambiar tema">
        {#if theme === 'dark'}<Sun size={17} />{:else}<Moon size={17} />{/if}
      </button>
      <button class="station-chip" type="button">
        <Radio size={14} />
        <span class="sc-prov">{station.providerId}</span>
        <strong>{station.name}</strong>
        <ChevronDown size={14} />
      </button>
    </div>
  </header>

  <!-- ── CINTA DE ESTACIÓN ── -->
  <div class="stripe">
    <Isobars lines={7} />
    <div class="stripe-inner">
      <div class="s-left">
        <span class="s-prov">{station.provider}</span>
        <h1>{station.name}</h1>
        <span class="s-place">{station.place}</span>
      </div>
      <div class="s-facts">
        <span><small>ID</small>{station.id}</span>
        <span><small>ALT</small>{station.alt} m</span>
        <span><small>LAT</small>{station.lat}</span>
        <span><small>LON</small>{station.lon}</span>
      </div>
      <div class="s-live">
        <span class="live"><i></i>En directo</span>
        <span class="s-time">{station.updated} · {station.ago}</span>
        <button class="refresh" type="button" aria-label="Actualizar"><RefreshCw size={15} /></button>
      </div>
    </div>
  </div>

  <!-- ── CONTENIDO ── -->
  <main class="wrap">
    <div class="crumb">
      <current.icon size={15} />
      <span>{current.label}</span>
      <span class="proto-tag">datos de ejemplo · sin backend</span>
    </div>

    {#if active === 'observation'}<ObservationView />
    {:else if active === 'trends'}<TrendsView />
    {:else if active === 'historical'}<HistoricalView />
    {:else if active === 'map'}<MapView />
    {:else}<RankingView />{/if}

    <footer class="foot">
      <span>MeteoLabx · v1.3.1 — prototipo de rediseño</span>
      <span>No afiliado a los proveedores de datos</span>
    </footer>
  </main>
</div>

<style>
  .shell {
    min-height: 100vh;
    background:
      radial-gradient(circle at 82% -8%, var(--bg-grad-1), transparent 42%),
      radial-gradient(circle at 4% 108%, var(--bg-grad-2), transparent 46%),
      var(--bg);
  }

  /* ── TOP NAV ── */
  .topnav {
    position: sticky; top: 0; z-index: 20;
    display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
    gap: 20px; padding: 11px 26px;
    border-bottom: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg) 82%, transparent);
    backdrop-filter: blur(14px);
  }
  .brand { display: inline-flex; align-items: center; gap: 11px; text-decoration: none; color: inherit; }
  .brand img { width: 36px; height: 36px; border-radius: 10px; box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3); }
  .brand-txt { display: flex; flex-direction: column; line-height: 1.15; }
  .brand-txt strong { font-size: 0.94rem; letter-spacing: -0.02em; }
  .brand-txt small { font-size: 0.62rem; color: var(--muted); }

  .tabs { display: flex; gap: 3px; padding: 4px; border: 1px solid var(--border); border-radius: 13px; background: var(--panel-2); }
  .tabs button {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 14px; border: 0; border-radius: 9px;
    font-size: 0.79rem; font-weight: 560; color: var(--muted);
    background: transparent; white-space: nowrap; transition: color 0.15s, background 0.15s;
  }
  .tabs button:hover { color: var(--ink); }
  .tabs button.active { color: var(--accent); background: rgba(62, 142, 208, 0.13); font-weight: 640; }

  .right { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
  .theme { display: grid; place-items: center; width: 38px; height: 38px; border: 1px solid var(--border); border-radius: 10px; color: var(--ink-2); background: var(--panel); }
  .theme:hover { color: var(--ink); border-color: var(--border-2); }
  .station-chip { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel); font-size: 0.79rem; }
  .station-chip .sc-prov { font-size: 0.62rem; font-weight: 700; letter-spacing: 0.04em; color: var(--muted); }
  .station-chip strong { font-weight: 640; }
  .station-chip :global(svg:first-child) { color: var(--accent); }

  /* ── STRIPE / cinta de estación ── */
  .stripe { position: relative; overflow: hidden; border-bottom: 1px solid var(--border); background: linear-gradient(180deg, var(--panel), var(--panel-2)); }
  .stripe-inner {
    position: relative; z-index: 1;
    max-width: 1180px; margin: 0 auto;
    display: flex; align-items: center; gap: 30px; flex-wrap: wrap;
    padding: 22px 26px;
  }
  .s-left { display: flex; flex-direction: column; gap: 2px; }
  .s-prov { font-size: 0.64rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
  .s-left h1 { font-size: 1.5rem; font-weight: 720; letter-spacing: -0.03em; }
  .s-place { font-size: 0.78rem; color: var(--muted); }
  .s-facts { display: flex; gap: 22px; }
  .s-facts span { display: flex; flex-direction: column; font-size: 0.86rem; font-weight: 640; font-variant-numeric: tabular-nums; }
  .s-facts small { font-size: 0.58rem; font-weight: 700; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 2px; }
  .s-live { display: flex; align-items: center; gap: 14px; margin-left: auto; }
  .s-live .live { display: inline-flex; align-items: center; gap: 7px; font-size: 0.76rem; font-weight: 640; color: var(--ink-2); }
  .s-live .live i { width: 8px; height: 8px; border-radius: 50%; background: #43c98a; box-shadow: 0 0 0 4px rgba(67, 201, 138, 0.18); animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
  .s-time { font-size: 0.7rem; color: var(--muted); font-variant-numeric: tabular-nums; }
  .refresh { display: grid; place-items: center; width: 36px; height: 36px; border: 1px solid var(--border); border-radius: 10px; color: var(--ink-2); background: var(--card); }
  .refresh:hover { color: var(--ink); }

  /* ── WRAP ── */
  .wrap { max-width: 1180px; margin: 0 auto; padding: 22px 26px 8px; }
  .crumb { display: flex; align-items: center; gap: 9px; margin-bottom: 20px; font-size: 0.82rem; font-weight: 600; color: var(--ink-2); }
  .crumb :global(svg) { color: var(--accent); }
  .proto-tag { margin-left: auto; padding: 4px 11px; border: 1px dashed var(--border-2); border-radius: 999px; font-size: 0.68rem; font-weight: 500; color: var(--muted); }

  .foot { display: flex; justify-content: space-between; gap: 12px; padding: 26px 0 30px; font-size: 0.68rem; color: var(--muted-2); flex-wrap: wrap; }

  /* ── responsive ── */
  @media (max-width: 940px) {
    .topnav { grid-template-columns: auto 1fr; row-gap: 10px; }
    .tabs { grid-column: 1 / -1; order: 3; overflow-x: auto; }
    .right { grid-column: 2; }
    .brand-txt small { display: none; }
    .s-facts { gap: 16px; }
  }
  @media (max-width: 620px) {
    .topnav { padding: 11px 15px; }
    .tabs button span { display: none; }
    .tabs button { padding: 9px 13px; }
    .stripe-inner { padding: 18px 15px; gap: 18px; }
    .s-live { margin-left: 0; width: 100%; }
    .s-time { display: none; }
    .wrap { padding: 18px 15px 8px; }
    .proto-tag { display: none; }
  }
</style>

<script>
  import Icon from './Icon.svelte';
  import Sparkline from './Sparkline.svelte';
  import { families } from '../data.js';

  // metric: { title, value, unit, icon, family, chip?, sub?[], windDir? }
  let { metric, spark = null } = $props();
  const fam = $derived(families[metric.family] || { color: 'var(--accent)', soft: 'rgba(255,138,76,.12)' });

  function arrowGlyph(a) {
    return a === 'up' ? '▲' : a === 'down' ? '▼' : '';
  }
</script>

<article class="card" style:--fam={fam.color} style:--fam-soft={fam.soft}>
  <header>
    <span class="ic"><Icon name={metric.icon} size={17} /></span>
    <h3>{metric.title}</h3>
    {#if metric.chip}
      <span class="chip {metric.chip.tone}">{metric.chip.text}</span>
    {/if}
  </header>

  <div class="readout">
    <div class="val tnum">
      {metric.value}<span class="unit">{metric.unit}</span>
    </div>
    {#if metric.windDir != null}
      <div class="compass" style:--deg="{metric.windDir}deg" title={metric.windCard}>
        <svg viewBox="0 0 40 40" width="44" height="44" aria-hidden="true">
          <circle cx="20" cy="20" r="17" fill="none" stroke="var(--border-2)" stroke-width="1.5" />
          <g class="needle"><path d="M20 6 L24 22 L20 19 L16 22 Z" fill="var(--fam)" /></g>
        </svg>
        <small>{metric.windCard}</small>
      </div>
    {:else if spark}
      <Sparkline data={spark} color={fam.color} />
    {/if}
  </div>

  {#if metric.sub?.length}
    <ul class="sub">
      {#each metric.sub as s}
        <li>
          <span class="lbl">{s.label}</span>
          <span class="v tnum">
            {#if s.arrow}<b class="arw {s.arrow}">{arrowGlyph(s.arrow)}</b>{/if}
            {s.value}
            {#if s.chip}<span class="chip {s.chip.tone} sm">{s.chip.text}</span>{/if}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</article>

<style>
  .card {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px 16px 15px;
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    background: var(--card);
    overflow: hidden;
    transition: border-color 0.18s, transform 0.18s, background 0.18s;
  }
  .card::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    background: var(--fam);
    opacity: 0.85;
  }
  .card:hover { border-color: var(--border-2); background: var(--card-hover); transform: translateY(-2px); }

  header { display: flex; align-items: center; gap: 9px; }
  .ic {
    display: grid; place-items: center; width: 30px; height: 30px; flex: none;
    border-radius: 9px; color: var(--fam); background: var(--fam-soft);
  }
  h3 { font-size: 0.82rem; font-weight: 600; letter-spacing: -0.01em; color: var(--ink); }

  .chip {
    margin-left: auto; padding: 3px 8px; border-radius: 999px;
    font-size: 0.62rem; font-weight: 600; letter-spacing: 0.01em; white-space: nowrap;
  }
  .chip.warn { color: var(--chip-warn-fg); background: var(--chip-warn-bg); }
  .chip.note { color: var(--chip-note-fg); background: var(--chip-note-bg); }
  .chip.sm { margin-left: 6px; padding: 1px 6px; font-size: 0.58rem; }

  .readout { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; }
  .val { font-size: 2.05rem; font-weight: 680; line-height: 1; letter-spacing: -0.03em; }
  .unit { margin-left: 4px; font-size: 0.86rem; font-weight: 600; color: var(--muted); letter-spacing: 0; }

  .compass { display: flex; flex-direction: column; align-items: center; gap: 1px; }
  .compass .needle { transform-origin: 20px 20px; transform: rotate(var(--deg)); }
  .compass small { font-size: 0.6rem; color: var(--muted); font-weight: 600; }

  .sub { list-style: none; margin: 0; padding: 11px 0 0; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 7px; }
  .sub li { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .lbl { font-size: 0.72rem; color: var(--muted); }
  .sub .v { font-size: 0.76rem; font-weight: 600; color: var(--ink-2); display: inline-flex; align-items: center; }
  .arw { margin-right: 4px; font-size: 0.6rem; }
  .arw.up { color: #43c98a; }
  .arw.down { color: #e8686b; }
</style>

<script>
  import { mapProviders, mapStations, sensorFilters } from '../data.js';

  let selected = $state('ILHOSP26');
  const active = $derived(mapStations.find((s) => s.name === selected) || mapStations[0]);
</script>

<div class="map-layout">
  <!-- Panel de filtros -->
  <aside class="filters">
    <h3>Filtros</h3>

    <div class="fgroup">
      <div class="fhead"><span>Proveedores</span></div>
      <p class="caption">Activa proveedores para ver sus estaciones.</p>
      <ul class="prov-list">
        {#each mapProviders as p}
          <li>
            <label>
              <input type="checkbox" checked={p.near} />
              <i class="dot" style:background={p.color}></i>
              <span>{p.name}</span>
              <b>{p.count.toLocaleString('es-ES')}</b>
            </label>
          </li>
        {/each}
      </ul>
    </div>

    <div class="fgroup">
      <div class="fhead"><span>Sensores</span><button type="button">Limpiar</button></div>
      <div class="chips">
        {#each sensorFilters as s, i}
          <button class="fchip" class:on={i < 2} type="button">{s.label}</button>
        {/each}
      </div>
    </div>

    <div class="fgroup">
      <div class="fhead"><span>Países</span></div>
      <input class="search" type="text" placeholder="Buscar país…" value="España" />
    </div>
  </aside>

  <!-- Lienzo de mapa -->
  <section class="canvas">
    <div class="map-toolbar">
      <span class="mt-title">Área de Barcelona</span>
      <span class="mt-count">{mapStations.length} estaciones · {mapProviders.filter((p) => p.near).length} proveedores</span>
    </div>

    <div class="map-face">
      <!-- fondo estilizado (costa/mar), decorativo -->
      <svg class="map-bg" viewBox="0 0 100 70" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="land" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="rgba(120,160,120,0.10)" />
            <stop offset="1" stop-color="rgba(120,160,120,0.02)" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="100" height="70" fill="rgba(77,140,200,0.06)" />
        <path d="M0 0 H100 V44 Q78 50 60 46 Q40 42 22 52 Q8 58 0 54 Z" fill="url(#land)" stroke="var(--border-2)" stroke-width="0.3" />
        {#each [12, 24, 36, 48, 60, 72, 84, 96] as gx}
          <line x1={gx} y1="0" x2={gx} y2="70" stroke="var(--grid-line)" stroke-width="0.3" />
        {/each}
        {#each [10, 20, 30, 40, 50, 60] as gy}
          <line x1="0" y1={gy} x2="100" y2={gy} stroke="var(--grid-line)" stroke-width="0.3" />
        {/each}
      </svg>

      <!-- marcadores -->
      {#each mapStations as s}
        {@const col = mapProviders.find((p) => p.id === s.provider)?.color || '#ff8a4c'}
        <button
          class="marker"
          class:sel={s.name === selected}
          style:left="{s.x}%" style:top="{s.y}%" style:--mc={col}
          type="button"
          onclick={() => (selected = s.name)}
        >
          <span class="mtemp tnum">{s.t}°</span>
        </button>
      {/each}

      <div class="map-legend">
        {#each mapProviders.filter((p) => p.near) as p}
          <span><i style:background={p.color}></i>{p.name}</span>
        {/each}
      </div>
    </div>

    <!-- detalle de estación seleccionada -->
    <div class="detail">
      <div class="d-main">
        <span class="d-prov" style:--mc={mapProviders.find((p) => p.id === active.provider)?.color}>{active.provider}</span>
        <strong>{active.name}</strong>
        <span class="d-temp tnum">{active.t}°C</span>
      </div>
      <button class="connect" type="button">Conectar estación</button>
    </div>
  </section>
</div>

<style>
  .map-layout { display: grid; grid-template-columns: 268px 1fr; gap: 16px; }

  .filters { padding: 18px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); height: fit-content; }
  .filters > h3 { font-size: 0.9rem; font-weight: 700; margin-bottom: 4px; }
  .fgroup { padding: 16px 0; border-top: 1px solid var(--border); }
  .fgroup:first-of-type { border-top: 0; padding-top: 10px; }
  .fhead { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .fhead span { font-size: 0.78rem; font-weight: 640; }
  .fhead button { font-size: 0.68rem; color: var(--accent); border: 0; background: none; }
  .caption { font-size: 0.68rem; color: var(--muted); margin-bottom: 10px; line-height: 1.4; }

  .prov-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .prov-list label { display: flex; align-items: center; gap: 9px; padding: 7px 8px; border-radius: 9px; font-size: 0.76rem; }
  .prov-list label:hover { background: var(--card-hover); }
  .prov-list input { accent-color: var(--accent); width: 15px; height: 15px; }
  .prov-list .dot { width: 9px; height: 9px; border-radius: 50%; }
  .prov-list b { margin-left: auto; font-size: 0.68rem; font-weight: 600; color: var(--muted); font-family: var(--mono); }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .fchip { padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; font-size: 0.68rem; color: var(--muted); background: transparent; }
  .fchip.on { color: var(--accent); border-color: rgba(255, 138, 76, 0.4); background: rgba(255, 138, 76, 0.1); }

  .search { width: 100%; padding: 9px 11px; border: 1px solid var(--border-2); border-radius: 9px; background: var(--panel-2); color: var(--ink); font-size: 0.76rem; outline: none; }

  .canvas { border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); overflow: hidden; }
  .map-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); }
  .mt-title { font-size: 0.86rem; font-weight: 640; }
  .mt-count { font-size: 0.72rem; color: var(--muted); }

  .map-face { position: relative; aspect-ratio: 16 / 9; background: var(--panel-2); }
  .map-bg { position: absolute; inset: 0; width: 100%; height: 100%; }

  .marker {
    position: absolute; transform: translate(-50%, -50%);
    display: grid; place-items: center; padding: 3px 8px;
    border: 1.5px solid var(--mc); border-radius: 999px;
    background: var(--card); color: var(--ink); white-space: nowrap;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    transition: transform 0.12s;
  }
  .marker:hover { transform: translate(-50%, -50%) scale(1.08); z-index: 3; }
  .marker .mtemp { font-size: 0.72rem; font-weight: 700; }
  .marker.sel { background: var(--mc); color: #10151d; box-shadow: 0 0 0 5px color-mix(in srgb, var(--mc) 22%, transparent); z-index: 4; }

  .map-legend { position: absolute; left: 12px; bottom: 12px; display: flex; flex-wrap: wrap; gap: 12px; padding: 8px 12px; border-radius: 10px; background: color-mix(in srgb, var(--panel) 82%, transparent); backdrop-filter: blur(6px); border: 1px solid var(--border); }
  .map-legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.68rem; color: var(--ink-2); }
  .map-legend i { width: 8px; height: 8px; border-radius: 50%; }

  .detail { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px 18px; border-top: 1px solid var(--border); flex-wrap: wrap; }
  .d-main { display: flex; align-items: center; gap: 12px; }
  .d-prov { padding: 3px 9px; border-radius: 7px; font-size: 0.64rem; font-weight: 700; color: var(--mc); background: color-mix(in srgb, var(--mc) 16%, transparent); }
  .d-main strong { font-size: 0.9rem; }
  .d-temp { font-size: 0.9rem; font-weight: 700; color: var(--ink-2); }
  .connect { padding: 9px 16px; border: 0; border-radius: 9px; font-size: 0.78rem; font-weight: 640; color: var(--accent-ink); background: var(--accent); }

  @media (max-width: 860px) {
    .map-layout { grid-template-columns: 1fr; }
  }
</style>

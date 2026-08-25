<script>
  import Climogram from '../lib/Climogram.svelte';
  import WindRose from '../lib/WindRose.svelte';
  import { months, climogram, windRose, historicalTable } from '../data.js';
</script>

<div class="hist-head">
  <div>
    <h2>Histórico</h2>
    <p>Resumen climático · Observatori Fabra · 1990–2025</p>
  </div>
  <div class="controls">
    <label>Tipo de resumen
      <div class="seg">
        <button class="active" type="button">Mensual</button>
        <button type="button">Anual</button>
      </div>
    </label>
  </div>
</div>

<section class="climo-card">
  <header>
    <h3>Climograma (mensual)</h3>
    <div class="legend">
      <span><i style:background="#ff8a4c"></i>Tmáx</span>
      <span><i style:background="#4db6e8"></i>Tmín</span>
      <span><i class="bar"></i>Precipitación</span>
    </div>
  </header>
  <Climogram {months} tmax={climogram.tmax} tmin={climogram.tmin} precip={climogram.precip} />
</section>

<div class="hist-lower">
  <section class="rose-card">
    <h3>Rosa de viento del periodo</h3>
    <p class="sub">% por rumbo · 16 sectores</p>
    <div class="rose-wrap"><WindRose data={windRose} size={230} /></div>
  </section>

  <section class="table-card">
    <h3>Resumen del periodo</h3>
    <table>
      <thead><tr><th>Métrica</th><th>Valor</th></tr></thead>
      <tbody>
        {#each historicalTable as row}
          <tr><td>{row.metric}</td><td class="tnum">{row.value}</td></tr>
        {/each}
      </tbody>
    </table>
  </section>
</div>

<style>
  .hist-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .hist-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .hist-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted); }
  .controls label { display: flex; flex-direction: column; gap: 7px; font-size: 0.68rem; color: var(--muted); font-weight: 600; }
  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg button { padding: 6px 14px; border: 0; border-radius: 7px; font-size: 0.74rem; font-weight: 600; color: var(--muted); background: transparent; }
  .seg button.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }

  section { border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .climo-card { padding: 18px 20px 14px; margin-bottom: 16px; }
  .climo-card header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px; }
  h3 { font-size: 0.88rem; font-weight: 700; }
  .legend { display: flex; gap: 14px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.7rem; color: var(--muted); }
  .legend i { width: 12px; height: 3px; border-radius: 2px; }
  .legend i.bar { width: 11px; height: 11px; border-radius: 3px; background: rgba(91, 155, 255, 0.5); }

  .hist-lower { display: grid; grid-template-columns: 1fr 1.3fr; gap: 16px; }
  .rose-card, .table-card { padding: 18px 20px; }
  .rose-card .sub { margin-top: 3px; font-size: 0.72rem; color: var(--muted); }
  .rose-wrap { display: grid; place-items: center; padding: 8px 0 4px; }

  table { width: 100%; border-collapse: collapse; margin-top: 14px; }
  th { text-align: left; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); padding: 0 0 10px; border-bottom: 1px solid var(--border); }
  th:last-child, td:last-child { text-align: right; }
  td { padding: 11px 0; font-size: 0.82rem; border-bottom: 1px solid var(--border); }
  td:first-child { color: var(--ink-2); }
  td:last-child { font-weight: 640; }
  tbody tr:last-child td { border-bottom: 0; }

  @media (max-width: 860px) {
    .hist-lower { grid-template-columns: 1fr; }
  }
</style>

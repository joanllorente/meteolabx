<script>
  import TrendChart from '../lib/TrendChart.svelte';
  import { hours, trendSeries } from '../data.js';

  const charts = [
    {
      title: 'Temperatura y punto de rocío', axis: '°C', zero: false,
      series: [
        { data: trendSeries.temperature, color: '#ff8a4c', label: 'Temperatura' },
        { data: trendSeries.dewpoint, color: '#4db6e8', label: 'Punto de rocío' }
      ]
    },
    {
      title: 'Tendencia de Presión Absoluta (intervalo 3h)', axis: 'dp/dt (hPa/h)', zero: true,
      series: [{ data: trendSeries.pressure_dt, color: '#8b8bff', label: 'dp/dt' }]
    },
    {
      title: 'Tendencia de Temperatura Potencial Equivalente (θe)', axis: 'dθe/dt (K/h)', zero: true,
      series: [{ data: trendSeries.theta_e_dt, color: '#b98bff', label: 'dθe/dt' }]
    },
    {
      title: 'Tendencia de Razón de Mezcla (r)', axis: 'dr/dt (g/kg/h)', zero: true,
      series: [{ data: trendSeries.mixing_dt, color: '#2fb8a6', label: 'dr/dt' }]
    },
    {
      title: 'Componentes del viento', axis: 'Velocidad (km/h)', zero: true,
      series: [
        { data: trendSeries.wind_u, color: '#37c8d6', label: 'u (zonal)' },
        { data: trendSeries.wind_v, color: '#f4bb3f', label: 'v (meridional)' }
      ]
    }
  ];
</script>

<div class="trend-head">
  <div>
    <h2>Tendencias</h2>
    <p>Evolución de las variables a lo largo del día</p>
  </div>
  <div class="controls">
    <span class="source">Fuente: Serie local del proveedor (5 min)</span>
    <div class="seg">
      <button class="active" type="button">Tendencia sinóptica</button>
      <button type="button">Hoy</button>
    </div>
  </div>
</div>

<div class="charts">
  {#each charts as c}
    <section class="chart-card">
      <header>
        <h3>{c.title}</h3>
        <div class="legend">
          {#each c.series as s}
            <span><i style:background={s.color}></i>{s.label}</span>
          {/each}
        </div>
      </header>
      <span class="axis-name">{c.axis}</span>
      <TrendChart series={c.series} labels={hours} zeroLine={c.zero} height={190} />
    </section>
  {/each}
</div>

<style>
  .trend-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .trend-head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .trend-head p { margin-top: 4px; font-size: 0.8rem; color: var(--muted); }
  .controls { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .source { font-size: 0.72rem; color: var(--muted); }
  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg button { padding: 6px 12px; border: 0; border-radius: 7px; font-size: 0.74rem; font-weight: 600; color: var(--muted); background: transparent; }
  .seg button.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }

  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .chart-card { padding: 18px 18px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .chart-card header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 2px; }
  .chart-card h3 { font-size: 0.82rem; font-weight: 600; max-width: 70%; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; font-size: 0.68rem; color: var(--muted); }
  .legend i { width: 9px; height: 9px; border-radius: 3px; }
  .axis-name { display: block; font-size: 0.64rem; color: var(--muted-2); font-family: var(--mono); margin: 6px 0 2px; }

  .charts .chart-card:first-child { grid-column: 1 / -1; }

  @media (max-width: 900px) {
    .charts { grid-template-columns: 1fr; }
    .charts .chart-card:first-child { grid-column: auto; }
  }
</style>

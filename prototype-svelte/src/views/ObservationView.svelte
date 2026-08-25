<script>
  import Icon from '../lib/Icon.svelte';
  import Sparkline from '../lib/Sparkline.svelte';
  import MetricCard from '../lib/MetricCard.svelte';
  import TrendChart from '../lib/TrendChart.svelte';
  import WindChart from '../lib/WindChart.svelte';
  import WindRose from '../lib/WindRose.svelte';
  import {
    thermo, radiation, trendSeries, families,
    obsHours, obsNowIndex, obsCharts, obsWindRose, obsWindRoseStats
  } from '../data.js';

  const thermoMetrics = thermo.map((m) => ({ ...m, family: 'thermo' }));
  const radMetrics = radiation.map((m) => ({ ...m, family: 'radiation' }));
</script>

<!-- ══ OBSERVADOS · bento ══ -->
<div class="sec-head"><h2>Observados</h2><span class="rule"></span><span class="meta">Medidas directas de la estación</span></div>

<div class="bento">
  <!-- Temperatura (grande) -->
  <article class="tile temp t-hero" style:--fam={families.temperature.color}>
    <header><span class="ic"><Icon name="Thermometer" size={18} /></span><h3>Temperatura</h3><span class="chip warn">Aviso de calor</span></header>
    <div class="hero-val tnum">29.4<span>°C</span></div>
    <p class="hero-feels">Sensación <strong>32.1°</strong> · Heat index <strong>33.0°</strong></p>
    <div class="hero-spark">
      <div class="hs-head"><span>Hoy</span><span class="rng">21.3° — 30.9°</span></div>
      <Sparkline data={trendSeries.temperature} color={families.temperature.color} width={340} height={64} />
    </div>
  </article>

  <!-- Humedad -->
  <article class="tile hum t-a" style:--fam={families.humidity.color}>
    <header><span class="ic"><Icon name="Droplets" size={17} /></span><h3>Humedad relativa</h3></header>
    <div class="val tnum">68<span>%</span></div>
    <div class="foot"><span>Presión de vapor</span><b>27.4 hPa</b></div>
  </article>

  <!-- Punto de rocío -->
  <article class="tile dew t-b" style:--fam={families.dewpoint.color}>
    <header><span class="ic"><Icon name="Droplet" size={17} /></span><h3>Punto de rocío</h3></header>
    <div class="val tnum">22.8<span>°C</span></div>
    <div class="foot"><span>Bulbo húmedo</span><b>24.6 °C</b></div>
  </article>

  <!-- Viento (alto, con brújula) -->
  <article class="tile wind t-tall" style:--fam={families.wind.color}>
    <header><span class="ic"><Icon name="Wind" size={17} /></span><h3>Viento</h3></header>
    <div class="compass">
      <svg viewBox="0 0 90 90" width="118" height="118" aria-hidden="true">
        <circle cx="45" cy="45" r="40" fill="none" stroke="var(--border)" stroke-width="1.5" />
        {#each ['N','E','S','W'] as c, i}
          <text x={45 + Math.sin((i * 90) * Math.PI / 180) * 33} y={45 - Math.cos((i * 90) * Math.PI / 180) * 33 + 4} text-anchor="middle" class="cpt">{c}</text>
        {/each}
        <g style="transform: rotate(155deg); transform-origin: 45px 45px;">
          <path d="M45 14 L52 46 L45 40 L38 46 Z" fill="var(--fam)" />
        </g>
      </svg>
      <div class="c-read"><strong class="tnum">14</strong><span>km/h</span></div>
    </div>
    <div class="wind-sub">
      <div><small>Racha</small><b>27 km/h</b></div>
      <div><small>Dirección</small><b>SSE · 155°</b></div>
    </div>
  </article>

  <!-- Precipitación -->
  <article class="tile precip t-c" style:--fam={families.precip.color}>
    <header><span class="ic"><Icon name="CloudRain" size={17} /></span><h3>Precipitación hoy</h3><span class="chip note">Sin lluvia</span></header>
    <div class="val tnum">1.2<span>mm</span></div>
    <div class="foot"><span>Instantánea</span><b>0.0 mm/h</b></div>
  </article>

  <!-- Presión (ancho) -->
  <article class="tile press t-wide" style:--fam={families.pressure.color}>
    <div class="pw-left">
      <header><span class="ic"><Icon name="Gauge" size={17} /></span><h3>Presión</h3></header>
      <div class="val tnum">1014.2<span>hPa</span></div>
    </div>
    <div class="press-stats">
      <span><small>Tendencia</small><b class="up">▲ Subiendo</b></span>
      <span><small>Δ3h</small><b>+0.8 hPa</b></span>
      <span><small>MSL</small><b>1018.9 hPa</b></span>
    </div>
  </article>

  <!-- UV (destacado) -->
  <article class="tile uv t-d" style:--fam={families.radiation.color}>
    <header><span class="ic"><Icon name="SunMedium" size={17} /></span><h3>Índice UV</h3></header>
    <div class="val tnum">7<span>Alto</span></div>
    <div class="foot"><span>Irradiancia</span><b>712 W/m²</b></div>
  </article>
</div>

<!-- ══ TERMODINÁMICA ══ -->
<div class="sec-head"><h2>Termodinámica</h2><span class="rule"></span><span class="meta">Variables derivadas</span></div>
<div class="grid compact">
  {#each thermoMetrics as m}<MetricCard metric={m} />{/each}
</div>

<!-- ══ RADIACIÓN ══ -->
<div class="sec-head"><h2>Radiación</h2><span class="rule"></span><span class="meta">Sol, UV y balance hídrico</span></div>
<div class="grid compact">
  {#each radMetrics as m}<MetricCard metric={m} />{/each}
</div>

<!-- ══ GRÁFICOS ══ -->
<div class="sec-head"><h2>Gráficos</h2><span class="rule"></span><span class="meta">Evolución intradía de hoy</span></div>
<div class="charts">
  <!-- Temperatura -->
  <section class="chart-card wide">
    <header><h3>Temperatura de Hoy</h3><span class="axis-name">°C</span></header>
    <TrendChart
      series={[{ data: obsCharts.temperature, color: families.temperature.color }]}
      labels={obsHours} nowIndex={obsNowIndex} height={180}
    />
  </section>

  <!-- Presión de vapor -->
  <section class="chart-card">
    <header>
      <h3>Presión de Vapor y Saturación</h3>
      <div class="legend"><span><i style:background={families.humidity.color}></i>e</span><span><i class="dash" style:--c={families.humidity.color}></i>e_s</span></div>
    </header>
    <span class="axis-name">hPa</span>
    <TrendChart
      series={[
        { data: obsCharts.vapor_e, color: families.humidity.color },
        { data: obsCharts.vapor_es, color: families.humidity.color, dash: true }
      ]}
      labels={obsHours} nowIndex={obsNowIndex} fillArea={false} height={176}
    />
  </section>

  <!-- Precipitación -->
  <section class="chart-card">
    <header><h3>Precipitación registrada hoy</h3><span class="axis-name">mm</span></header>
    <TrendChart
      series={[{ data: obsCharts.precip, color: families.precip.color }]}
      labels={obsHours} nowIndex={obsNowIndex} height={176}
    />
  </section>

  <!-- Viento y rachas -->
  <section class="chart-card wide">
    <header>
      <h3>Viento y Rachas Hoy</h3>
      <div class="legend">
        <span><i style:background="#2f7fd6"></i>Viento</span>
        <span><i class="dash" style:--c="#37c8d6"></i>Racha</span>
        <span><i class="dot"></i>Dirección</span>
      </div>
    </header>
    <WindChart
      labels={obsHours} speed={obsCharts.wind_speed} gust={obsCharts.wind_gust}
      dir={obsCharts.wind_dir} height={180}
    />
  </section>

  <!-- Rosa de viento -->
  <section class="chart-card rose">
    <header><h3>Rosa de Viento</h3></header>
    <div class="rose-wrap"><WindRose data={obsWindRose} size={200} /></div>
    <div class="rose-stats">
      <span><small>Dominante</small><b>{obsWindRoseStats.dominant}</b></span>
      <span><small>Frecuencia</small><b>{obsWindRoseStats.frequency}</b></span>
      <span><small>Muestras</small><b>{obsWindRoseStats.samples}</b></span>
      <span><small>Calma &lt;2 km/h</small><b>{obsWindRoseStats.calm}</b></span>
    </div>
  </section>

  <!-- Irradiancia -->
  <section class="chart-card">
    <header>
      <h3>Irradiancia medida vs teórica</h3>
      <div class="legend"><span><i style:background={families.radiation.color}></i>Medida</span><span><i class="dash" style:--c={families.radiation.color}></i>Teórica</span></div>
    </header>
    <span class="axis-name">W/m²</span>
    <TrendChart
      series={[
        { data: obsCharts.irr_measured, color: families.radiation.color },
        { data: obsCharts.irr_theoretical, color: families.radiation.color, dash: true }
      ]}
      labels={obsHours} nowIndex={obsNowIndex} fillArea={false} height={176}
    />
  </section>
</div>

<style>
  .sec-head { display: flex; align-items: center; gap: 14px; margin: 6px 0 15px; }
  .sec-head h2 { font-size: 0.96rem; font-weight: 700; letter-spacing: -0.01em; }
  .rule { height: 1px; flex: 1; background: var(--border); }
  .meta { font-size: 0.72rem; color: var(--muted); }

  /* ── BENTO ── */
  .bento {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    grid-auto-rows: 128px;
    gap: 13px;
    margin-bottom: 30px;
    grid-template-areas:
      "temp temp hum  dew"
      "temp temp wind precip"
      "press press wind uv";
  }
  .t-hero { grid-area: temp; } .t-a { grid-area: hum; } .t-b { grid-area: dew; }
  .t-tall { grid-area: wind; } .t-c { grid-area: precip; }
  .t-wide { grid-area: press; } .t-d { grid-area: uv; }

  .tile {
    position: relative; display: flex; flex-direction: column;
    padding: 15px 16px; border: 1px solid var(--border); border-radius: var(--r-md);
    background: var(--card); overflow: hidden; transition: border-color 0.18s, transform 0.18s, background 0.18s;
  }
  .tile::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--fam); opacity: 0.9; }
  .tile:hover { border-color: var(--border-2); background: var(--card-hover); transform: translateY(-2px); }

  .tile header { display: flex; align-items: center; gap: 9px; margin-bottom: 8px; }
  .ic { display: grid; place-items: center; width: 29px; height: 29px; flex: none; border-radius: 8px; color: var(--fam); background: color-mix(in srgb, var(--fam) 15%, transparent); }
  .tile h3 { font-size: 0.8rem; font-weight: 600; }
  .chip { margin-left: auto; padding: 3px 8px; border-radius: 999px; font-size: 0.6rem; font-weight: 700; white-space: nowrap; }
  .chip.warn { color: var(--chip-warn-fg); background: var(--chip-warn-bg); }
  .chip.note { color: var(--chip-note-fg); background: var(--chip-note-bg); }

  .val { font-size: 1.9rem; font-weight: 680; line-height: 1; letter-spacing: -0.03em; margin-top: auto; }
  .val span { margin-left: 4px; font-size: 0.78rem; font-weight: 600; color: var(--muted); letter-spacing: 0; }
  .foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--border); font-size: 0.72rem; color: var(--muted); }
  .foot b { color: var(--ink-2); font-weight: 640; }

  /* hero temp */
  .t-hero .hero-val { font-size: 3.9rem; font-weight: 720; line-height: 0.95; letter-spacing: -0.04em; margin-top: 6px; }
  .t-hero .hero-val span { font-size: 1.3rem; font-weight: 600; color: var(--muted); margin-left: 5px; }
  .hero-feels { margin-top: 8px; font-size: 0.82rem; color: var(--ink-2); }
  .hero-feels strong { color: var(--ink); font-weight: 700; }
  .hero-spark { margin-top: auto; padding-top: 12px; }
  .hs-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
  .hs-head span:first-child { font-size: 0.72rem; color: var(--muted); font-weight: 600; }
  .rng { font-size: 0.7rem; color: var(--muted); font-family: var(--mono); }

  /* viento */
  .wind .compass { display: flex; align-items: center; gap: 14px; margin: 4px 0; }
  .cpt { fill: var(--muted); font-size: 9px; font-weight: 700; font-family: var(--font); }
  .c-read { display: flex; flex-direction: column; }
  .c-read strong { font-size: 1.8rem; font-weight: 700; line-height: 1; letter-spacing: -0.03em; }
  .c-read span { font-size: 0.74rem; color: var(--muted); }
  .wind-sub { display: flex; gap: 18px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--border); }
  .wind-sub div { display: flex; flex-direction: column; gap: 2px; }
  .wind-sub small { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .wind-sub b { font-size: 0.82rem; font-weight: 640; }

  /* presión ancho */
  .t-wide { flex-direction: row; align-items: center; gap: 26px; }
  .pw-left { display: flex; flex-direction: column; }
  .pw-left .val { margin-top: 8px; }
  .press-stats { display: flex; gap: 30px; margin-left: auto; }
  .press-stats span { display: flex; flex-direction: column; gap: 4px; }
  .press-stats small { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .press-stats b { font-size: 0.9rem; font-weight: 660; }
  .press-stats b.up { color: #43c98a; }

  .grid { display: grid; gap: 13px; margin-bottom: 30px; }
  .grid.compact { grid-template-columns: repeat(4, 1fr); }

  /* ── GRÁFICOS ── */
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 30px; }
  .chart-card { padding: 16px 18px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 4px; }
  .chart-card h3 { font-size: 0.84rem; font-weight: 660; }
  .axis-name { display: block; font-size: 0.64rem; color: var(--muted-2); font-family: var(--mono); margin: 2px 0; }
  .chart-card header .axis-name { margin: 3px 0 0; }
  .legend { display: flex; gap: 13px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.68rem; color: var(--muted); white-space: nowrap; }
  .legend i { width: 11px; height: 3px; border-radius: 2px; }
  .legend i.dash { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); }
  .legend i.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }

  .chart-card.rose { display: flex; flex-direction: column; }
  .rose-wrap { display: grid; place-items: center; padding: 6px 0 10px; }
  .rose-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; padding-top: 12px; border-top: 1px solid var(--border); margin-top: auto; }
  .rose-stats span { display: flex; flex-direction: column; gap: 2px; }
  .rose-stats small { font-size: 0.62rem; color: var(--muted); }
  .rose-stats b { font-size: 0.86rem; font-weight: 660; }

  /* ── responsive ── */
  @media (max-width: 1080px) {
    .bento {
      grid-auto-rows: 118px;
      grid-template-areas:
        "temp temp hum  dew"
        "temp temp wind precip"
        "press press wind uv";
    }
    .grid.compact { grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 760px) {
    .bento {
      grid-template-columns: repeat(2, 1fr);
      grid-auto-rows: 120px;
      grid-template-areas:
        "temp temp"
        "temp temp"
        "hum  dew"
        "wind precip"
        "wind uv"
        "press press";
    }
    .grid.compact { grid-template-columns: repeat(2, 1fr); }
    .t-wide { flex-direction: column; align-items: flex-start; }
    .press-stats { margin-left: 0; margin-top: 12px; gap: 22px; }
    .charts { grid-template-columns: 1fr; }
  }
  @media (max-width: 440px) {
    .bento { grid-template-columns: 1fr; grid-template-areas: "temp" "temp" "hum" "dew" "wind" "precip" "uv" "press"; }
    .grid.compact { grid-template-columns: 1fr; }
  }
</style>

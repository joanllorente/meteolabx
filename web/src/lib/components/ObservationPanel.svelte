<script>
  /**
   * El panel de observación: la interfaz del prototipo con datos reales.
   *
   * Se renderiza en el servidor, gráficas incluidas —son SVG puro, sin
   * canvas ni librerías—, así que lo que recibe un buscador es exactamente
   * lo que ve una persona: valores, series del día y rosa de los vientos.
   */
  import Icon from './Icon.svelte';
  import MetricCard from './MetricCard.svelte';
  import Sparkline from './Sparkline.svelte';
  import TrendChart from './TrendChart.svelte';
  import WindChart from './WindChart.svelte';
  import WindRose from './WindRose.svelte';
  import { families } from '$lib/families.js';
  import { num, stationTime } from '$lib/format.js';

  import app from '$lib/i18n/app-i18n.generated.js';
  import { cardTooltip } from '$lib/i18n/card-tooltip.js';
  import { ui } from '$lib/i18n/ui.js';
  import { t } from '$lib/seo/i18n.js';

  let { model, language, stationName = '' } = $props();

  /** Nombre del PNG: de qué gráfica es y de qué estación. */
  const pngName = $derived(
    (key) => `meteolabx ${ui(language, key)} ${stationName}`.trim()
  );

  const charts = $derived(model.charts);
  const tick = $derived((value, decimals = 1) => num(value, { language, decimals }));

  /** La definición de cada tarjeta básica, indexada por su clave canónica. */
  const help = $derived((key) => cardTooltip(key, language));

  /**
   * Instante señalado por el cursor, compartido por todas las gráficas del día.
   *
   * Es lo que permite comparar: al pasar el ratón por la de temperatura, la de
   * lluvia y la de viento marcan el mismo momento. Se sincroniza por tiempo y
   * no por posición, porque cada serie descarta sus propios huecos.
   */
  let activeEpoch = $state(null);
  const onHover = (epoch) => (activeEpoch = epoch);

  /** Hora local de la estación para el punto señalado. */
  const formatEpoch = $derived((epoch) =>
    stationTime(epoch, { language, timeZone: model.timeZone || 'UTC' })
  );

  /**
   * Series visibles del gráfico de viento.
   *
   * Tres magnitudes en un mismo eje se estorban; apagar las que no interesan
   * es la única forma de leer una sola. Se pulsa su nombre en la leyenda.
   */
  let windSeries = $state({ speed: true, gust: true, dir: true });
  const toggleWind = (key) => (windSeries = { ...windSeries, [key]: !windSeries[key] });

  // «Máx.» y «Mín.» ya están traducidas para el histórico; son las mismas.
  const shortLabels = $derived(
    app.historical?.[language]?.cards || app.historical?.es?.cards || {}
  );
  const hasCharts = $derived(
    Boolean(charts.temperature || charts.vapour || charts.precipitation || charts.wind || charts.irradiance)
  );
</script>

<!-- ══ OBSERVADO · bento ══ -->
<div class="sec-head">
  <h2>{ui(language, 'section_observed')}</h2>
  <span class="rule"></span>
</div>

<!-- El aviso, sobre las tarjetas y no dentro de una.
     Puede venir del índice de calor o del bulbo húmedo, y metido en la
     tarjeta de temperatura daba a entender que hablaba del termómetro: se
     leía «exposición peligrosa» junto a una temperatura de lo más normal. -->
{#if model.temperature.alert}
  <p class="alert-band {model.temperature.alert.tone}" role="alert">
    <b>{model.temperature.alert.subject}</b>
    {model.temperature.alert.text}
  </p>
{/if}

{#snippet extremes(values)}
  <!-- Máximo y mínimo del día, en la esquina de la tarjeta: es donde se
       buscan y no compiten con la lectura actual. -->
  {#if values?.max || values?.min}
    <div class="extremes">
      {#if values.max}
        <span class="up">
          <i>▲</i><span class="extreme-label">{shortLabels.max_short || ''}</span><b class="tnum">{values.max}</b>
        </span>
      {/if}
      {#if values.min}
        <span class="down">
          <i>▼</i><span class="extreme-label">{shortLabels.min_short || ''}</span><b class="tnum">{values.min}</b>
        </span>
      {/if}
    </div>
  {/if}
{/snippet}

<div class="bento">
  <!-- Temperatura -->
  <article class="tile temp t-hero" style:--fam={families.temperature.color}>
    <header>
      <span class="ic"><Icon name="Thermometer" size={18} /></span>
      <h3>{t(language, 'obs_temperature')}</h3>
      {#if help('temperatura')}
        <span class="help" tabindex="0" role="note" aria-label={help('temperatura')}>?</span>
        <span class="bubble">{help('temperatura')}</span>
      {/if}
      {@render extremes(model.temperature.extremes)}
    </header>
    <div class="hero-val tnum">{model.temperature.value}<span>{model.temperature.unit}</span></div>
    <p class="hero-feels">
      <!-- Con la unidad entera: un «33,6°» a secas son grados de un ángulo,
           no de temperatura. Y separada del número, como manda el SI y como
           lo escribe la aplicación actual. -->
      {#if model.temperature.feelsLike}
        {ui(language, 'feels_like')} <strong>{model.temperature.feelsLike} {model.temperature.unit}</strong>
      {/if}
      {#if model.temperature.heatIndex}
        · {ui(language, 'heat_index')} <strong>{model.temperature.heatIndex} {model.temperature.unit}</strong>
      {:else if model.temperature.windChill}
        · {ui(language, 'wind_chill')} <strong>{model.temperature.windChill} {model.temperature.unit}</strong>
      {/if}
    </p>
    <!-- El riesgo, en una línea, desde los 40 °C de índice de calor. El aviso
         largo llega después, a los 45: hasta ahora, entre 40 y 45 la tarjeta
         no decía nada. -->
    {#if model.temperature.risk}
      <p class="hero-risk {model.temperature.risk.tone}">{model.temperature.risk.text}</p>
    {/if}
    {#if model.temperature.spark}
      <div class="hero-bottom">
        {#if model.temperature.spark}
          <div class="hero-spark">
            <Sparkline data={model.temperature.spark} color={families.temperature.color} width={340} height={64} />
          </div>
        {/if}
      </div>
    {/if}
  </article>

  <!-- Humedad -->
  <article class="tile hum t-a" style:--fam={families.humidity.color}>
    <header><span class="ic"><Icon name="Droplets" size={17} /></span><h3>{t(language, 'obs_humidity')}</h3>
      {#if help('humedad relativa')}
        <span class="help" tabindex="0" role="note" aria-label={help('humedad relativa')}>?</span>
        <span class="bubble">{help('humedad relativa')}</span>
      {/if}
      {@render extremes(model.humidity.extremes)}</header>
    <div class="val tnum">{model.humidity.value}<span>%</span></div>
    {#if model.humidity.vapourPressure}
      <div class="foot"><span>{ui(language, 'vapour_pressure')}</span><b>{model.humidity.vapourPressure}</b></div>
    {/if}
  </article>

  <!-- Punto de rocío -->
  <article class="tile dew t-b" style:--fam={families.dewpoint.color}>
    <header>
      <span class="ic"><Icon name="Droplet" size={17} /></span>
      <h3>{t(language, 'obs_dew_point')}</h3>
      {#if help('punto de rocio')}
        <span class="help" tabindex="0" role="note" aria-label={help('punto de rocio')}>?</span>
        <span class="bubble">{help('punto de rocio')}</span>
      {/if}
      <!-- El estado del bulbo húmedo, con las palabras de la aplicación
           actual. En la cabecera y no bajo el valor: la fila del bento tiene
           altura fija y ahí abajo el texto se cortaba a media línea. -->
      {#if model.dewPoint.risk}
        <span class="chip {model.dewPoint.risk.tone === 'danger' ? 'warn' : 'note'}">
          {model.dewPoint.risk.text.charAt(0).toUpperCase() + model.dewPoint.risk.text.slice(1)}
        </span>
      {/if}
    </header>
    <div class="val tnum">{model.dewPoint.value}<span>{model.dewPoint.unit}</span></div>
    {#if model.dewPoint.wetBulb}
      <div class="foot"><span>{ui(language, 'wet_bulb')}</span><b>{model.dewPoint.wetBulb}</b></div>
    {/if}
  </article>

  <!-- Viento -->
  <article class="tile wind t-tall" style:--fam={families.wind.color}>
    <header><span class="ic"><Icon name="Wind" size={17} /></span><h3>{t(language, 'obs_wind')}</h3>
      {#if help('viento')}
        <span class="help" tabindex="0" role="note" aria-label={help('viento')}>?</span>
        <span class="bubble">{help('viento')}</span>
      {/if}
      {@render extremes(model.wind.extremes)}</header>
    <div class="compass">
      <svg viewBox="0 0 90 90" width="118" height="118" aria-hidden="true">
        <circle cx="45" cy="45" r="40" fill="none" stroke="var(--border)" stroke-width="1.5" />
        {#each ['N', 'E', 'S', 'W'] as _, i}
          <text
            x={45 + Math.sin(i * 90 * Math.PI / 180) * 33}
            y={45 - Math.cos(i * 90 * Math.PI / 180) * 33 + 4}
            text-anchor="middle"
            class="cpt">{model.roseCardinals[i]}</text>
        {/each}
        {#if model.wind.degrees !== null}
          <g style="transform: rotate({model.wind.degrees}deg); transform-origin: 45px 45px;">
            <path d="M45 14 L52 46 L45 40 L38 46 Z" fill="var(--fam)" />
          </g>
        {/if}
      </svg>
      <div class="c-read"><strong class="tnum">{model.wind.value}</strong><span>{model.wind.unit}</span></div>
    </div>
    <div class="wind-sub">
      {#if model.wind.gust}<div><small>{ui(language, 'gust')}</small><b>{model.wind.gust}</b></div>{/if}
      <div><small>{ui(language, 'direction')}</small><b>{model.wind.direction}</b></div>
    </div>
  </article>

  <!-- Precipitación -->
  <article class="tile precip t-c" style:--fam={families.precip.color}>
    <header>
      <span class="ic"><Icon name="CloudRain" size={17} /></span>
      <h3>{t(language, 'obs_precipitation')}</h3>
      {#if help('precipitacion hoy')}
        <span class="help" tabindex="0" role="note" aria-label={help('precipitacion hoy')}>?</span>
        <span class="bubble">{help('precipitacion hoy')}</span>
      {/if}
      <span class="chip note">{model.precipitation.label}</span>
    </header>
    <div class="val tnum">{model.precipitation.value}<span>{model.precipitation.unit}</span></div>
    {#if model.precipitation.rate}
      <div class="foot"><span>{ui(language, 'rate_now')}</span><b>{model.precipitation.rate}</b></div>
    {/if}
  </article>

  <!-- Presión -->
  <article class="tile press t-wide" style:--fam={families.pressure.color}>
    <div class="pw-left">
      <header><span class="ic"><Icon name="Gauge" size={17} /></span><h3>{t(language, 'obs_pressure')}</h3>
      {#if help('presion')}
        <span class="help" tabindex="0" role="note" aria-label={help('presion')}>?</span>
        <span class="bubble">{help('presion')}</span>
      {/if}</header>
      <div class="val tnum">{model.pressure.value}<span>{model.pressure.unit}</span></div>
    </div>
    <div class="press-stats">
      <span>
        <small>{ui(language, 'trend')}</small>
        <b class={model.pressure.trend.direction}>{model.pressure.trend.arrow} {model.pressure.trend.label}</b>
      </span>
      {#if model.pressure.delta3h}
        <span><small>{ui(language, 'delta3h')}</small><b class="tnum">{model.pressure.delta3h}</b></span>
      {/if}
      {#if model.pressure.absolute}
        <span><small>{ui(language, 'absolute')}</small><b class="tnum">{model.pressure.absolute}</b></span>
      {/if}
    </div>
  </article>

  <!-- UV / radiación -->
  {#if model.radiationTile.present}
    <article class="tile uv t-d" style:--fam={families.radiation.color}>
      <header>
        <span class="ic"><Icon name="SunMedium" size={17} /></span>
        <h3>{model.radiationTile.title}</h3>
      </header>
      <div class="val tnum">{model.radiationTile.value}<span>{model.radiationTile.unit}</span></div>
      {#if model.radiationTile.footValue}
        <div class="foot"><span>{model.radiationTile.footLabel}</span><b>{model.radiationTile.footValue}</b></div>
      {/if}
    </article>
  {/if}
</div>

<!-- ══ TERMODINÁMICA ══ -->
{#if model.thermo.length}
  <div class="sec-head">
    <h2>{ui(language, 'section_thermo')}</h2>
    <span class="rule"></span>
  </div>
  <div class="grid compact">
    {#each model.thermo as metric (metric.title)}<MetricCard {metric} />{/each}
  </div>
{/if}

<!-- ══ RADIACIÓN ══ -->
{#if model.radiation.length}
  <div class="sec-head">
    <h2>{ui(language, 'section_radiation')}</h2>
    <span class="rule"></span>
  </div>
  <div class="grid compact">
    {#each model.radiation as metric (metric.title)}<MetricCard {metric} />{/each}
  </div>
{/if}

<!-- ══ GRÁFICOS ══ -->
{#if hasCharts}
  <div class="sec-head">
    <h2>{ui(language, 'section_charts')}</h2>
    <span class="rule"></span>
  </div>
  <div class="charts">
    {#if charts.temperature}
      <section class="chart-card wide">
        <header><h3>{ui(language, 'chart_temperature')}</h3></header>
        <TrendChart
          series={[{ data: charts.temperature.data[0], color: families.temperature.color }]}
          labels={charts.temperature.labels}
          unit={model.units.temperature}
          epochs={charts.temperature.epochs}
          {activeEpoch}
          {onHover}
          formatValue={(value) => tick(value, 1)}
          {formatEpoch}
          exportName={pngName('chart_temperature')}
          exportLabel={ui(language, 'download_png')}
          formatTick={tick}
          height={180}
        />
      </section>
    {/if}

    {#if charts.vapour}
      <section class="chart-card">
        <header>
          <h3>{ui(language, 'chart_vapor')}</h3>
          <div class="legend">
            <span><i style:background={families.humidity.color}></i>e</span>
            <span><i class="dash" style:--c={families.humidity.color}></i>e_s</span>
          </div>
        </header>
        <TrendChart
          series={[
            { data: charts.vapour.data[0], color: families.humidity.color },
            { data: charts.vapour.data[1], color: families.humidity.color, dash: true }
          ]}
          labels={charts.vapour.labels}
          unit={model.units.pressure}
          epochs={charts.vapour.epochs}
          {activeEpoch}
          {onHover}
          formatValue={(value) => tick(value, 1)}
          {formatEpoch}
          exportName={pngName('chart_vapor')}
          exportLabel={ui(language, 'download_png')}
          formatTick={tick}
          fillArea={false}
          height={176}
        />
      </section>
    {/if}

    {#if charts.precipitation}
      <section class="chart-card">
        <header><h3>{ui(language, 'chart_precip')}</h3></header>
        <TrendChart
          series={[{ data: charts.precipitation.data[0], color: families.precip.color }]}
          labels={charts.precipitation.labels}
          unit={model.units.precip}
          epochs={charts.precipitation.epochs}
          {activeEpoch}
          {onHover}
          formatValue={(value) => tick(value, 1)}
          {formatEpoch}
          exportName={pngName('chart_precip')}
          exportLabel={ui(language, 'download_png')}
          formatTick={tick}
          height={176}
        />
      </section>
    {/if}

    {#if charts.wind}
      <section class="chart-card wide">
        <header>
          <h3>{ui(language, 'chart_wind')}</h3>
          <div class="legend">
            <button type="button" class:off={!windSeries.speed} onclick={() => toggleWind('speed')}>
              <i style:background="#2f7fd6"></i>{t(language, 'obs_wind')}
            </button>
            <button type="button" class:off={!windSeries.gust} onclick={() => toggleWind('gust')}>
              <i class="dash" style:--c="#37c8d6"></i>{ui(language, 'gust')}
            </button>
            {#if model.charts.windDirection}
              <button type="button" class:off={!windSeries.dir} onclick={() => toggleWind('dir')}>
                <i class="dot"></i>{ui(language, 'direction')}
              </button>
            {/if}
          </div>
        </header>
        <WindChart
          unit={model.units.wind}
          labels={charts.wind.labels}
          epochs={charts.wind.epochs}
          {activeEpoch}
          {onHover}
          seriesLabels={{
            speed: ui(language, 'mean_wind'),
            gust: ui(language, 'gust'),
            dir: ui(language, 'direction')
          }}
          formatValue={tick}
          {formatEpoch}
          visible={windSeries}
          speed={charts.wind.data[0]}
          gust={charts.wind.data[1]}
          dir={charts.windDirection ? charts.windDirection.data[2] : []}
          exportName={pngName('chart_wind')}
          exportLabel={ui(language, 'download_png')}
          height={180}
        />
      </section>
    {/if}

    {#if model.rose}
      <section class="chart-card rose">
        <header><h3>{ui(language, 'chart_rose')}</h3></header>
        <!-- Los cuatro datos, al lado: debajo robaban a la rosa la mitad de
             la altura de la tarjeta, que es lo único que fija su tamaño. -->
        <div class="rose-body">
          <div class="rose-wrap">
            <WindRose
              data={model.rose.data}
              cardinals={model.rose.cardinals}
              frequencyLabel={ui(language, 'rose_frequency')}
              formatPct={(value) => `${tick(value, 0)} %`}
              exportName={pngName('chart_rose')}
              exportLabel={ui(language, 'download_png')}
            />
          </div>
          <div class="rose-stats">
            <span><small>{ui(language, 'rose_dominant')}</small><b>{model.rose.stats.dominant}</b></span>
            <span><small>{ui(language, 'rose_frequency')}</small><b>{model.rose.stats.frequency}</b></span>
            <span><small>{ui(language, 'rose_samples')}</small><b>{model.rose.stats.samples}</b></span>
            <span>
              <small>{String(ui(language, 'rose_calm')).replace(/<.*$/, model.rose.stats.calmThreshold || '')}</small>
              <b>{model.rose.stats.calm}</b>
            </span>
          </div>
        </div>
      </section>
    {/if}

    {#if charts.irradiance}
      <section class="chart-card">
        <header>
          <h3>{ui(language, 'chart_irradiance')}</h3>
          <div class="legend">
            <span><i style:background={families.radiation.color}></i>{ui(language, 'legend_measured')}</span>
            <span><i class="dash" style:--c={families.radiation.color}></i>{ui(language, 'legend_theoretical')}</span>
          </div>
        </header>
        <TrendChart
          series={[
            { data: charts.irradiance.data[0], color: families.radiation.color },
            { data: charts.irradiance.data[1], color: families.radiation.color, dash: true }
          ]}
          labels={charts.irradiance.labels}
          unit={model.units.radiation}
          epochs={charts.irradiance.epochs}
          {activeEpoch}
          {onHover}
          formatValue={(value) => tick(value, 1)}
          {formatEpoch}
          exportName={pngName('chart_irradiance')}
          exportLabel={ui(language, 'download_png')}
          formatTick={tick}
          fillArea={false}
          height={176}
        />
      </section>
    {/if}
  </div>
{/if}

<style>
  /* Leyenda que enciende y apaga su serie. */
  .legend button {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 6px; border: 0; border-radius: 6px;
    background: transparent; color: var(--muted);
    font: inherit; font-size: 0.68rem; cursor: pointer;
  }
  .legend button:hover { color: var(--ink-2); background: var(--card); }
  .legend button.off { opacity: 0.42; text-decoration: line-through; }

  .extremes {
    margin-left: auto; display: grid;
    grid-template-columns: 0.72rem max-content minmax(2.5rem, max-content);
    column-gap: 3px; row-gap: 1px;
    font-size: 0.7rem; font-weight: 600; color: var(--muted); line-height: 1.35;
  }
  .extremes > span { display: contents; }
  .extremes i {
    display: block; width: 0.72rem; text-align: center;
    font-style: normal; font-size: 0.62rem;
  }
  .extremes .extreme-label { text-align: left; }
  .extremes .up i { color: #e8686b; }
  .extremes .down i { color: #4db6e8; }
  .extremes b { color: var(--ink-2); font-weight: 700; text-align: right; }

  /* Ayuda de la tarjeta: el mismo texto que enseña la app actual al posarse
     en el interrogante. La tarjeta se vuelve el ancla de la burbuja. */
  .tile { position: relative; }
  .help {
    display: grid; place-items: center;
    width: 15px; height: 15px; flex: none;
    border-radius: 50%; border: 1px solid var(--border-2);
    color: var(--muted-2); font-size: 0.6rem; font-weight: 700; cursor: help;
  }
  .help:hover, .help:focus-visible { color: var(--ink-2); border-color: var(--accent); }
  .bubble {
    /* Se sale de la tarjeta a propósito: hay definiciones de varias líneas y
       tarjetas de dos centímetros de alto. Ancho propio, no el de la tarjeta. */
    position: absolute; z-index: 40; left: 14px; top: 44px;
    width: max-content; max-width: min(320px, calc(100vw - 40px));
    padding: 10px 12px; border: 1px solid var(--border-2); border-radius: 10px;
    background: var(--panel); box-shadow: var(--shadow);
    font-size: 0.72rem; line-height: 1.45; color: var(--ink-2);
    white-space: pre-line;
    opacity: 0; visibility: hidden; transition: opacity 0.12s;
  }
  .help:hover ~ .bubble, .help:focus-visible ~ .bubble { opacity: 1; visibility: visible; }
  /* La tarjeta recorta lo que se sale —así el filo de color respeta la esquina
     redondeada—, salvo mientras se lee una definición. */
  .tile:has(.help:hover), .tile:has(.help:focus-visible) { overflow: visible; z-index: 40; }

  .sec-head { display: flex; align-items: center; gap: 14px; margin: 6px 0 15px; }
  .sec-head h2 { font-size: 0.96rem; font-weight: 700; letter-spacing: -0.01em; }
  .rule { height: 1px; flex: 1; background: var(--border); }
  .meta { font-size: 0.72rem; color: var(--muted); }

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

  .t-hero .hero-val { font-size: 3.9rem; font-weight: 720; line-height: 0.95; letter-spacing: -0.04em; margin-top: 6px; }
  /* El titular va con `letter-spacing` negativo para apretar sus cifras
     enormes; heredado, ese apretón pegaba el grado a la C. */
  .t-hero .hero-val span {
    font-size: 1.3rem; font-weight: 600; color: var(--muted);
    margin-left: 7px; letter-spacing: 0;
  }
  .hero-feels { margin-top: 8px; font-size: 0.82rem; color: var(--ink-2); }
  .hero-feels strong { color: var(--ink); font-weight: 700; }
  .hero-bottom {
    display: grid; grid-template-columns: minmax(0, 340px) minmax(170px, 1fr);
    align-items: end; gap: 18px; margin-top: auto; padding-top: 12px;
  }
  .hero-spark { min-width: 0; }
  .hero-spark :global(svg) { display: block; width: 100%; height: auto; }
  .alert-band {
    margin: 0 0 12px; padding: 10px 14px;
    border: 1px solid color-mix(in srgb, var(--chip-warn-fg) 45%, transparent);
    border-left-width: 4px; border-radius: 10px;
    background: var(--chip-warn-bg); color: var(--chip-warn-fg);
    font-size: 0.78rem; font-weight: 700; line-height: 1.4;
  }
  .alert-band b { margin-right: 6px; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.72rem; }
  .alert-band.danger {
    border-color: var(--alert-danger-border);
    background: var(--alert-danger-bg); color: var(--alert-danger-fg);
  }

  .hero-risk {
    margin-top: 6px; font-size: 0.78rem; font-weight: 700;
    color: var(--chip-warn-fg);
  }
  .hero-risk.danger { color: var(--alert-danger-fg); }


  .wind .compass { display: flex; align-items: center; gap: 14px; margin: 4px 0; }
  .cpt { fill: var(--muted); font-size: 9px; font-weight: 700; font-family: var(--font); }
  .c-read { display: flex; flex-direction: column; }
  .c-read strong { font-size: 1.8rem; font-weight: 700; line-height: 1; letter-spacing: -0.03em; }
  .c-read span { font-size: 0.74rem; color: var(--muted); }
  .wind-sub { display: flex; gap: 18px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--border); }
  .wind-sub div { display: flex; flex-direction: column; gap: 2px; }
  .wind-sub small { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .wind-sub b { font-size: 0.82rem; font-weight: 640; }

  .t-wide { flex-direction: row; align-items: center; gap: 26px; }
  .pw-left { display: flex; flex-direction: column; }
  .pw-left .val { margin-top: 8px; }
  .press-stats { display: flex; gap: 30px; margin-left: auto; }
  .press-stats span { display: flex; flex-direction: column; gap: 4px; }
  .press-stats small { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .press-stats b { font-size: 0.9rem; font-weight: 660; }
  .press-stats b.up { color: #43c98a; }
  .press-stats b.down { color: #e8686b; }

  .grid { display: grid; gap: 13px; margin-bottom: 30px; }
  .grid.compact { grid-template-columns: repeat(4, 1fr); }

  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 30px; }
  .chart-card { padding: 16px 18px 12px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 4px; }
  .chart-card h3 { font-size: 0.84rem; font-weight: 660; }
  .axis-name { display: block; font-size: 0.64rem; color: var(--muted-2); font-family: var(--mono); margin: 2px 0; }
  .legend { display: flex; gap: 13px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.68rem; color: var(--muted); white-space: nowrap; }
  .legend i { width: 11px; height: 3px; border-radius: 2px; }
  .legend i.dash { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); }
  .legend i.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }

  .chart-card.rose { display: flex; flex-direction: column; }
  .rose-body { display: flex; align-items: center; gap: 18px; flex: 1; padding: 6px 0 4px; }
  .rose-wrap { flex: 1 1 auto; min-width: 0; display: grid; place-items: center; }
  .rose-wrap :global(svg.rose) { max-width: 300px; }
  .rose-stats {
    flex: 0 0 auto; display: grid; grid-template-columns: 1fr; gap: 12px 16px;
    padding-left: 16px; border-left: 1px solid var(--border);
  }
  .rose-stats span { display: flex; flex-direction: column; gap: 2px; }
  .rose-stats small { font-size: 0.62rem; color: var(--muted); }
  .rose-stats b { font-size: 0.86rem; font-weight: 660; }

  @media (max-width: 1080px) {
    .bento { grid-auto-rows: 118px; }
    .grid.compact { grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 760px) {
    .rose-body { flex-direction: column; align-items: stretch; gap: 12px; }
    .rose-stats {
      grid-template-columns: 1fr 1fr; padding-left: 0; padding-top: 12px;
      border-left: none; border-top: 1px solid var(--border);
    }
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
    .hero-bottom { grid-template-columns: minmax(0, 1fr) minmax(155px, 0.8fr); gap: 12px; }
  }
  @media (max-width: 440px) {
    .bento { grid-template-columns: 1fr; grid-template-areas: "temp" "temp" "hum" "dew" "wind" "precip" "uv" "press"; }
    .grid.compact { grid-template-columns: 1fr; }
    .hero-bottom { grid-template-columns: minmax(0, 1fr) minmax(130px, 0.85fr); gap: 8px; }
    .alert-band { padding: 8px 10px; font-size: 0.7rem; }
  }
</style>

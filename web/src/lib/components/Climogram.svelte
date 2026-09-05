<script>
  import ChartFrame from './ChartFrame.svelte';
  import Watermark from './Watermark.svelte';
  import { niceStep, niceTicks, tickDecimals } from '$lib/observation/scale.js';
  /**
   * Climograma: barras de precipitación (eje derecho) y líneas de temperatura
   * (eje izquierdo). El mismo gráfico que la app actual dibuja con Plotly,
   * aquí en SVG para que salga renderizado desde el servidor.
   *
   * Los huecos se toleran: una serie con `null` sigue dibujándose, saltando
   * los puntos que el proveedor no publicó en vez de hundirlos a cero.
   */
  let {
    months = [],
    tmax = [],
    tmin = [],
    tmean = [],
    precip = [],
    temperatureUnit = '°C',
    precipUnit = 'mm',
    formatTick = (value) => value.toFixed(0),
    // El eje va con números redondos, pero la lectura del punto lleva decimal:
    // ahí se está mirando un dato concreto, no una escala.
    formatValue = null,
    label = 'Climograma',
    exportName = 'meteolabx-climograma',
    exportLabel = 'Descargar PNG',
    // Nombres de las cuatro series para la lectura al pasar el ratón. Vienen
    // traducidos: el climograma se publica en los seis idiomas.
    seriesLabels = {
      precip: 'Precipitación',
      tmax: 'Máxima',
      tmean: 'Media',
      tmin: 'Mínima'
    }
  } = $props();

  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  const clean = (values) => values.filter(isNumber);

  const W = 640;
  const CHART_H = 214; // alto del área de dibujo, sin el eje de fechas
  const LEFT_PAD = 52;
  const RIGHT_PAD = 60;
  const AXIS_STEPS = 6;

  /**
   * Fechas en vertical cuando no caben de lado.
   *
   * Un mes diario son 31 etiquetas en 560 px: de lado se pisan, y lo que se
   * hacía era enseñar una de cada tres. Giradas caben todas, que es lo que
   * interesa para leer un climograma día a día. Con pocas —doce meses— se
   * dejan horizontales, que se leen mejor.
   */
  const labelChars = $derived(
    months.reduce((longest, month) => Math.max(longest, String(month).length), 0)
  );
  const iw = W - LEFT_PAD - RIGHT_PAD;
  // El eje va en monoespaciada de 10 px: unos 6 px por carácter, más un
  // hueco mínimo para que dos etiquetas seguidas no se toquen.
  const labelWidth = $derived(labelChars * 6 + 8);
  const upright = $derived(months.length > 1 && iw / months.length < labelWidth);

  // Girada, la etiqueta ocupa a lo largo lo que mide su texto.
  const axisSpace = $derived(upright ? Math.round(labelChars * 6) + 16 : 30);
  const pad = $derived({ t: 16, r: RIGHT_PAD, b: axisSpace, l: LEFT_PAD });
  const H = $derived(CHART_H + axisSpace);
  const ih = CHART_H - 16;

  const temps = $derived(clean([...tmin, ...tmax, ...tmean]));
  const tLo = $derived((temps.length ? Math.min(...temps) : 0) - 3);
  const tHi = $derived((temps.length ? Math.max(...temps) : 10) + 3);
  const rain = $derived(clean(precip));
  const pMax = $derived((rain.length ? Math.max(...rain) : 0) * 1.15 || 1);

  const n = $derived(months.length || 1);
  const bw = $derived((iw / n) * 0.56);
  const xCenter = (i) => pad.l + (i + 0.5) * (iw / n);
  const yT = (v) => pad.t + ih - ((v - tLo) / (tHi - tLo)) * ih;
  const yP = (v) => pad.t + ih - (v / pMax) * ih;

  /** Traza saltando los huecos: cada tramo continuo arranca con su propio M. */
  function tPath(values) {
    let path = '';
    let drawing = false;
    values.forEach((value, index) => {
      if (!isNumber(value)) {
        drawing = false;
        return;
      }
      path += `${drawing ? 'L' : 'M'}${xCenter(index).toFixed(1)} ${yT(value).toFixed(1)} `;
      drawing = true;
    });
    return path.trim();
  }
  const tTicks = $derived(niceTicks(tLo, tHi, AXIS_STEPS));
  const pTicks = $derived(niceTicks(0, pMax, AXIS_STEPS));
  const tDecimals = $derived(tickDecimals(niceStep(tHi - tLo, AXIS_STEPS)));
  const pDecimals = $derived(tickDecimals(niceStep(pMax, AXIS_STEPS)));
  // Con una serie diaria las etiquetas del eje X se pisan: se muestran
  // salteadas para dejar unas doce como mucho.
  const labelStep = $derived(Math.max(1, Math.ceil(months.length / 12)));

  // --- Lectura al pasar el ratón -------------------------------------------
  // El eje X de un climograma es discreto —meses o días—, así que el punto
  // señalado es el más cercano en horizontal, sin tolerancias: siempre hay
  // uno debajo del cursor.
  let svg;
  let active = $state(null);

  function pointerMove(event) {
    const rect = svg?.getBoundingClientRect();
    if (!rect?.width || !months.length) return;
    const x = ((event.clientX - rect.left) / rect.width) * W;
    const slot = Math.floor(((x - pad.l) / iw) * n);
    active = Math.max(0, Math.min(n - 1, slot));
  }

  /** Las series que tienen dato en el punto señalado, con su color. */
  const reading = $derived.by(() => {
    if (active === null) return null;
    const entries = [
      { key: 'precip', color: 'var(--precip, #5b9bff)', value: precip[active], unit: precipUnit },
      { key: 'tmax', color: '#ff8a4c', value: tmax[active], unit: temperatureUnit },
      { key: 'tmean', color: '#c9d2dc', value: tmean[active], unit: temperatureUnit },
      { key: 'tmin', color: '#4db6e8', value: tmin[active], unit: temperatureUnit }
    ].filter((entry) => isNumber(entry.value));
    if (!entries.length) return null;
    return { label: months[active] || '', entries };
  });

  /**
   * Ancho de la caja, medido por su contenido.
   *
   * Los nombres de las series son de longitud muy distinta según el idioma
   * —«Media de máximas» frente a «Máx.»—, así que un ancho fijo hace que la
   * etiqueta se meta debajo del número. Se estima a partir del texto más
   * largo: aquí no hay acceso a métricas de fuente, pero a 9,5 px un carácter
   * ocupa algo más de 5, y sobrar un poco de caja no se nota.
   */
  const readValue = $derived(formatValue || formatTick);

  const boxWidth = $derived.by(() => {
    if (!reading) return 140;
    const label = Math.max(...reading.entries.map((entry) => seriesLabels[entry.key]?.length || 0));
    const value = Math.max(
      reading.label.length,
      ...reading.entries.map((entry) => `${readValue(entry.value)}${entry.unit}`.length)
    );
    // 23 hasta la etiqueta + hueco de 16 entre etiqueta y número + margen.
    return Math.min(320, Math.max(140, 23 + label * 5.3 + 16 + value * 6.1 + 10));
  });

  // La caja se pinta al lado contrario del cursor para no taparle el punto, y
  // sin salirse del lienzo.
  const boxX = $derived.by(() => {
    if (active === null) return 0;
    const x = xCenter(active);
    const left = x > pad.l + iw / 2 ? x - boxWidth - 10 : x + 10;
    return Math.max(4, Math.min(W - boxWidth - 4, left));
  });
</script>

<ChartFrame name={exportName} label={exportLabel}>
<svg
  viewBox="0 0 {W} {H}"
  class="climo"
  role="img"
  aria-label={label}
  bind:this={svg}
  onpointermove={pointerMove}
  onpointerleave={() => (active = null)}
>
  {#each tTicks as tv}
    <line x1={pad.l} x2={W - pad.r} y1={yT(tv)} y2={yT(tv)} stroke="var(--grid-line)" />
    <text x={pad.l - 7} y={yT(tv) + 3.5} class="axis" text-anchor="end">{formatTick(tv, tDecimals)}</text>
  {/each}
  <text
    class="axis-unit"
    transform="rotate(-90 11 {pad.t + ih / 2})"
    x="11" y={pad.t + ih / 2} text-anchor="middle">{temperatureUnit}</text>

  <!-- Eje derecho: precipitación. -->
  {#each pTicks as pv}
    <text x={W - pad.r + 24} y={yP(pv) + 3.5} class="axis rain" text-anchor="start">{formatTick(pv, pDecimals)}</text>
  {/each}
  <text
    class="axis-unit rain"
    transform="rotate(-90 {W - pad.r + 9} {pad.t + ih / 2})"
    x={W - pad.r + 9} y={pad.t + ih / 2} text-anchor="middle">{precipUnit}</text>

  {#each precip as value, i}
    {#if isNumber(value)}
      <rect
        x={xCenter(i) - bw / 2}
        y={yP(value)}
        width={bw}
        height={Math.max(0, pad.t + ih - yP(value))}
        rx="3"
        fill="var(--precip, #5b9bff)"
        opacity="0.5" />
    {/if}
  {/each}

  <path d={tPath(tmax)} fill="none" stroke="#ff8a4c" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
  <path d={tPath(tmean)} fill="none" stroke="#c9d2dc" stroke-width="1.8" stroke-dasharray="5 4" stroke-linecap="round" stroke-linejoin="round" opacity="0.85" />
  <path d={tPath(tmin)} fill="none" stroke="#4db6e8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />

  <!-- Los puntos solo se pintan cuando la serie es corta; con un mes diario
       ya son treinta y uno y ensucian más de lo que ayudan. -->
  {#if months.length <= 14}
    {#each tmax as value, i}{#if isNumber(value)}<circle cx={xCenter(i)} cy={yT(value)} r="2.6" fill="#ff8a4c" />{/if}{/each}
    {#each tmin as value, i}{#if isNumber(value)}<circle cx={xCenter(i)} cy={yT(value)} r="2.6" fill="#4db6e8" />{/if}{/each}
  {/if}

  {#each months as month, i}
    {#if upright}
      <text
        x={xCenter(i)}
        y={pad.t + ih + 10}
        class="axis"
        text-anchor="end"
        transform="rotate(-90, {xCenter(i)}, {pad.t + ih + 10})">{month}</text>
    {:else if labelStep === 1 || i % labelStep === 0}
      <text x={xCenter(i)} y={H - 9} class="axis" text-anchor="middle">{month}</text>
    {/if}
  {/each}

  <!-- Lectura del punto bajo el cursor -->
  {#if reading}
    <line
      x1={xCenter(active)} x2={xCenter(active)} y1={pad.t} y2={pad.t + ih}
      stroke="var(--border-2)" stroke-width="1.4" />
    {#each reading.entries as entry (entry.key)}
      <circle
        cx={xCenter(active)}
        cy={entry.key === 'precip' ? yP(entry.value) : yT(entry.value)}
        r="3.4" fill={entry.color} stroke="var(--panel)" stroke-width="1.4" />
    {/each}

    <g transform="translate({boxX}, {pad.t + 6})">
      <rect
        width={boxWidth} height={24 + reading.entries.length * 15}
        rx="8" class="box" />
      <text x="10" y="15" class="box-title">{reading.label}</text>
      {#each reading.entries as entry, index (entry.key)}
        <circle cx="14" cy={28 + index * 15} r="3.2" fill={entry.color} />
        <text x="23" y={31 + index * 15} class="box-label">{seriesLabels[entry.key]}</text>
        <text x={boxWidth - 10} y={31 + index * 15} class="box-value" text-anchor="end">
          {readValue(entry.value)}{entry.unit}
        </text>
      {/each}
    </g>
  {/if}

  <Watermark x={W - pad.r - 3} y={pad.t + ih - 6} />
</svg>
</ChartFrame>

<style>
  .box { fill: var(--panel); stroke: var(--border-2); stroke-width: 1; }
  .box-title { fill: var(--ink); font-size: 10px; font-weight: 700; }
  .box-label { fill: var(--muted); font-size: 9.5px; }
  .box-value { fill: var(--ink-2); font-size: 9.5px; font-weight: 700; font-variant-numeric: tabular-nums; }

  .climo { width: 100%; height: auto; display: block; }
  .axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
  .axis.rain { fill: var(--muted-2); }
  .axis-unit { fill: var(--muted-2); font-size: 9.5px; font-family: var(--mono); letter-spacing: 0.04em; }
</style>

<script>
  /**
   * Barra de color del campo interpolado.
   *
   * Los cortes salen de `app-i18n.generated.js`, que los exporta desde los
   * mismos módulos del backend que pintan los PNG. Una leyenda con una escala
   * propia acabaría mintiendo sobre lo que se está viendo en el mapa.
   *
   * El eje es lineal en valor —como el de la aplicación actual—, así que cada
   * corte se coloca en su posición real y no repartidos a partes iguales.
   */
  import app from '$lib/i18n/app-i18n.generated.js';
  import { num } from '$lib/format.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { convertUnit, unitLabel } from '$lib/units.js';

  let { layer, language } = $props();

  const LEGEND_KEY = {
    temperature: 'temp_field_legend',
    wind: 'wind_field_legend',
    precipitation: 'precip_field_legend'
  };

  // Marcas del eje. La precipitación se corta antes: por encima de 50 mm el
  // tramo es tan largo que las etiquetas se amontonan al final.
  const TICKS = {
    temperature: [-20, -10, 0, 10, 20, 30, 40],
    wind: [0, 20, 40, 60, 80, 110, 150],
    precipitation: [0, 1, 5, 20, 50, 100, 200]
  };

  const stops = $derived(app.field_scales?.[layer] || []);
  const family = $derived(layer === 'temperature' ? 'temperature' : layer === 'wind' ? 'wind' : 'precip');
  const label = $derived(
    String(app.map[language]?.[LEGEND_KEY[layer]] || '').replace(
      /\s*\([^)]*\)\s*$/,
      ` (${unitLabel(family, unitPreferences)})`
    )
  );
  const ticks = $derived(TICKS[layer] || []);

  const low = $derived(stops.length ? stops[0][0] : 0);
  const high = $derived(stops.length ? stops[stops.length - 1][0] : 1);
  const position = (value) => ((value - low) / (high - low || 1)) * 100;

  const gradient = $derived(
    stops
      .map(([value, [r, g, b]]) => `rgb(${r},${g},${b}) ${position(value).toFixed(2)}%`)
      .join(', ')
  );

  const decimals = $derived(layer === 'precipitation' ? 0 : 0);
</script>

{#if stops.length}
  <figure class="legend">
    <figcaption>{label}</figcaption>
    <div class="bar" style:background="linear-gradient(90deg, {gradient})"></div>
    <div class="ticks">
      {#each ticks as tick (tick)}
        <span style:left="{position(tick)}%">{num(convertUnit(tick, family, unitPreferences), { language, decimals: family === 'precip' && unitPreferences.precip === 'in' ? 2 : decimals })}</span>
      {/each}
    </div>
  </figure>
{/if}

<style>
  .legend { margin: 14px 0 0; }
  figcaption { font-size: 0.76rem; font-weight: 650; color: var(--ink-2); margin-bottom: 7px; }
  .bar { height: 10px; border-radius: 999px; border: 1px solid var(--border); }
  .ticks { position: relative; height: 18px; margin-top: 4px; }
  .ticks span {
    position: absolute; transform: translateX(-50%);
    font-size: 0.7rem; color: var(--muted); font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
</style>

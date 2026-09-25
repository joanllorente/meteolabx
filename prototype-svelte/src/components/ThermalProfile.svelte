<script>
  import { X } from '@lucide/svelte';

  let { profile, language = 'es', onclose } = $props();
  const copy = $derived(language === 'en' ? {
    title: 'Vertical temperature profile', temperature: 'Temperature', dewpoint: 'Dew point',
    wetBulb: 'Wet-bulb temperature', pressure: 'Pressure (hPa)', crosses: 'Isotherm crossings',
    highest: 'Highest crossing shown on the map', close: 'Close profile', noCrosses: 'No crossings in this profile'
  } : {
    title: 'Perfil vertical de temperatura', temperature: 'Temperatura', dewpoint: 'Punto de rocío',
    wetBulb: 'Bulbo húmedo', pressure: 'Presión (hPa)', crosses: 'Cruces de la isoterma',
    highest: 'Cruce más alto mostrado en el mapa', close: 'Cerrar perfil', noCrosses: 'No hay cruces en este perfil'
  });
  const isSnow = $derived(profile.product === 'snow-level');
  const pressures = [1000, 850, 700, 500, 300, 200, 100];
  const temperatures = [-80, -60, -40, -20, 0, 20, 40];
  const left = 78;
  const top = 25;
  const bottom = 477;
  const xScale = 3.25;
  const skew = 82;
  const pressureSpan = Math.log(1050 / 100);

  function yOf(pressure) {
    return bottom - Math.log(1050 / pressure) / pressureSpan * (bottom - top);
  }

  function xOf(temperature, pressure) {
    return left + (temperature + 90) * xScale + Math.log(1050 / pressure) / pressureSpan * skew;
  }

  function linePath(key) {
    return profile.levels.filter((level) => Number.isFinite(level[key]) && level.pressure_hpa >= 100)
      .map((level, index) => `${index ? 'L' : 'M'}${xOf(level[key], level.pressure_hpa).toFixed(1)},${yOf(level.pressure_hpa).toFixed(1)}`)
      .join(' ');
  }

  const sortedCrossings = $derived([...profile.crossings].sort((a, b) => b.height_m - a.height_m));
</script>

<svelte:window onkeydown={(event) => { if (event.key === 'Escape') onclose?.(); }} />

<div class="profile-backdrop">
  <dialog open class="profile-dialog" aria-modal="true" aria-label={copy.title}>
    <header>
      <div>
        <h3>{copy.title}</h3>
        <p>{profile.latitude.toFixed(3)}° N · {profile.longitude.toFixed(3)}° E · {new Date(profile.valid_time).toLocaleString(language === 'en' ? 'en-GB' : 'es-ES', { timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} UTC</p>
      </div>
      <button type="button" onclick={onclose} aria-label={copy.close}><X size={18} /></button>
    </header>
    <div class="chart-wrap">
      <svg viewBox="0 0 620 510" role="img" aria-label={`${copy.title}. ${copy.crosses}: ${sortedCrossings.map((cross) => Math.round(cross.height_m)).join(', ')} m`}>
        <rect x="78" y="25" width="485" height="452" fill="#101d2c" />
        {#each pressures as pressure}
          <line class="isobar" x1="78" x2="563" y1={yOf(pressure)} y2={yOf(pressure)} />
          <text class="axis-label" x="68" y={yOf(pressure) + 4} text-anchor="end">{pressure}</text>
        {/each}
        <text class="axis-label" x="18" y="260" text-anchor="middle" transform="rotate(-90 18 260)">{copy.pressure}</text>
        {#each temperatures as temperature}
          <line class="isotherm" x1={xOf(temperature, 1050)} y1={bottom} x2={xOf(temperature, 100)} y2={top} />
          <text class="axis-label" x={xOf(temperature, 1050)} y="495" text-anchor="middle">{temperature}°</text>
        {/each}
        <path class="temperature" d={linePath('temperature_c')} />
        {#if isSnow}
          <path class="dewpoint" d={linePath('dewpoint_c')} />
          <path class="wet-bulb" d={linePath('wet_bulb_c')} />
        {/if}
        {#each sortedCrossings as crossing, index}
          <circle class="crossing" cx={xOf(profile.threshold_c, crossing.pressure_hpa)} cy={yOf(crossing.pressure_hpa)} r="5" />
          <text class="crossing-label" x={xOf(profile.threshold_c, crossing.pressure_hpa) + 10} y={yOf(crossing.pressure_hpa) - (index % 2 ? 8 : 0)}>{Math.round(crossing.height_m)} m</text>
        {/each}
      </svg>
    </div>
    <div class="curve-legend">
      <span><i class="t"></i>{copy.temperature}</span>
      {#if isSnow}<span><i class="td"></i>{copy.dewpoint}</span><span><i class="tw"></i>{copy.wetBulb}</span>{/if}
      <span><i class="mark"></i>{isSnow ? 'Tw = 0,5 °C' : 'T = 0 °C'}</span>
    </div>
    <div class="crossing-summary">
      <strong>{copy.crosses}</strong>
      {#if sortedCrossings.length}
        <p>{sortedCrossings.map((cross) => `${Math.round(cross.height_m)} m`).join(' · ')}</p>
        <small>{copy.highest}: {Math.round(sortedCrossings[0].height_m)} m</small>
      {:else}<p>{copy.noCrosses}</p>{/if}
    </div>
  </dialog>
</div>

<style>
  .profile-backdrop{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;padding:16px;background:rgba(2,9,17,.72);backdrop-filter:blur(6px)}
  .profile-dialog{position:relative;margin:0;width:min(680px,100%);max-height:calc(100vh - 32px);overflow:auto;border:1px solid rgba(163,199,227,.25);border-radius:15px;color:#eaf2f8;background:#142235;box-shadow:0 25px 80px rgba(0,0,0,.42)}
  header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding:16px 18px 10px}h3{margin:0;font-size:1rem}header p{margin:5px 0 0;color:#a9bfd0;font-size:.72rem}header button{display:grid;place-items:center;width:30px;height:30px;border:1px solid rgba(255,255,255,.18);border-radius:8px;color:#eaf2f8;background:rgba(255,255,255,.07);cursor:pointer}
  .chart-wrap{padding:0 8px}.chart-wrap svg{display:block;width:100%;height:auto}.isobar{stroke:#557087;stroke-width:1;opacity:.62}.isotherm{stroke:#436077;stroke-width:1;opacity:.5}.axis-label{fill:#a8bfd0;font-size:11px}.temperature,.dewpoint,.wet-bulb{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.temperature{stroke:#f17e75}.dewpoint{stroke:#83c99d}.wet-bulb{stroke:#70c6ed}.crossing{fill:#ffd56b;stroke:#152235;stroke-width:2}.crossing-label{fill:#ffe5a0;font-size:12px;font-weight:700;paint-order:stroke;stroke:#142235;stroke-width:3px}
  .curve-legend{display:flex;flex-wrap:wrap;gap:8px 17px;padding:0 20px 14px;font-size:.69rem}.curve-legend span{display:flex;align-items:center;gap:6px}.curve-legend i{display:block;width:15px;height:3px;border-radius:3px}.curve-legend .t{background:#f17e75}.curve-legend .td{background:#83c99d}.curve-legend .tw{background:#70c6ed}.curve-legend .mark{width:9px;height:9px;border-radius:50%;background:#ffd56b}
  .crossing-summary{padding:12px 20px 18px;border-top:1px solid rgba(255,255,255,.1)}.crossing-summary strong{font-size:.76rem}.crossing-summary p{margin:6px 0;color:#ffe5a0;font-size:.8rem}.crossing-summary small{color:#a9bfd0;font-size:.68rem}
</style>

<script>
  import { navigatingTo } from '$lib/navigation.js';
  /**
   * Ranking diario: cuatro tarjetas de resumen y el top de la elegida.
   *
   * Las cuatro métricas llegan en la misma respuesta, así que cada tarjeta
   * puede enseñar ya su número uno sin pedir nada más. Antes el ámbito global
   * quedaba escondido dentro del desplegable de países; ahora es lo primero
   * que se elige.
   */
  import Icon from './Icon.svelte';
  import { locale, num } from '$lib/format.js';
  import { ui } from '$lib/i18n/ui.js';
  import app from '$lib/i18n/app-i18n.generated.js';
  import { providerLabel } from '$lib/seo/i18n.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { convertUnit, unitLabel } from '$lib/units.js';

  let { data, language, countryName, links } = $props();

  const METRIC_ICON = { tmax: 'Thermometer', tmin: 'Thermometer', gust: 'Wind', rain: 'CloudRain' };

  const label = (metric) => ui(language, `metric_${metric}`);
  const familyOfMetric = (metric) => metric === 'gust' ? 'wind' : metric === 'rain' ? 'precip' : 'temperature';
  const unit = (metric) => unitLabel(familyOfMetric(metric), unitPreferences);
  const value = (metric, raw) => convertUnit(raw, familyOfMetric(metric), unitPreferences);
  const rowsOf = (metric) => data.ranking.metrics?.[metric] || [];

  const cards = $derived(
    data.metrics.map((metric) => {
      const leader = rowsOf(metric)[0] || null;
      return { metric, label: label(metric), icon: METRIC_ICON[metric], unit: unit(metric), leader };
    })
  );

  const rows = $derived(rowsOf(data.metric));

  /**
   * Días disponibles, en orden cronológico. El backend nunca mezcla husos:
   * una lista es siempre de una sola fecha local, así que moverse por días es
   * saltar de lista en lista.
   */
  const days = $derived(data.ranking.days || []);
  const dayIndex = $derived(days.indexOf(data.ranking.day));
  const previousDay = $derived(dayIndex > 0 ? days[dayIndex - 1] : '');
  const nextDay = $derived(dayIndex >= 0 && dayIndex < days.length - 1 ? days[dayIndex + 1] : '');

  /** La fecha en el idioma de la página; el backend la da como ISO. */
  const dayLabel = $derived((iso) => {
    if (!iso) return '';
    try {
      return new Intl.DateTimeFormat(locale(language), {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC'
      }).format(new Date(`${iso}T12:00:00Z`));
    } catch {
      return iso;
    }
  });
  const scopeName = $derived(data.global ? ui(language, 'scope_global') : countryName(data.country));

  /**
   * El lugar de una estación: provincia y localidad cuando la red las
   * publica, y el país cuando la lista es mundial. Cada fila enseña lo que
   * tiene; ninguna red las trae todas.
   */
  function place(row) {
    const parts = [row.locality, row.region].filter(Boolean);
    const unique = [...new Set(parts)];
    if (data.global && row.country) unique.push(countryName(row.country));
    return unique.join(' · ');
  }

  const decimals = (metric) => metric === 'gust' ? 0 : metric === 'rain' && unitPreferences.precip === 'in' ? 2 : 1;
</script>

<div class="head">
  <div>
    <h2>{ui(language, 'ranking_title')}</h2>
    <p>{ui(language, 'ranking_subtitle', { limit: data.limit })}</p>
  </div>

  <div class="controls">
    <div class="seg" role="group" aria-label={ui(language, 'scope')}>
      <a href={links.scope(false)} class:active={!data.global}>{ui(language, 'scope_country')}</a>
      <a href={links.scope(true)} class:active={data.global}>{ui(language, 'scope_global')}</a>
    </div>

    {#if !data.global}
      <form method="GET">
        <input type="hidden" name="metrica" value={data.metric} />
        <label>
          <span class="sr">{ui(language, 'country')}</span>
          <select name="pais" onchange={(event) => event.currentTarget.form.requestSubmit()}>
            {#each links.countries as entry (entry.code)}
              <option value={entry.code} selected={entry.code === data.country}>{entry.name}</option>
            {/each}
          </select>
        </label>
        <noscript><button type="submit">OK</button></noscript>
      </form>
    {/if}
  </div>
</div>

<!-- Resumen: el número uno de cada métrica, y al pulsar se abre su top. -->
<div class="cards">
  {#each cards as card (card.metric)}
    <a class="card" class:active={card.metric === data.metric} href={links.metric(card.metric)}>
      <header>
        <Icon name={card.icon} size={17} />
        <h3>{card.label}</h3>
        {#if card.metric === data.metric}<span class="dot" aria-hidden="true"></span>{/if}
      </header>
      {#if card.leader}
        <p class="value tnum">
          {num(value(card.metric, card.leader.value), { language, decimals: decimals(card.metric) })}<span>{card.unit}</span>
        </p>
        <p class="who">{card.leader.name}</p>
      {:else}
        <p class="value muted">—</p>
      {/if}
    </a>
  {/each}
</div>

<section class="table">
  <div class="caption">
    <span class="what">
      {label(data.metric)} · {scopeName}
      {#if data.ranking.day}
        <span class="days">
          {#if previousDay}
            <a href={links.day(previousDay)} title={dayLabel(previousDay)} aria-label={dayLabel(previousDay)}>‹</a>
          {:else}
            <span class="off" aria-hidden="true">‹</span>
          {/if}
          <b>{dayLabel(data.ranking.day)}</b>
          {#if nextDay}
            <a href={links.day(nextDay)} title={dayLabel(nextDay)} aria-label={dayLabel(nextDay)}>›</a>
          {:else}
            <span class="off" aria-hidden="true">›</span>
          {/if}
        </span>
      {/if}
    </span>
    <div class="right">
      {#if data.reversible}
        <a class="order" href={links.order()}>
          {data.descending ? '↓' : '↑'}
          {ui(language, data.descending ? 'order_desc' : 'order_asc')}
        </a>
      {/if}
      {#if data.global}
        <a class="order" href={links.antarctica()}>
          {data.withoutAntarctica ? '☑' : '☐'}
          {app.ranking?.[language]?.exclude_antarctica || app.ranking?.es?.exclude_antarctica}
        </a>
      {/if}
    </div>
  </div>

  {#if rows.length}
    <ol>
      {#each rows as row (row.provider + row.station_id)}
        {@const target = row.url_slug
          ? `/${language}/observation/${row.url_slug}`
          : `/${language}/observation/${encodeURIComponent(row.provider)}/${encodeURIComponent(row.station_id)}`}
        <li class:podium={row.rank <= 3} class:busy={navigatingTo(target)}>
          <span class="pos">{row.rank}</span>

          <div class="who">
            <a href={target}>{row.name}</a>
            {#if navigatingTo(target)}<span class="spin" aria-hidden="true"></span>{/if}
            <small>{place(row)}</small>
          </div>

          <span class="prov">{providerLabel(row.provider)}</span>

          <span class="extra">
            {#if row.elevation !== null && row.elevation !== undefined}
              <b class="tnum">{num(row.elevation, { language, decimals: 0 })} m</b>
            {/if}
            {#if row.local_time}
              <b class="time">{row.local_time}</b>
            {/if}
          </span>

          <span class="value tnum">
            {num(value(data.metric, row.value), { language, decimals: decimals(data.metric) })}<i>{unit(data.metric)}</i>
          </span>
        </li>
      {/each}
    </ol>
  {:else}
    <p class="empty">{ui(language, 'ranking_empty')}</p>
  {/if}

  <!-- Los proveedores, al pie: en el ámbito global son doce nombres y en la
       cabecera empujaban el resto de controles fuera de sitio. -->
  {#if data.ranking.providers?.length}
    <footer class="providers">
      {ui(language, 'providers_label')}: {data.ranking.providers.map(providerLabel).join(' · ')}
    </footer>
  {/if}
</section>

<style>
  /* Fila a la que se está conectando. El giro va pegado al nombre, que es lo
     que se ha pulsado: a la derecha de la fila están los datos —el valor, la
     distancia— y taparlos con un indicador es peor que no ponerlo. */
  .spin {
    display: inline-block; width: 11px; height: 11px; margin-left: 8px;
    vertical-align: -1px; flex: none;
    border: 2px solid var(--border-2); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  .busy { opacity: 0.75; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation: none; } }

  .head { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; margin-bottom: 20px; flex-wrap: wrap; }
  .head h2 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
  .head p {
    margin-top: 4px; font-size: 0.8rem; color: var(--muted);
    /* Sin límite de ancho: la frase entra en una línea y no hay que repartir
       nada. En pantalla estrecha, cuando no quepa, `balance` evita que la
       última palabra caiga sola. */
    text-wrap: balance;
  }
  .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }

  .seg { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
  .seg a { padding: 7px 16px; border-radius: 7px; font-size: 0.76rem; font-weight: 640; color: var(--muted); text-decoration: none; }
  .seg a:hover { color: var(--ink-2); }
  .seg a.active { color: var(--ink); background: var(--card); box-shadow: var(--shadow); }

  select {
    padding: 8px 12px; border: 1px solid var(--border); border-radius: 9px;
    background: var(--panel-2); color: var(--ink); font: inherit; font-size: 0.78rem; font-weight: 600;
  }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-bottom: 18px; }
  .card {
    display: flex; flex-direction: column; gap: 7px;
    padding: 15px 17px; border: 1px solid var(--border); border-radius: var(--r-md);
    background: var(--card); text-decoration: none;
    transition: border-color 0.16s, background 0.16s, transform 0.16s;
  }
  .card:hover { border-color: var(--border-2); background: var(--card-hover); transform: translateY(-2px); }
  .card.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--card)); }
  .card header { display: flex; align-items: center; gap: 8px; color: var(--muted); }
  .card h3 { font-size: 0.78rem; font-weight: 640; color: var(--ink-2); }
  .card .dot { width: 8px; height: 8px; margin-left: auto; border-radius: 50%; background: var(--accent); }
  .card .value { font-size: 1.8rem; font-weight: 720; letter-spacing: -0.03em; line-height: 1; }
  .card .value span { margin-left: 4px; font-size: 0.46em; font-weight: 600; color: var(--muted); }
  .card .value.muted { color: var(--muted-2); }
  .card .who { font-size: 0.76rem; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .table { padding: 16px 20px 10px; border: 1px solid var(--border); border-radius: var(--r-md); background: var(--panel); }
  .caption { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
  .caption .what { display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.84rem; font-weight: 660; }
  .days { display: inline-flex; align-items: center; gap: 4px; }
  .days a, .days .off {
    display: grid; place-items: center;
    width: 22px; height: 22px; border-radius: 6px;
    border: 1px solid var(--border);
    font-size: 0.9rem; line-height: 1; text-decoration: none;
    color: var(--ink-2);
  }
  .days a:hover { border-color: var(--accent); color: var(--accent); }
  .days .off { color: var(--muted-2); border-color: transparent; cursor: default; }
  .days b { font-size: 0.78rem; font-weight: 640; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  /* Siempre a la derecha, también cuando la cabecera es larga y el bloque
     baja de línea. Sin esto el orden y los proveedores se pegaban a la
     izquierda en unos ámbitos y a la derecha en otros, según lo que ocupara
     el nombre del país. */
  .right {
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
    margin-left: auto; justify-content: flex-end; text-align: right;
  }
  .order { font-size: 0.72rem; color: var(--muted); text-decoration: underline; text-underline-offset: 3px; }
  .order:hover { color: var(--ink); }
  .providers {
    margin-top: 12px; padding-top: 10px;
    border-top: 1px solid var(--border);
    font-size: 0.68rem; line-height: 1.5; color: var(--muted-2);
  }

  ol { list-style: none; margin: 0; padding: 0; }
  li {
    display: grid;
    grid-template-columns: 30px minmax(140px, 2fr) auto auto minmax(72px, auto);
    align-items: center; gap: 16px;
    padding: 11px 0; border-bottom: 1px solid var(--border);
  }
  li:last-child { border-bottom: 0; }

  .pos { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px; font-size: 0.76rem; font-weight: 700; color: var(--muted); background: var(--panel-2); }
  .podium .pos { color: var(--accent-ink); background: var(--accent); }
  li:nth-child(2) .pos { background: #c9d2dc; color: #10151d; }
  li:nth-child(3) .pos { background: #d99a5b; color: #10151d; }

  .who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .who a { font-size: 0.86rem; font-weight: 650; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who a:hover { color: var(--accent); text-decoration: underline; }
  .who small { font-size: 0.7rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .prov { font-size: 0.68rem; font-weight: 650; color: var(--muted); white-space: nowrap; }

  .extra { display: flex; gap: 12px; align-items: baseline; }
  .extra b { font-size: 0.74rem; font-weight: 600; color: var(--muted); white-space: nowrap; }
  .extra .time { font-variant-numeric: tabular-nums; }

  .value { font-size: 0.96rem; font-weight: 720; text-align: right; white-space: nowrap; }
  .value i { margin-left: 3px; font-size: 0.62em; font-weight: 600; font-style: normal; color: var(--muted); }

  .empty { padding: 30px 0; color: var(--muted); font-size: 0.88rem; }

  @media (max-width: 720px) {
    li { grid-template-columns: 26px 1fr auto; }
    .prov, .extra { display: none; }
  }
</style>

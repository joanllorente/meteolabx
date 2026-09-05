/**
 * Tarjetas de la pestaña de Histórico.
 *
 * El backend manda una tabla de métricas: una fila por magnitud, con su valor
 * y su fecha. Enseñarlas de una en una es lo que hacía este frontend, y se
 * pierde lo que las relaciona: la máxima y la mínima absolutas son la misma
 * historia —los extremos del periodo— y su amplitud solo existe cuando van
 * juntas. Aquí se agrupan igual que en la app actual.
 *
 * Qué tarjetas salen depende de tres cosas, y por eso ninguna es fija:
 *   · lo que publica el proveedor —sin veleta no hay rumbo, sin piranómetro
 *     no hay tarjeta solar—;
 *   · el modo, mensual o anual;
 *   · cuántos bloques se pidieron: comparar dos agostos no enseña lo mismo
 *     que mirar uno solo.
 *
 * Sin dependencias, para poder probarlo en Node.
 */

/**
 * Qué métricas son un hito del periodo.
 *
 * La tabla del backend trae también índices climáticos —noches tropicales,
 * tórridas, de helada—, que son cuentas del periodo entero y ya salen en el
 * resumen. Un hito es un récord con su día: la máxima absoluta, la racha
 * máxima, el día más lluvioso. Es la misma lista que la app actual.
 */
const MILESTONE_KEYS = new Set([
  'absolute_max', 'absolute_min', 'warmest_year', 'coldest_year',
  'windiest_year', 'max_gust', 'wettest_year', 'driest_year',
  'most_rain_days_year', 'max_precip_24h', 'sunniest_year', 'least_sunny_year',
  'lowest_maximum', 'highest_minimum', 'windiest_month', 'max_precip_24h_short',
  'windiest_day', 'rainiest_day'
]);

/** Orden en que se colocan las tarjetas sueltas. El resto van detrás. */
const CARD_ORDER = {
  lowest_maximum: 10,
  highest_minimum: 20,
  warmest_year: 10,
  coldest_year: 20,
  max_gust: 30,
  windiest_day: 40,
  windiest_month: 40,
  windiest_year: 40,
  rainiest_day: 50,
  max_precip_24h_short: 50,
  max_precip_24h: 50,
  wettest_year: 50
};

const TEMPERATURE_KEYS = new Set([
  'absolute_max', 'absolute_min', 'warmest_year', 'coldest_year',
  'lowest_maximum', 'highest_minimum', 'tropical_nights', 'torrid_nights',
  'frost_nights'
]);
const WIND_KEYS = new Set(['max_gust', 'windiest_day', 'windiest_month', 'windiest_year']);
const PRECIP_KEYS = new Set([
  'rainiest_day', 'wettest_year', 'driest_year', 'most_rain_days_year',
  'max_precip_24h', 'max_precip_24h_short'
]);
const SOLAR_KEYS = new Set(['sunniest_year', 'least_sunny_year']);

/** Rumbo que hay que enseñar junto a cada métrica de viento. */
const DIRECTION_SOURCE = {
  max_gust: 'gust_direction',
  windiest_day: 'windiest_day_direction',
  windiest_month: 'windiest_month_direction',
  windiest_year: 'predominant_direction'
};

/** Métricas de lluvia que pueden llevar colgada la intensidad máxima. */
const RATE_TARGETS = ['rainiest_day', 'max_precip_24h_short', 'max_precip_24h', 'wettest_year'];

const EMPTY = new Set(['', '-', '—', 'nan', 'None', 'null', 'undefined']);

/** Fecha de una métrica, o cadena vacía si el backend manda el hueco. */
function dateOf(row) {
  const value = String(row?.date ?? '').trim();
  return EMPTY.has(value) ? '' : value;
}

/** ¿Esta fila trae un valor de verdad? */
export function hasValue(row) {
  if (!row) return false;
  // El backend manda el hueco con su unidad puesta —«— km/h», «— mm»—, así
  // que mirar solo la cadena entera dejaba pasar por bueno lo que no es un
  // dato: Grand Etang enseñaba una tarjeta de viento con «— km/h» porque su
  // red no publica velocidad media diaria. Una métrica sin una sola cifra no
  // es una métrica.
  const text = String(row.value ?? '').trim();
  return !EMPTY.has(text) && /\d/.test(text);
}

/** «37.3 °C» → `{ value: '37.3', unit: '°C' }`. */
export function split(display) {
  const text = String(display ?? '').trim();
  const match = text.match(/^(-?[\d.,]+)\s*(.*)$/);
  return match ? { value: match[1], unit: match[2] } : { value: text || '—', unit: '' };
}

/** Diferencia entre dos valores formateados, con su unidad. */
function spread(first, second) {
  const a = Number(split(first).value.replace(',', '.'));
  const b = Number(split(second).value.replace(',', '.'));
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '';
  const unit = split(first).unit || split(second).unit;
  return `${Math.abs(a - b).toFixed(1)}${unit ? ` ${unit}` : ''}`;
}

function iconFor(key) {
  if (key === 'lowest_maximum') return 'temp_cold';
  if (key === 'highest_minimum') return 'temp_night';
  if (TEMPERATURE_KEYS.has(key)) return 'temp';
  if (WIND_KEYS.has(key)) return 'wind';
  if (PRECIP_KEYS.has(key)) return 'rain';
  if (SOLAR_KEYS.has(key)) return 'solar';
  return 'temp';
}

/** Intensidad máxima, ya con su fecha si es distinta de la de la métrica. */
function rateText(details, ownDate) {
  if (!details?.max_precip_rate) return '';
  const date = details.max_precip_rate_date;
  return date && date !== ownDate
    ? `${details.max_precip_rate} · ${date}`
    : details.max_precip_rate;
}

function direction(details, key) {
  const source = DIRECTION_SOURCE[key];
  const value = source ? details?.[source] : null;
  if (!value || !value.cardinal || value.cardinal === '-') return null;
  return { cardinal: value.cardinal, degrees: value.degrees || '' };
}

/**
 * Hitos del periodo, agrupados.
 *
 * `texts` son los rótulos ya traducidos (`historical.cards.*`), tal cual los
 * exporta `scripts/export_app_i18n.py`.
 */
export function milestoneCards(summary, texts = {}) {
  const rows = (summary?.extremes || []).filter(
    (row) => MILESTONE_KEYS.has(row.key) && hasValue(row)
  );
  const details = summary?.details || {};
  const comparison = Boolean(summary?.annual_comparison);
  const monthlyBlocks = summary?.summary_mode === 'monthly' && (summary?.period_count || 0) > 1;
  const labels = texts.summary_labels || {};

  const take = (key) => rows.find((row) => row.key === key) || null;
  const used = new Set();
  const cards = [];

  /** Tarjeta de dos valores enfrentados: máximo y mínimo de lo mismo. */
  const pair = (firstKey, secondKey, { title, footerLabel, footerValue, icon, footerItems }) => {
    const first = take(firstKey);
    const second = take(secondKey);
    if (!first || !second) return false;
    used.add(firstKey);
    used.add(secondKey);
    cards.push({
      kind: 'pair',
      key: `${firstKey}-${secondKey}`,
      title,
      icon,
      primary: { ...split(first.value), date: dateOf(first), label: labels.max_short || '' },
      secondary: { ...split(second.value), date: dateOf(second), label: labels.min_short || '' },
      footer: footerValue ? { label: footerLabel, value: footerValue } : null,
      footerItems: footerItems || []
    });
    return true;
  };

  // 1. Extremos térmicos. Al comparar años, la mínima de máximas y la máxima
  //    de mínimas dejan de ser tarjeta propia y bajan al pie de esta.
  const max = take('absolute_max');
  const min = take('absolute_min');
  if (max && min) {
    const footerItems = [];
    if (comparison) {
      for (const key of ['lowest_maximum', 'highest_minimum']) {
        const row = take(key);
        if (!row) continue;
        used.add(key);
        const when = dateOf(row);
        footerItems.push({
          label: row.metric,
          value: when ? `${row.value} · ${when}` : row.value
        });
      }
    }
    pair('absolute_max', 'absolute_min', {
      title: texts.thermal_extremes,
      footerLabel: texts.period_amplitude,
      footerValue: spread(max.value, min.value),
      icon: 'temp',
      footerItems
    });
  }

  // 2-4. Comparando años, los extremos entre años son pares, no valores sueltos.
  if (comparison) {
    const warm = take('warmest_year');
    const cold = take('coldest_year');
    if (warm && cold) {
      pair('warmest_year', 'coldest_year', {
        title: texts.mean_temp_extremes,
        footerLabel: texts.mean_temp_difference,
        footerValue: spread(warm.value, cold.value),
        icon: 'temp'
      });
    }
    pair('wettest_year', 'driest_year', {
      title: texts.precip_extremes,
      footerLabel: texts.max_intensity,
      footerValue: rateText(details, ''),
      icon: 'rain'
    });
    pair('sunniest_year', 'least_sunny_year', {
      title:
        summary?.solar_metric_kind === 'sunshine_hours'
          ? texts.solar_extremes
          : texts.solar_irradiation_extremes,
      icon: 'solar'
    });
  }

  // 5. Varios meses: el día más ventoso y el mes más ventoso se leen juntos.
  const windiestDay = take('windiest_day');
  const windiestMonth = take('windiest_month');
  if (monthlyBlocks && windiestDay && windiestMonth) {
    used.add('windiest_day');
    used.add('windiest_month');
    cards.push({
      kind: 'wind',
      key: 'wind-extremes',
      title: texts.wind_extremes,
      icon: 'wind',
      directionLabel: labels.predominant_direction || '',
      day: {
        label: labels.windiest_day || windiestDay.metric,
        ...split(windiestDay.value),
        date: dateOf(windiestDay),
        direction: direction(details, 'windiest_day')
      },
      month: {
        label: labels.windiest_month || windiestMonth.metric,
        ...split(windiestMonth.value),
        date: details.windiest_month_label || dateOf(windiestMonth),
        direction: direction(details, 'windiest_month')
      }
    });
  }

  // 6. El resto, cada una en su tarjeta. La intensidad máxima se cuelga de la
  //    métrica de lluvia más específica que haya llegado.
  const remaining = rows.filter((row) => !used.has(row.key));
  const rateTarget = RATE_TARGETS.find((key) => remaining.some((row) => row.key === key));

  remaining
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        (CARD_ORDER[a.row.key] ?? 100) - (CARD_ORDER[b.row.key] ?? 100) || a.index - b.index
    )
    .forEach(({ row }) => {
      const when = dateOf(row);
      const extras = [];
      if (row.key === rateTarget) {
        const text = rateText(details, when);
        if (text) extras.push({ label: texts.max_intensity, value: text });
      }
      cards.push({
        kind: 'single',
        key: row.key,
        title: row.metric,
        icon: iconFor(row.key),
        ...split(row.value),
        date: when,
        direction: direction(details, row.key),
        extras
      });
    });

  return cards;
}

/** Grupos del resumen, en el orden de la app actual. */
const SUMMARY_GROUPS = [
  {
    key: 'temperature',
    title: 'average_temperatures',
    icon: 'temp',
    items: [
      ['mean', 'mean_temperature'],
      ['maximums', 'mean_maximums'],
      ['minimums', 'mean_minimums'],
      ['stddev', 'temperature_stddev']
    ]
  },
  { key: 'wind', title: 'wind_summary', icon: 'wind', items: [['mean', 'mean_wind']] },
  {
    key: 'rain',
    title: 'rain_summary',
    icon: 'rain',
    items: [
      ['accumulated', 'accumulated_precipitation'],
      ['mean', 'mean_precipitation'],
      ['rain_days', 'rain_days']
    ]
  },
  { key: 'solar', title: 'solar_summary', icon: 'solar', items: [] },
  {
    key: 'days',
    title: 'characteristic_days',
    icon: 'temp_night',
    items: [
      ['tropical', 'tropical_nights'],
      ['torrid', 'torrid_nights'],
      ['frost', 'frost_nights']
    ]
  }
];

/** La métrica solar depende de la red: irradiación, irradiancia u horas de sol. */
const SOLAR_METRICS = [
  ['irradiation_mean', 'mean_daily_global_solar_irradiation'],
  ['sunshine_mean', 'mean_sunshine_hours'],
  ['irradiance_mean', 'mean_solar_irradiance']
];

/**
 * Resumen del periodo en cinco familias.
 *
 * Un grupo sin ningún dato no se pinta: la app actual enseña guiones, pero
 * aquí una tarjeta de «Solar» con un guion en una red que no mide el sol solo
 * ocupa sitio y hace dudar de si falta el dato o falta el sensor.
 */
export function summaryCards(summary, texts = {}) {
  const values = new Map(
    (summary?.general || []).filter((row) => row.key && hasValue(row)).map((row) => [row.key, row])
  );
  const labels = texts.summary_labels || {};
  const details = summary?.details || {};

  return SUMMARY_GROUPS.map((group) => {
    const items = group.items
      .filter(([, metricKey]) => values.has(metricKey))
      .map(([labelKey, metricKey]) => ({
        label: labels[labelKey] || metricKey,
        ...split(values.get(metricKey).value)
      }));

    if (group.key === 'solar') {
      const found = SOLAR_METRICS.find(([, metricKey]) => values.has(metricKey));
      if (found) {
        items.push({ label: labels[found[0]] || '', ...split(values.get(found[1]).value) });
      }
    }

    if (group.key === 'wind') {
      const rose = details.predominant_direction;
      if (rose?.cardinal && rose.cardinal !== '-') {
        items.push({
          label: labels.predominant_direction || '',
          value: rose.cardinal,
          unit: rose.degrees || ''
        });
      }
    }

    return { key: group.key, title: texts[group.title] || '', icon: group.icon, items };
  }).filter((group) => group.items.length);
}

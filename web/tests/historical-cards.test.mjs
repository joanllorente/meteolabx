/**
 * Las tarjetas del histórico no son una lista fija: dependen de lo que
 * publique la red y de cuántos bloques se hayan pedido. Estos casos son los
 * tres que cambian el resultado, con datos calcados de una consulta real.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { hasValue, milestoneCards, summaryCards, split } from '../src/lib/historical/cards.js';

const TEXTS = {
  thermal_extremes: 'Extremos térmicos',
  mean_temp_extremes: 'Extremos de temperatura media',
  mean_temp_difference: 'Diferencia entre medias',
  precip_extremes: 'Extremos de precipitación',
  solar_extremes: 'Extremos de insolación',
  solar_irradiation_extremes: 'Extremos de irradiación solar',
  wind_extremes: 'Extremos de viento',
  period_amplitude: 'Amplitud del periodo',
  max_intensity: 'Intensidad máxima',
  average_temperatures: 'Temperaturas medias',
  wind_summary: 'Viento',
  rain_summary: 'Lluvia',
  solar_summary: 'Solar',
  characteristic_days: 'Días característicos',
  summary_labels: {
    mean: 'Media', maximums: 'Máximas', minimums: 'Mínimas', stddev: 'Desv. estándar',
    predominant_direction: 'Predominante', accumulated: 'Acumulada', rain_days: 'Días de lluvia',
    tropical: 'Tropicales', torrid: 'Tórridas', frost: 'Helada',
    irradiation_mean: 'Irradiación media', windiest_day: 'Día más ventoso',
    windiest_month: 'Mes más ventoso'
  }
};

const row = (key, metric, value, date = '') => ({ key, metric, value, date });

const DETAILS = {
  gust_direction: { cardinal: 'NNE', degrees: '15°' },
  predominant_direction: { cardinal: 'WSW', degrees: '245.2°' },
  windiest_day_direction: { cardinal: 'ESE', degrees: '106°' },
  windiest_month_direction: { cardinal: 'SW', degrees: '229°' },
  max_precip_rate: '12.0 mm/h',
  max_precip_rate_date: '06/08/2026',
  windiest_month_label: 'Agosto 2026'
};

const oneMonth = {
  has_data: true,
  summary_mode: 'monthly',
  period_count: 1,
  annual_comparison: false,
  solar_metric_kind: 'irradiation',
  details: DETAILS,
  extremes: [
    row('absolute_max', 'Máxima absoluta', '37.3 °C', '14/08/2026'),
    row('absolute_min', 'Mínima absoluta', '20.8 °C', '30/08/2026'),
    row('lowest_maximum', 'Mínima de máximas', '28.8 °C', '22/08/2026'),
    row('max_gust', 'Racha máxima', '46.8 km/h', '15/08/2026'),
    row('windiest_day', 'Día más ventoso', '9.7 km/h', '21/08/2026'),
    row('rainiest_day', 'Día más lluvioso', '2.3 mm', '20/08/2026')
  ],
  general: [
    row('mean_temperature', 'Temperatura media', '28.2 °C'),
    row('mean_wind', 'Media de viento', '6.6 km/h'),
    row('accumulated_precipitation', 'Precipitación acumulada', '6.2 mm'),
    row('mean_daily_global_solar_irradiation', 'Irradiación', '21.2 MJ/m²')
  ]
};

test('un solo mes: extremos térmicos juntos y su amplitud', () => {
  const cards = milestoneCards(oneMonth, TEXTS);
  const first = cards[0];
  assert.equal(first.kind, 'pair');
  assert.equal(first.title, 'Extremos térmicos');
  assert.equal(first.primary.value, '37.3');
  assert.equal(first.secondary.value, '20.8');
  // 37,3 − 20,8. Solo tiene sentido con los dos valores en la misma tarjeta.
  assert.equal(first.footer.value, '16.5 °C');
  // Sin comparación entre años, la mínima de máximas sigue siendo tarjeta propia.
  assert.ok(cards.some((card) => card.key === 'lowest_maximum'));
});

test('la dirección acompaña a cada métrica de viento, y no a las demás', () => {
  const cards = milestoneCards(oneMonth, TEXTS);
  const gust = cards.find((card) => card.key === 'max_gust');
  const day = cards.find((card) => card.key === 'windiest_day');
  const rain = cards.find((card) => card.key === 'rainiest_day');
  assert.deepEqual(gust.direction, { cardinal: 'NNE', degrees: '15°' });
  // La del día más ventoso es la suya, no la predominante del periodo.
  assert.deepEqual(day.direction, { cardinal: 'ESE', degrees: '106°' });
  assert.equal(rain.direction, null);
});

test('la intensidad máxima se cuelga de la métrica de lluvia', () => {
  const rain = milestoneCards(oneMonth, TEXTS).find((card) => card.key === 'rainiest_day');
  assert.deepEqual(rain.extras, [
    { label: 'Intensidad máxima', value: '12.0 mm/h · 06/08/2026' }
  ]);
});

test('varios meses: el día y el mes más ventosos se leen juntos', () => {
  const cards = milestoneCards(
    {
      ...oneMonth,
      period_count: 3,
      extremes: [
        ...oneMonth.extremes,
        row('windiest_month', 'Mes más ventoso', '7.2 km/h', '01/08/2026')
      ]
    },
    TEXTS
  );
  const wind = cards.find((card) => card.kind === 'wind');
  assert.equal(wind.title, 'Extremos de viento');
  assert.equal(wind.day.value, '9.7');
  assert.equal(wind.month.value, '7.2');
  // El agregado mensual es de un mes, no de un día suelto.
  assert.equal(wind.month.date, 'Agosto 2026');
  assert.equal(cards.filter((card) => card.key === 'windiest_day').length, 0);
});

test('comparando años, los extremos entre años se emparejan', () => {
  const cards = milestoneCards(
    {
      ...oneMonth,
      summary_mode: 'annual',
      period_count: 3,
      annual_comparison: true,
      extremes: [
        row('absolute_max', 'Máxima absoluta', '39.6 °C', '08/07/2026'),
        row('absolute_min', 'Mínima absoluta', '1.6 °C', '07/01/2026'),
        row('warmest_year', 'Año más cálido', '19.4 °C', '2026'),
        row('coldest_year', 'Año más frío', '18.0 °C', '2024'),
        row('wettest_year', 'Año más lluvioso', '691.5 mm', '2024'),
        row('driest_year', 'Año más seco', '280.1 mm', '2026'),
        row('sunniest_year', 'Año con mayor irradiación', '19.2 MJ/m²', '2025'),
        row('least_sunny_year', 'Año con menor irradiación', '15.6 MJ/m²', '2024')
      ]
    },
    TEXTS
  );
  const titles = cards.filter((card) => card.kind === 'pair').map((card) => card.title);
  assert.deepEqual(titles, [
    'Extremos térmicos',
    'Extremos de temperatura media',
    'Extremos de precipitación',
    // La red mide irradiación, así que no es «insolación».
    'Extremos de irradiación solar'
  ]);
  const means = cards.find((card) => card.title === 'Extremos de temperatura media');
  assert.equal(means.footer.value, '1.4 °C');
});

test('el resumen se agrupa en familias y omite las que no mide la red', () => {
  const groups = summaryCards(oneMonth, TEXTS);
  assert.deepEqual(
    groups.map((group) => group.title),
    ['Temperaturas medias', 'Viento', 'Lluvia', 'Solar']
  );
  // Sin noches tropicales ni heladas no hay tarjeta de días característicos.
  assert.ok(!groups.some((group) => group.title === 'Días característicos'));
  // El viento arrastra el rumbo predominante, que no es una métrica de la tabla.
  const wind = groups.find((group) => group.title === 'Viento');
  assert.deepEqual(wind.items.at(-1), { label: 'Predominante', value: 'WSW', unit: '245.2°' });
});

test('split separa el número de su unidad', () => {
  assert.deepEqual(split('37.3 °C'), { value: '37.3', unit: '°C' });
  assert.deepEqual(split('-1.6 °C'), { value: '-1.6', unit: '°C' });
  assert.deepEqual(split('12.0 mm/h'), { value: '12.0', unit: 'mm/h' });
  assert.deepEqual(split(''), { value: '—', unit: '' });
});

test('una métrica sin fecha no enseña un guion donde iría la fecha', () => {
  // Hay proveedores que dan el récord sin decir cuándo ocurrió; el backend
  // manda «—» y eso no se pinta.
  const cards = milestoneCards(
    { ...oneMonth, extremes: [row('max_gust', 'Racha máxima', '46.8 km/h', '—')] },
    TEXTS
  );
  assert.equal(cards[0].date, '');
});

test('las noches tropicales y tórridas no son hitos: viven en el resumen', () => {
  // Son cuentas del periodo entero, no un récord con su día, y salen en la
  // tarjeta de días característicos. Repetirlas arriba es ruido.
  const cards = milestoneCards(
    {
      ...oneMonth,
      extremes: [
        ...oneMonth.extremes,
        row('tropical_nights', 'Noches tropicales (mín > 20 °C)', '31 noches', '—'),
        row('torrid_nights', 'Noches tórridas (mín > 25 °C)', '12 noches', '—')
      ]
    },
    TEXTS
  );
  assert.ok(!cards.some((card) => /noches/i.test(card.title)));

  const groups = summaryCards(
    {
      ...oneMonth,
      general: [
        ...oneMonth.general,
        row('tropical_nights', 'Noches tropicales', '31 noches'),
        row('torrid_nights', 'Noches tórridas', '12 noches')
      ]
    },
    TEXTS
  );
  const days = groups.find((group) => group.title === 'Días característicos');
  assert.deepEqual(days.items.map((item) => item.label), ['Tropicales', 'Tórridas']);
});

test('una métrica sin cifras no es una métrica', () => {
  // El backend manda el hueco con la unidad puesta. Grand Etang (ECCC) no
  // publica velocidad media diaria, y la tarjeta de viento salía con «— km/h»
  // como si fuera un dato.
  assert.equal(hasValue({ value: '— km/h' }), false);
  assert.equal(hasValue({ value: '— mm' }), false);
  assert.equal(hasValue({ value: '—' }), false);
  assert.equal(hasValue({ value: '67.0 km/h' }), true);
  assert.equal(hasValue({ value: '0 noches' }), true);
});

test('el resumen se queda sin grupo de viento cuando la red no publica media', () => {
  const summary = {
    general: [
      { key: 'mean_temperature', metric: 'Temperatura media', value: '18.8 °C', date: '' },
      { key: 'mean_wind', metric: 'Media de viento', value: '— km/h', date: '' },
      { key: 'accumulated_precipitation', metric: 'Precipitación', value: '— mm', date: '' }
    ],
    details: {}
  };
  const grupos = summaryCards(summary, {});
  assert.deepEqual(grupos.map((g) => g.key), ['temperature']);
});

test('la racha máxima sí llega a los hitos aunque falte el viento medio', () => {
  const summary = {
    extremes: [
      { key: 'max_gust', metric: 'Racha máxima', value: '99.0 km/h', date: '23/08/2026' },
      { key: 'windiest_day', metric: 'Día más ventoso', value: '— km/h', date: '—' }
    ],
    details: {}
  };
  const claves = milestoneCards(summary, {}).map((card) => card.key);
  assert.ok(claves.includes('max_gust'));
  assert.ok(!claves.includes('windiest_day'));
});

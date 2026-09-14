/**
 * Una estación sin higrómetro publica la presión saturante —sale de la
 * temperatura— pero no la de vapor. El gráfico se pintaba igual, solo con la
 * curva de referencia discontinua.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { observationModel } from '../src/lib/observation/model.js';

const station = { provider: 'AEMET', station_id: '0201X', name: 'Drassanes', tz: 'Europe/Madrid' };

function vapourChart(vapor, saturation) {
  const inicio = Date.UTC(2026, 8, 4, 22, 0, 0) / 1000; // 00:00 del día 5 en Madrid
  return observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17 },
      derivatives: {},
      daily_extremes: {},
      series: {
        epochs: saturation.map((_, index) => inicio + index * 600),
        vapor_pressures: vapor,
        saturation_pressures: saturation
      }
    },
    station,
    'es'
  ).charts.vapour;
}

const saturante = [19.4, 19.1, 18.9, 19.6, 21.0, 22.8];

test('sin higrómetro no hay gráfico de presión de vapor', () => {
  assert.equal(vapourChart(saturante.map(() => null), saturante), null);
  // Tampoco si la serie ni siquiera llega.
  assert.equal(vapourChart([], saturante), null);
});

test('con higrómetro se dibujan las dos curvas', () => {
  const chart = vapourChart([14.2, 14.0, 13.9, 14.3, 14.8, 15.1], saturante);
  assert.ok(chart);
  assert.deepEqual(chart.data[0].filter((value) => value !== null), [14.2, 14, 13.9, 14.3, 14.8, 15.1]);
  assert.deepEqual(chart.data[1].filter((value) => value !== null), saturante);
});

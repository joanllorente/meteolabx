import assert from 'node:assert/strict';
import test from 'node:test';

import { observationModel } from '../src/lib/observation/model.js';

const station = { provider: 'METEOGALICIA', station_id: '14005', name: 'Porto de Marín', tz: 'Europe/Madrid' };

function uvCard(uv, solarAltitude) {
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17, uv },
      derivatives: { uv, erythemal_irradiance_mw_m2: uv === null ? null : 25 * uv },
      daily_extremes: {},
      series: { solar_altitude: solarAltitude }
    },
    station,
    'es'
  );
  return model.radiation.find((card) => card.title === 'Índice UV');
}

test('bajo el horizonte, el residuo del sensor UV se cuenta como cero', () => {
  // El caso real: Porto de Marín a las 22:00, con el Sol a 11° bajo el
  // horizonte, seguía publicando 0,024 UVI — 0,6 mW/m² al pasarlo a
  // irradiancia eritematosa.
  const card = uvCard(0.024, -11.28);
  assert.equal(card.value, '0,0');
  assert.equal(card.sub[0].value, '0,0 mW/m²');
});

test('con el Sol sobre el horizonte se enseña lo que mide la estación', () => {
  const card = uvCard(3.4, 42);
  assert.equal(card.value, '3,4');
  assert.equal(card.sub[0].value, '85,0 mW/m²');
});

test('sin altura solar conocida no se corrige nada', () => {
  const card = uvCard(0.024, null);
  assert.equal(card.sub[0].value, '0,6 mW/m²');
});

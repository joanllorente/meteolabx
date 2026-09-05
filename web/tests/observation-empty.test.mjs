import assert from 'node:assert/strict';
import test from 'node:test';

import { observationModel } from '../src/lib/observation/model.js';

const station = { provider: 'METEOCAT', station_id: 'X4', tz: 'Europe/Madrid' };

test('sin estación conectada, ninguna tarjeta inventa un cero', () => {
  const model = observationModel(null, {}, 'es');
  assert.equal(model.temperature.value, '—');
  assert.equal(model.dewPoint.value, '—');
  assert.equal(model.pressure.value, '—');
  assert.equal(model.humidity.value, '—');
  assert.equal(model.wind.value, '—');
});

test('una estación sin piranómetro ni sensor UV no enseña la sección de radiación', () => {
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 21, RH: 60 },
      derivatives: { solar_rad: null, uv: null },
      daily_extremes: {}
    },
    station,
    'es'
  );
  assert.deepEqual(model.radiation, []);
});

test('con piranómetro la sección se enseña, aunque no haya sensor UV', () => {
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 21, RH: 60, solar_radiation: 386 },
      derivatives: { solar_rad: 386, uv: null, erythemal_dose_today_sed: null },
      daily_extremes: {}
    },
    station,
    'es'
  );
  assert.ok(model.radiation.length > 0);

  // Y la dosis, que no se mide, se dice con una raya: un 0,00 SED afirmaría
  // que hoy no ha llegado nada de ultravioleta.
  const dose = model.radiation.find((card) => card.title.startsWith('Dosis'));
  assert.equal(dose.value, '—');
});

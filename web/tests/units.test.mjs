import test from 'node:test';
import assert from 'node:assert/strict';

import {
  convertRadiationEnergy,
  convertSeries,
  convertUnit,
  normalizeUnitPreferences,
  radiationEnergyLabel,
  unitLabel
} from '../src/lib/units.js';

test('normaliza preferencias desconocidas a las unidades canónicas', () => {
  assert.deepEqual(normalizeUnitPreferences({ temperature: 'F', wind: 'invalid' }), {
    temperature: 'f', wind: 'kmh', pressure: 'hpa', precip: 'mm', radiation: 'wm2'
  });
});

test('convierte las cinco familias que muestra el selector global', () => {
  const units = { temperature: 'f', wind: 'kt', pressure: 'inhg', precip: 'in', radiation: 'kwhm2' };
  assert.equal(convertUnit(20, 'temperature', units), 68);
  assert.equal(convertUnit(10, 'temperature', units, { delta: true }), 18);
  assert.ok(Math.abs(convertUnit(18.52, 'wind', units) - 10) < 0.01);
  assert.ok(Math.abs(convertUnit(1013.25, 'pressure', units) - 29.9213) < 0.001);
  assert.equal(convertUnit(25.4, 'precip', units), 1);
  assert.equal(convertUnit(800, 'radiation', units), 0.8);
  assert.equal(convertRadiationEnergy(3.6, units), 1);
  assert.equal(radiationEnergyLabel(units), 'kWh/m²');
});

test('convierte series sin transformar sus huecos', () => {
  assert.deepEqual(convertSeries([0, null, 100], 'temperature', { temperature: 'f' }), [32, null, 212]);
  assert.equal(unitLabel('wind', { wind: 'mph' }), 'mph');
});

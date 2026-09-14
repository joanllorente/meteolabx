import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeUnitPreset,
  convertRadiationEnergy,
  convertSeries,
  convertUnit,
  normalizeUnitPreferences,
  radiationEnergyLabel,
  unitLabel,
  unitOptions,
  unitPresets
} from '../src/lib/units.js';

test('normaliza preferencias desconocidas a las unidades canónicas', () => {
  assert.deepEqual(normalizeUnitPreferences({ temperature: 'F', wind: 'invalid' }), {
    temperature: 'f', wind: 'kmh', pressure: 'hpa', precip: 'mm', radiation: 'wm2',
    distance: 'km', altitude: 'm'
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

test('distancia y altitud se convierten desde km y m', () => {
  assert.ok(Math.abs(convertUnit(1.609344, 'distance', { distance: 'mi' }) - 1) < 1e-9);
  assert.ok(Math.abs(convertUnit(1.852, 'distance', { distance: 'nm' }) - 1) < 1e-9);
  assert.ok(Math.abs(convertUnit(410, 'altitude', { altitude: 'ft' }) - 1345.144) < 0.001);
  assert.equal(convertUnit(410, 'altitude', { altitude: 'm' }), 410);
  assert.equal(unitLabel('distance', { distance: 'nm' }), 'NM');
});

test('los presets usan unidades que existen y se reconocen al elegirlos', () => {
  assert.deepEqual(unitPresets.map((preset) => preset.id), ['metric', 'meteorological', 'imperial', 'aviation', 'uk']);
  for (const preset of unitPresets) {
    for (const [family, unit] of Object.entries(preset.units)) assert.ok(unitOptions[family][unit], `${preset.id}.${family}`);
    assert.equal(activeUnitPreset({ radiation: 'mjm2', ...preset.units }), preset.id);
  }
  assert.equal(activeUnitPreset(normalizeUnitPreferences({})), 'metric');
  assert.equal(activeUnitPreset({ ...unitPresets[0].units, wind: 'kt' }), '');
});

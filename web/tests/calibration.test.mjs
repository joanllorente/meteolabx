import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CALIBRATION_ORDER,
  CALIBRATION_SPECS,
  normalizeCalibration
} from '../src/lib/calibration.js';

test('los siete sensores son los que acepta el backend', () => {
  assert.deepEqual(CALIBRATION_ORDER.slice().sort(), Object.keys(CALIBRATION_SPECS).sort());
  assert.equal(CALIBRATION_ORDER.length, 7);
});

test('cada offset se recorta a su rango', () => {
  const values = normalizeCalibration({ thermometer: 40, barometer: -99, wind_vane: 400 });
  assert.equal(values.thermometer, 5);
  assert.equal(values.barometer, -20);
  assert.equal(values.wind_vane, 180);
});

test('se admite la coma decimal y se redondea a los decimales del sensor', () => {
  const values = normalizeCalibration({ thermometer: '-1,26', barometer: '3,7' });
  assert.equal(values.thermometer, -1.3);
  // El barómetro va en enteros de hPa.
  assert.equal(values.barometer, 4);
});

test('lo que no es un número no calibra nada', () => {
  const values = normalizeCalibration({ thermometer: '', hygrometer: 'x', anemometer: null });
  assert.equal(values.thermometer, 0);
  assert.equal(values.hygrometer, 0);
  assert.equal(values.anemometer, 0);
});

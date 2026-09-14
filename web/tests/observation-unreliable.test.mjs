import assert from 'node:assert/strict';
import test from 'node:test';

import { ui } from '../src/lib/i18n/ui.js';
import { hasUnreliableData } from '../src/lib/observation/warnings.js';

test('los avisos de sensor dudoso encienden el cartel', () => {
  // Skriveri (termómetro congelado), PAKF (pluviómetro disparado) y las de
  // Windy con variables planas: todas acaban en el mismo mensaje.
  for (const code of [
    'suspect_temperature',
    'suspect_precipitation',
    'unreported_precipitation',
    'suspect_wind',
    'flatlined_series'
  ]) {
    assert.equal(hasUnreliableData([code]), true, code);
  }
  assert.equal(hasUnreliableData(['missing_elevation', 'suspect_temperature']), true);
});

test('la antigüedad del dato no enciende el cartel', () => {
  // La cabecera ya dice «sin datos recientes · hace 5 h»; repetirlo es ruido.
  assert.equal(hasUnreliableData(['data_age']), false);
  assert.equal(hasUnreliableData(['missing_elevation']), false);
});

test('sin avisos no hay cartel', () => {
  assert.equal(hasUnreliableData([]), false);
  assert.equal(hasUnreliableData(undefined), false);
  assert.equal(hasUnreliableData(null), false);
  assert.equal(hasUnreliableData('suspect_temperature'), false);
});

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
for (const language of languages) {
  test(`el aviso de datos dudosos está traducido al ${language}`, () => {
    const text = ui(language, 'unreliable_data');
    assert.equal(typeof text, 'string');
    assert.ok(text.length > 20, `${language} sin traducir: ${text}`);
  });
}

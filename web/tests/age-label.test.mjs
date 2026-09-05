import assert from 'node:assert/strict';
import test from 'node:test';

import { ui } from '../src/lib/i18n/ui.js';

/** La misma escalera que pinta la cinta en AppShell. */
function ageLabel(language, seconds) {
  if (seconds === null) return '';
  if (seconds < 60) return ui(language, 'ago_seconds').replace('{n}', String(seconds));
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return ui(language, 'ago_minutes').replace('{n}', String(minutes));
  return ui(language, 'ago_hours').replace('{n}', String(Math.floor(minutes / 60)));
}

test('el primer minuto se cuenta en segundos', () => {
  assert.equal(ageLabel('es', 0), 'hace 0 s');
  assert.equal(ageLabel('es', 45), 'hace 45 s');
  assert.equal(ageLabel('es', 59), 'hace 59 s');
});

test('a partir del minuto se cuentan minutos, y horas a partir de la hora', () => {
  assert.equal(ageLabel('es', 60), 'hace 1 min');
  assert.equal(ageLabel('es', 45 * 60), 'hace 45 min');
  assert.equal(ageLabel('es', 3600), 'hace 1 h');
  assert.equal(ageLabel('es', 3 * 3600 + 61), 'hace 3 h');
});

for (const language of ['es', 'ca', 'en', 'fr', 'it', 'pt']) {
  test(`los tres tramos están traducidos al ${language}`, () => {
    for (const seconds of [30, 600, 7200]) {
      const label = ageLabel(language, seconds);
      assert.ok(label.length > 3, `${language}/${seconds}: ${label}`);
      assert.ok(!label.includes('{n}'), `${language}/${seconds} sin sustituir: ${label}`);
    }
  });
}

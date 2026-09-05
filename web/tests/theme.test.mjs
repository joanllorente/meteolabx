import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import { nextMode, resolveTheme, themeForHour } from '../src/lib/theme.js';

test('la hora solo decide cuando el dispositivo no dice nada', () => {
  assert.equal(themeForHour(10), 'light');
  assert.equal(themeForHour(8), 'light');
  assert.equal(themeForHour(19), 'light');
  assert.equal(themeForHour(20), 'dark');
  assert.equal(themeForHour(3), 'dark');
});

test('el interruptor da la vuelta completa y vuelve al automático', () => {
  assert.equal(nextMode('auto'), 'light');
  assert.equal(nextMode('light'), 'dark');
  assert.equal(nextMode('dark'), 'auto');
});

test('en automático manda el sistema; elegido, manda la elección', () => {
  assert.equal(resolveTheme('auto', 'light'), 'light');
  assert.equal(resolveTheme('auto', 'dark'), 'dark');
  // Es lo que fallaba: con el ordenador en claro se veía oscuro y no había
  // forma de devolverle el mando al sistema.
  assert.equal(resolveTheme('dark', 'light'), 'dark');
  assert.equal(resolveTheme('light', 'dark'), 'light');
});

test('el script del head aplica la misma regla horaria que el módulo', () => {
  // Son dos copias por fuerza —el head es un script suelto, sin módulos—, así
  // que al menos que no se separen sin que nadie se entere.
  const html = readFileSync(new URL('../src/app.html', import.meta.url), 'utf8');
  assert.match(html, /hour >= 8 && hour < 20 \? 'light' : 'dark'/);
});

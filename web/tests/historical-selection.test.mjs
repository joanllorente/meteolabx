import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveSelection, seriesRange } from '../src/lib/historical/selection.js';

const empty = new URLSearchParams();

test('sin fechas de serie se ofrecen los años de siempre', () => {
  const selection = resolveSelection(empty, 'monthly', 'es', false, null);
  const currentYear = new Date().getUTCFullYear();
  assert.equal(selection.yearOptions[0], currentYear);
  assert.equal(selection.yearOptions.at(-1), 1990);
});

test('una estación cerrada solo ofrece sus años y propone su último mes', () => {
  const station = { series_start: '1955-03-01', series_end: '2024-03-25' };
  const monthly = resolveSelection(empty, 'monthly', 'es', false, station);
  assert.equal(monthly.yearOptions[0], 2024);
  assert.equal(monthly.yearOptions.at(-1), 1955);
  assert.deepEqual(monthly.years, [2024]);
  assert.deepEqual(monthly.months, [3]);

  const annual = resolveSelection(empty, 'annual', 'es', false, station);
  assert.deepEqual(annual.years, [2024]);

  // Un año fuera de la serie que llegue en la URL no se consulta.
  const outside = resolveSelection(new URLSearchParams('anios=2026&meses=8'), 'monthly', 'es', true, station);
  assert.deepEqual(outside.years, []);
});

test('una serie en marcha llega hasta el año en curso', () => {
  const station = { series_start: '2001-01-02', series_end: null };
  const range = seriesRange(station);
  assert.equal(range.start.toISOString().slice(0, 10), '2001-01-02');
  assert.equal(range.end, null);
  const selection = resolveSelection(empty, 'monthly', 'es', false, station);
  assert.equal(selection.yearOptions[0], new Date().getUTCFullYear());
  assert.equal(selection.yearOptions.at(-1), 2001);
});

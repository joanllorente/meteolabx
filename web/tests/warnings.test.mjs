import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activeSpan,
  demoWarnings,
  filterWarnings,
  groupByCountry,
  isoDay,
  zoneLevels
} from '../src/lib/warnings/warnings.js';
import zones from '../src/lib/warnings/demo-zones.json' with { type: 'json' };

const NOW = new Date(2026, 8, 13, 16, 0);

test('la demostración cae siempre en hoy, mañana y pasado', () => {
  const { days, warnings } = demoWarnings(NOW);
  assert.deepEqual(days.map((day) => day.date), ['2026-09-13', '2026-09-14', '2026-09-15']);
  assert.ok(warnings.every((warning) => days.some((day) => day.date === warning.day)));
});

test('cada zona de la demostración existe en la geometría', () => {
  const known = new Set(zones.features.map((feature) => feature.properties.zone));
  for (const warning of demoWarnings(NOW).warnings) {
    assert.ok(known.has(warning.zone), warning.zone);
  }
});

test('los filtros combinan día, fenómeno, país y búsqueda sin tildes', () => {
  const { warnings } = demoWarnings(NOW);
  const today = isoDay(NOW);
  const rain = filterWarnings(warnings, { day: today, hazard: 'rain' });
  assert.deepEqual(rain.map((warning) => warning.zone), ['FR-Occitanie', 'FR-Auvergne-Rhône-Alpes']);

  const spain = filterWarnings(warnings, { day: today, country: 'ES' });
  assert.deepEqual(spain.map((warning) => warning.zone).sort(), ['ES-Aragón', 'ES-Extremadura']);

  const found = filterWarnings(warnings, { query: 'aragon', names: { 'ES-Aragón': 'Aragón' } });
  assert.deepEqual(found.map((warning) => warning.zone), ['ES-Aragón']);
});

test('una zona toma el nivel de su aviso más grave', () => {
  const levels = zoneLevels([
    { zone: 'FR-Occitanie', level: 1 },
    { zone: 'FR-Occitanie', level: 3 },
    { zone: 'IT-Liguria', level: 2 }
  ]);
  assert.deepEqual(levels, { 'FR-Occitanie': 3, 'IT-Liguria': 2 });
});

test('los países se ordenan por su aviso más grave', () => {
  const groups = groupByCountry([
    { zone: 'ES-Aragón', level: 1 },
    { zone: 'FR-Occitanie', level: 3 },
    { zone: 'FR-Bretagne', level: 1 },
    { zone: 'IT-Liguria', level: 2 }
  ]);
  assert.deepEqual(groups.map((group) => group.country), ['FR', 'IT', 'ES']);
  assert.deepEqual(groups[0].warnings.map((warning) => warning.zone), ['FR-Occitanie', 'FR-Bretagne']);
});

test('la franja activa va de la primera a la última hora con aviso', () => {
  assert.deepEqual(activeSpan([0, 0, 1, 2, 0, 1, 0]), [2, 5]);
  assert.equal(activeSpan([0, 0, 0]), null);
});

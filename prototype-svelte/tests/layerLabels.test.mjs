/**
 * Nombres de las capas del mapa. El de la capa superpuesta cambia según el
 * campo —isohipsas sobre geopotencial, isobaras sobre presión— y ese caso se
 * escapaba: el rótulo alternativo llegaba como respaldo, y el respaldo solo
 * entra cuando el idioma no tiene traducción propia, así que en los seis
 * idiomas del visor el panel seguía diciendo «isohipsas» sobre un mapa de
 * presión al nivel del mar.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import { FORECAST_LOCALES, forecastLayerLabel } from '../src/lib/forecast-i18n.js';

const IDIOMAS = Object.keys(FORECAST_LOCALES);

test('cada idioma nombra las isobaras, y ninguno las llama isohipsas', () => {
  for (const idioma of IDIOMAS) {
    const isobaras = forecastLayerLabel(idioma, 'isobars', 'Isobaras');
    const isohipsas = forecastLayerLabel(idioma, 'isohypses', 'Isohipsas');
    assert.ok(isobaras, `${idioma} no traduce isobars`);
    assert.notEqual(isobaras, isohipsas, `${idioma} las confunde`);
  }
});

test('cada idioma nombra las ciudades', () => {
  for (const idioma of IDIOMAS) {
    assert.ok(forecastLayerLabel(idioma, 'cities', ''), `${idioma} no traduce cities`);
  }
});

test('un idioma desconocido cae al respaldo que le pasan', () => {
  assert.equal(forecastLayerLabel('de', 'unknown-layer', 'Respaldo'), 'Respaldo');
});

test('la capa superpuesta pide su propia clave cuando el mapa la renombra', () => {
  const grid = readFileSync(new URL('../src/components/ForecastGrid.svelte', import.meta.url), 'utf8');
  assert.match(grid, /labelKey: 'isobars'/);
  assert.match(grid, /forecastLayerLabel\(language, capa\.labelKey \|\| capa\.id, capa\.label\)/);
});

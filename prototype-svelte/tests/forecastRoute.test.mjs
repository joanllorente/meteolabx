import assert from 'node:assert/strict';
import test from 'node:test';

import { forecastPath, parseForecastLocation } from '../src/lib/forecast-route.js';

test('el idioma y el mapa salen de la ruta', () => {
  assert.deepEqual(parseForecastLocation({ pathname: '/en/forecast/ebwd', search: '?slug=x' }), { language: 'en', product: 'ebwd' });
  assert.deepEqual(parseForecastLocation({ pathname: '/fr/forecast', search: '' }), { language: 'fr', product: '' });
  assert.deepEqual(parseForecastLocation({ pathname: '/fr/forecast/', search: '' }), { language: 'fr', product: '' });
});

test('la forma antigua con ?lang se sigue entendiendo', () => {
  assert.deepEqual(parseForecastLocation({ pathname: '/forecast/', search: '?lang=it' }), { language: 'it', product: '' });
  assert.deepEqual(parseForecastLocation({ pathname: '/forecast', search: '?lang=xx' }), { language: 'es', product: '' });
});

test('cada mapa tiene su ruta', () => {
  assert.equal(forecastPath('es'), '/es/forecast');
  assert.equal(forecastPath('en', 'shear-06'), '/en/forecast/shear-06');
});

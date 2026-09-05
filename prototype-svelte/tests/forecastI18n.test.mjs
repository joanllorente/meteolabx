import assert from 'node:assert/strict';
import test from 'node:test';

import {
  forecastLocale,
  forecastText,
  localizedForecastCategories,
  localizedForecastGuide,
  localizedForecastProducts
} from '../src/lib/forecast-i18n.js';

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];

test('la interfaz de predicción cubre los seis idiomas de MeteoLabX', () => {
  for (const language of languages) {
    assert.notEqual(forecastText(language, 'title'), 'title');
    assert.match(forecastLocale(language), /^[a-z]{2}-[A-Z]{2}$/);
  }
  assert.equal(forecastText('en', 'loading', { product: 'CAPE' }), 'Loading CAPE');
});

test('categorías, productos y ayuda siguen el idioma', () => {
  const categories = [{ id: 'temperature', label: 'Temperatura' }];
  const products = [{ id: 'wind-level', label: 'Viento por niveles', kind: 'native' }];
  assert.equal(localizedForecastCategories(categories, 'en')[0].label, 'Temperature');
  const product = localizedForecastProducts(products, 'en')[0];
  assert.equal(product.label, 'Wind by level');
  assert.match(localizedForecastGuide({}, product, 'en').what, /Wind by level/);
});

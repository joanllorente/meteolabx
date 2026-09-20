import assert from 'node:assert/strict';
import test from 'node:test';

import {
  forecastLocale,
  forecastText,
  localizedForecastCategories,
  localizedForecastProducts
} from '../src/lib/forecast-i18n.js';
import englishGuides from '../src/data/forecastProductGuides.en.js';
import { forecastProductGuides as spanishGuides } from '../src/data/forecastProductGuides.js';

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];

test('la interfaz de predicción cubre los seis idiomas de MeteoLabX', () => {
  for (const language of languages) {
    assert.notEqual(forecastText(language, 'title'), 'title');
    assert.match(forecastLocale(language), /^[a-z]{2}-[A-Z]{2}$/);
  }
  assert.equal(forecastText('en', 'loading', { product: 'CAPE' }), 'Loading CAPE');
});

test('categorías y productos siguen el idioma', () => {
  const categories = [{ id: 'temperature', label: 'Temperatura' }];
  const products = [{ id: 'wind-level', label: 'Viento por niveles', kind: 'native' }];
  assert.equal(localizedForecastCategories(categories, 'en')[0].label, 'Temperature');
  const product = localizedForecastProducts(products, 'en')[0];
  assert.equal(product.label, 'Wind by level');
});

/**
 * Las claves de la guía traducida son ids de producto.
 *
 * `localizedForecastGuide` busca `guías[product.id]` y, si no lo encuentra,
 * enseña la castellana con un aviso. Una clave mal escrita no rompe nada: la
 * traducción simplemente no se ve nunca, que es el fallo más difícil de notar.
 * La función vive en un módulo con runas y carga diferida, así que no se puede
 * importar desde Node; lo que se comprueba aquí es su materia prima.
 */
test('cada guía inglesa corresponde a un producto y trae su resumen', () => {
  const claves = Object.keys(englishGuides);
  assert.ok(claves.length > 0);
  for (const clave of claves) {
    assert.ok(clave in spanishGuides, `la guía inglesa «${clave}» no tiene castellana`);
    assert.equal(typeof englishGuides[clave].what, 'string');
  }
});

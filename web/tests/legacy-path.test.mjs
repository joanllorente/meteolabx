/**
 * Streamlit se mudó a `/app`. Las URLs que Google ya tiene indexadas —las
 * fichas estáticas, los directorios por red y por ciudad— siguen llegando a
 * la raíz, así que el proxy tiene que reescribirlas o responderían 404.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeBasePath, withLegacyBase } from '../src/lib/legacy-path.js';

test('el prefijo se normaliza venga como venga', () => {
  assert.equal(normalizeBasePath('app'), '/app');
  assert.equal(normalizeBasePath('/app/'), '/app');
  assert.equal(normalizeBasePath(''), '');
  assert.equal(normalizeBasePath(null), '');
});

test('las rutas de la app antigua reciben el prefijo', () => {
  assert.equal(withLegacyBase('/es/estaciones.html', 'app'), '/app/es/estaciones.html');
  assert.equal(withLegacyBase('/directories-sitemap.xml', 'app'), '/app/directories-sitemap.xml');
  assert.equal(withLegacyBase('/seo-pages.css', 'app'), '/app/seo-pages.css');
  assert.equal(withLegacyBase('/?e=AEMET~x&sid=1', 'app'), '/app/?e=AEMET~x&sid=1');
});

test('lo que ya viene prefijado no se prefija dos veces', () => {
  assert.equal(withLegacyBase('/app/', 'app'), '/app/');
  assert.equal(withLegacyBase('/app/_stcore/stream', 'app'), '/app/_stcore/stream');
  assert.equal(withLegacyBase('/app?x=1', 'app'), '/app?x=1');
});

test('sin prefijo configurado no se toca nada', () => {
  assert.equal(withLegacyBase('/es/estaciones.html', ''), '/es/estaciones.html');
});

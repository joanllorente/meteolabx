/**
 * El service worker de una app de datos en vivo: nada meteorológico ni
 * ninguna página sale de la caché mientras haya red.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { OFFLINE_URL, PRECACHE_FILES, strategyFor } from '../src/lib/pwa/strategy.js';

const ORIGIN = 'https://www.meteolabx.com';
const get = (path, mode = 'cors') => ({ url: `${ORIGIN}${path}`, method: 'GET', mode });

test('los datos en vivo nunca pasan por la caché', () => {
  assert.equal(strategyFor(get('/v1/observations/aemet/0201X'), ORIGIN), 'network');
  assert.equal(strategyFor(get('/v1/forecast/arome/frames.grid?product=cape'), ORIGIN), 'network');
  // Ni aunque sea una navegación directa a la API.
  assert.equal(strategyFor(get('/v1/stats/stations', 'navigate'), ORIGIN), 'network');
});

test('las páginas van a red y solo sin conexión dan la de aviso', () => {
  assert.equal(strategyFor(get('/es/observation/barcelona-drassanes-0201x', 'navigate'), ORIGIN), 'navigate');
  assert.equal(strategyFor(get('/forecast/', 'navigate'), ORIGIN), 'navigate');
});

test('solo los ficheros que no cambian sin cambiar de nombre salen de la caché', () => {
  assert.equal(strategyFor(get('/_app/immutable/chunks/BxY12.js'), ORIGIN), 'cache-first');
  assert.equal(strategyFor(get('/forecast/assets/forecast-TFoes56l.js'), ORIGIN), 'cache-first');
  assert.equal(strategyFor(get('/fonts/inter-latin.woff2'), ORIGIN), 'cache-first');
  // Sin hash: una copia vieja rompería el mapa o el aviso de versión nueva.
  assert.equal(strategyFor(get('/maplibre/maplibre-gl-worker.mjs'), ORIGIN), 'network');
  assert.equal(strategyFor(get('/_app/version.json'), ORIGIN), 'network');
  assert.equal(strategyFor(get('/paises.json'), ORIGIN), 'network');
});

test('ni otros dominios ni lo que no es GET', () => {
  const tesela = 'https://tiles.basemaps.cartocdn.com/vectortiles/carto.streets/v1/6/32/23.mvt';
  assert.equal(strategyFor({ url: tesela, method: 'GET', mode: 'cors' }, ORIGIN), 'network');
  assert.equal(strategyFor({ url: `${ORIGIN}/_app/immutable/chunks/a.js`, method: 'POST' }, ORIGIN), 'network');
  assert.equal(strategyFor({ url: 'no es una url', method: 'GET' }, ORIGIN), 'network');
});

test('lo precacheado existe y es poco', () => {
  assert.ok(PRECACHE_FILES.includes(OFFLINE_URL));
  for (const path of PRECACHE_FILES) {
    assert.ok(readFileSync(new URL(`../static${path}`, import.meta.url)).length > 0, path);
  }
  // La página sin conexión no puede depender de nada que no esté precacheado.
  const offline = readFileSync(new URL('../static/offline.html', import.meta.url), 'utf8');
  const recursos = [...offline.matchAll(/(?:src|href)="(\/[^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(recursos.filter((path) => !PRECACHE_FILES.includes(path)), []);
});

test('el manifiesto trae los iconos normales y los maskable por separado', () => {
  const manifest = JSON.parse(readFileSync(new URL('../static/manifest.webmanifest', import.meta.url), 'utf8'));
  const por = (purpose) =>
    manifest.icons.filter((icon) => (icon.purpose || 'any') === purpose).map((icon) => icon.sizes);
  assert.deepEqual(por('any'), ['192x192', '512x512']);
  assert.deepEqual(por('maskable'), ['192x192', '512x512']);
  // Nunca «any maskable» en una sola entrada.
  assert.ok(manifest.icons.every((icon) => !String(icon.purpose || '').includes(' ')));
  // Los maskable llevan su propio fichero, con margen para el recorte.
  const maskable = manifest.icons.filter((icon) => icon.purpose === 'maskable').map((icon) => icon.src);
  assert.deepEqual(maskable, ['/icons/icon-192-maskable.png', '/icons/icon-512-maskable.png']);
  for (const icon of manifest.icons) {
    assert.ok(readFileSync(new URL(`../static${icon.src}`, import.meta.url)).length > 0, icon.src);
  }
});

test('la página sin conexión habla todos los idiomas que publica la web', async () => {
  const { LANGUAGE_CODES } = await import('../src/lib/seo/i18n.js');
  const offline = readFileSync(new URL('../static/offline.html', import.meta.url), 'utf8');
  const bloque = offline.slice(offline.indexOf('var TEXTS = {'), offline.indexOf('};', offline.indexOf('var TEXTS = {')));
  const idiomas = [...bloque.matchAll(/^\s+([a-z]{2}): \[/gm)].map((match) => match[1]);
  assert.deepEqual([...idiomas].sort(), [...LANGUAGE_CODES].sort());
});

test('el visor de predicción enlaza la misma PWA', () => {
  const html = readFileSync(new URL('../../prototype-svelte/forecast.html', import.meta.url), 'utf8');
  // Absoluta: el visor declara <base href="/forecast/">.
  assert.match(html, /<link rel="manifest" href="\/manifest\.webmanifest" \/>/);
  assert.match(html, /<meta name="theme-color"/);
  assert.match(html, /<meta name="apple-mobile-web-app-title" content="MeteoLabX" \/>/);
});

test('el service worker espera a guardar lo que cachea', () => {
  const worker = readFileSync(new URL('../src/service-worker.js', import.meta.url), 'utf8');
  assert.match(worker, /event\.waitUntil\(cache\.put\(/);
  assert.doesNotMatch(worker, /^\s+cache\.put\(/m);
});

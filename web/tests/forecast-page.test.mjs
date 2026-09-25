import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  forecastPageContent, forecastProduct, forecastSitemapUrls, indexableLanguages,
  renderForecastPage, summarize
} from '$lib/server/forecast-page.js';
import data from '$lib/server/forecast-seo.generated.js';

const template = readFileSync(new URL('../static/forecast/index.html', import.meta.url), 'utf8');
const SITE = 'https://www.meteolabx.com';

test('cada mapa se sirve con sus metadatos y su guía en el HTML', () => {
  const product = forecastProduct('ebwd');
  const html = renderForecastPage(template, 'en', product);
  assert.match(html, /<html lang="en"/);
  assert.equal(html.match(/<title>/g).length, 1);
  assert.equal(html.match(/rel="canonical"/g).length, 1);
  assert.ok(html.includes(`<link rel="canonical" href="${SITE}/en/forecast/ebwd" />`));
  assert.ok(html.includes('content="index, follow, max-image-preview:large"'));
  for (const code of ['es', 'en', 'fr']) {
    assert.ok(html.includes(`hreflang="${code}" href="${SITE}/${code}/forecast/ebwd"`));
  }
  assert.ok(html.includes(`hreflang="x-default" href="${SITE}/es/forecast/ebwd"`));
  assert.ok(html.includes(`<h1>${product.labels.en}</h1>`));
  assert.ok(html.includes(product.guides.en.interpretation[0].slice(0, 40).replaceAll('&', '&amp;')));
  // Lo que traía el visor de serie no sobrevive: una sola descripción, un solo og:url.
  assert.equal(html.match(/name="description"/g).length, 1);
  assert.equal(html.match(/property="og:url"/g).length, 1);
  assert.ok(!html.includes('href="https://www.meteolabx.com/forecast"'));
  // El visor sigue cargando igual.
  assert.ok(html.includes('<base href="/forecast/" />'));
  assert.match(html, /<script type="module" crossorigin src="\/forecast\/assets\//);
});

test('los idiomas sin guía propia se sirven, pero no se indexan', () => {
  const html = renderForecastPage(template, 'ca', forecastProduct('ebwd'));
  assert.ok(html.includes('content="noindex, follow"'));
  assert.ok(!html.includes('hreflang='));
  assert.deepEqual(indexableLanguages(forecastProduct('ebwd')), ['es', 'en', 'fr']);
});

test('el índice enlaza todos los mapas', () => {
  const html = renderForecastPage(template, 'es');
  for (const product of data.products) {
    assert.ok(html.includes(`<a href="/es/forecast/${product.id}">`), product.id);
  }
  assert.ok(html.includes(`<link rel="canonical" href="${SITE}/es/forecast" />`));
});

test('el sitemap lista el índice y cada mapa en los idiomas indexables', () => {
  const urls = forecastSitemapUrls();
  const expected = 3 + data.products.reduce((total, product) => total + indexableLanguages(product).length, 0);
  assert.equal(urls.length, expected);
  assert.equal(new Set(urls).size, urls.length);
  assert.ok(urls.includes(`${SITE}/fr/forecast/ship`));
});

test('las descripciones caben en el resultado de búsqueda', () => {
  for (const product of data.products) {
    for (const language of indexableLanguages(product)) {
      const { description } = forecastPageContent(language, product);
      assert.ok(description.length >= 50 && description.length <= 158, `${language}/${product.id}`);
    }
  }
  assert.equal(summarize('uno dos tres', 8), 'uno dos…');
});

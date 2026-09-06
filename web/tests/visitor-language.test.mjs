import test from 'node:test';
import assert from 'node:assert/strict';
import { handle } from '../src/hooks.server.js';
import { LANGUAGE_COOKIE } from '../src/lib/server/language.js';
function event(path, language = '', saved = '') {
  const values = new Map([[LANGUAGE_COOKIE, saved]]);
  const headers = {};
  return {
    url: new URL(path, 'https://example.com'),
    request: new Request('https://example.com', { headers: { 'accept-language': language } }),
    cookies: { get: (key) => values.get(key), set: (key, value) => values.set(key, value), serialize: (key, value) => `${key}=${value}` },
    setHeaders: (value) => Object.assign(headers, value), headers
  };
}
const resolve = async () => new Response('page', { headers: { 'cache-control': 'public, max-age=60' } });
async function redirects(input, destination) {
  const response = await handle({ event: input, resolve });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), destination);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
}
test('enlace italiano conserva estación, pestaña y consulta en castellano', async () => {
  await redirects(event('/it/historical/tivissa?consulta=1&year=2025', 'es-ES'), '/es/historical/tivissa?consulta=1&year=2025');
  await redirects(event('/it/observation/AEMET/1234', 'es'), '/es/observation/AEMET/1234');
  await redirects(event('/es/observation/tivissa', 'it-IT'), '/it/observation/tivissa');
});
test('elección guardada prevalece sobre navegador y enlace', async () => {
  await redirects(event('/it/observation/tivissa', 'es', 'pt'), '/pt/observation/tivissa');
});
test('selector guarda elección y no vuelve al idioma del navegador', async () => {
  const input = event('/it/trends/tivissa?rango=sinoptica&set_language=it', 'es');
  await redirects(input, '/it/trends/tivissa?rango=sinoptica');
  assert.equal(input.cookies.get(LANGUAGE_COOKIE), 'it');
  const selected = await handle({ event: input, resolve });
  assert.equal(selected.headers.get('set-cookie'), 'meteolabx_language=it');
  input.url.searchParams.delete('set_language');
  assert.equal((await handle({ event: input, resolve })).status, 200);
});
test('sin idioma compatible conserva URL y separa la caché por visitante', async () => {
  for (const language of ['', 'de-DE']) {
    const response = await handle({ event: event('/it/observation/tivissa', language), resolve });
    assert.equal(response.status, 200);
    assert.match(response.headers.get('vary'), /Cookie/);
    assert.match(response.headers.get('vary'), /Accept-Language/);
  }
});

test('una página localizada que no se declara pública no se comparte', async () => {
  const privada = async () => new Response('page');
  const response = await handle({ event: event('/it/observation/tivissa', 'it'), resolve: privada });
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
});

test('las fichas que piden caché pública la conservan: son lo que rastrea Google', async () => {
  const response = await handle({ event: event('/it/observation/tivissa', 'it'), resolve });
  assert.equal(response.headers.get('cache-control'), 'public, max-age=60');
  assert.match(response.headers.get('vary'), /Cookie/);
});

test('quien ya tiene la versión recibe un 304 vacío', async () => {
  const etag = 'W/"abc-12"';
  const conEtag = async () => new Response('page', {
    headers: { 'cache-control': 'public, max-age=3600', etag }
  });
  const input = event('/it/observation/tivissa', 'it');
  input.request = new Request('https://example.com', {
    headers: { 'accept-language': 'it', 'if-none-match': etag }
  });
  const response = await handle({ event: input, resolve: conEtag });
  assert.equal(response.status, 304);
  assert.equal(await response.text(), '');
  assert.equal(response.headers.get('etag'), etag);
  assert.match(response.headers.get('vary'), /Cookie/);
});

test('con otra versión se sirve la página entera', async () => {
  const conEtag = async () => new Response('page', {
    headers: { 'cache-control': 'public, max-age=3600', etag: 'W/"nueva"' }
  });
  const input = event('/it/observation/tivissa', 'it');
  input.request = new Request('https://example.com', {
    headers: { 'accept-language': 'it', 'if-none-match': 'W/"vieja"' }
  });
  const response = await handle({ event: input, resolve: conEtag });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'page');
});
test('cookie inválida no se acepta y abrir enlaces no guarda preferencias', async () => {
  const input = event('/it/map', 'es', 'invalid');
  await redirects(input, '/es/map');
  assert.equal(input.cookies.get(LANGUAGE_COOKIE), 'invalid');
});
test('recursos sin idioma no se personalizan', async () => {
  const response = await handle({ event: event('/sitemap.xml', 'es'), resolve });
  assert.equal(response.headers.get('cache-control'), 'public, max-age=60');
});

test('la portada y los mapas siguen sin compartirse aunque pidan caché pública', async () => {
  // Llevan dentro la búsqueda y los filtros de quien mira: el `public` que
  // declaran es para el CDN, no para mezclar visitantes.
  for (const path of ['/', '/it/map', '/it/ranking', '/it/historical/tivissa']) {
    const response = await handle({ event: event(path, 'it'), resolve });
    assert.equal(response.headers.get('cache-control'), 'private, no-store', path);
  }
});

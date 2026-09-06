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
test('sin idioma compatible conserva URL y no comparte caché', async () => {
  for (const language of ['', 'de-DE']) {
    const response = await handle({ event: event('/it/observation/tivissa', language), resolve });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
    assert.match(response.headers.get('vary'), /Cookie/);
    assert.match(response.headers.get('vary'), /Accept-Language/);
  }
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

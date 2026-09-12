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
test('una URL con idioma se sirve en ese idioma, venga quien venga', async () => {
  // Antes se renegociaba: `/it/...` devolvía la página a quien tuviera
  // italiano y una redirección a quien tuviera otro idioma. Esa dependencia
  // del visitante obligaba a marcar la respuesta `Vary: Cookie`, y con eso
  // Cloudflare no comparte copia: cada visita iba al origen, y el HTML de las
  // fichas es el grueso de la salida de datos que se factura.
  //
  // Ahora un enlace conserva su idioma, que además es lo que espera quien lo
  // recibe: compartir una ficha en italiano ya no se la enseña en castellano
  // al que la abre.
  for (const [ruta, navegador, guardado] of [
    ['/it/historical/tivissa?consulta=1&year=2025', 'es-ES', ''],
    ['/it/observation/AEMET/1234', 'es', ''],
    ['/es/observation/tivissa', 'it-IT', ''],
    ['/it/observation/tivissa', 'es', 'pt']       // ni siquiera la elección guardada
  ]) {
    const response = await handle({ event: event(ruta, navegador, guardado), resolve });
    assert.equal(response.status, 200, ruta);
  }
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
test('una ficha compartible no separa la copia por cookie', async () => {
  // `Vary: Cookie` es lo que impide al CDN reutilizar la copia: Cloudflare
  // solo respeta `Vary: Accept-Encoding` y ante cualquier otro valor manda la
  // petición al origen.
  for (const language of ['', 'de-DE']) {
    const response = await handle({ event: event('/it/observation/tivissa', language), resolve });
    assert.equal(response.status, 200);
    assert.doesNotMatch(response.headers.get('vary'), /Cookie/);
  }
});

test('una página personal sí separa la copia de cada visitante', async () => {
  const response = await handle({ event: event('/it/ranking', 'es'), resolve });
  const vary = response.headers.get('vary');
  assert.match(vary, /Cookie/);
  assert.match(vary, /Accept-Language/);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
});

test('una página localizada que no se declara pública no se comparte', async () => {
  const privada = async () => new Response('page');
  const response = await handle({ event: event('/it/observation/tivissa', 'it'), resolve: privada });
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
});

test('lo compartible lleva TTL propio para el CDN', async () => {
  // `s-maxage` habla solo con las cachés compartidas; el navegador sigue con
  // su `max-age`.
  for (const ruta of ['/it/observation/tivissa', '/it/trends/tivissa']) {
    const response = await handle({ event: event(ruta, 'it'), resolve });
    const cc = response.headers.get('cache-control');
    assert.match(cc, /public/, ruta);
    assert.match(cc, /s-maxage=300/, ruta);
    assert.doesNotMatch(response.headers.get('vary'), /Cookie/, ruta);
  }
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
  assert.doesNotMatch(response.headers.get('vary'), /Cookie/);
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
test('abrir un enlace no guarda preferencias', async () => {
  // La cookie solo la escribe el selector de idioma, nunca una visita. Una
  // cookie con basura dentro no se corrige ni se borra sola: simplemente no
  // se usa para nada, porque el idioma sale de la URL.
  const input = event('/it/map', 'es', 'invalid');
  const response = await handle({ event: input, resolve });
  assert.equal(response.status, 200);
  assert.equal(input.cookies.get(LANGUAGE_COOKIE), 'invalid');
  assert.equal(response.headers.get('set-cookie'), null);
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

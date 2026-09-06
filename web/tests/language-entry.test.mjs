import test from 'node:test';
import assert from 'node:assert/strict';
import { load as stationEntry } from '../src/routes/observation/[slug]/+page.server.js';
import { registerHooks } from 'node:module';
import { GET as legacyEntry } from '../src/routes/app/+server.js';
import { GET as legacyRest } from '../src/routes/app/[...rest]/+server.js';

// SvelteKit inyecta este módulo al compilar; estas pruebas no consultan la API.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '$env/dynamic/private') {
      return { url: 'data:text/javascript,export const env = {};', shortCircuit: true };
    }
    return nextResolve(specifier, context);
  }
});
const { load: home } = await import('../src/routes/+page.server.js');

function event(language, params = {}) {
  const headers = {};
  return {
    params, headers,
    url: new URL('https://example.com/'),
    request: new Request('https://example.com/', { headers: { 'accept-language': language } }),
    setHeaders: (values) => Object.assign(headers, values)
  };
}

for (const [requested, expected] of [['it-IT,it;q=0.9', 'it'], ['pt-BR', 'pt'], ['es-ES', 'es'], ['en-US', 'en']]) {
  test(`entrada sin idioma respeta ${requested} y no guarda la redirección`, async () => {
    for (const [handler, destination] of [
      [stationEntry, `/${expected}/observation/jalance-8193e`],
      [legacyEntry, `/${expected}`],
      [legacyRest, `/${expected}`]
    ]) {
      const input = event(requested, { slug: 'jalance-8193e' });
      await assert.rejects(async () => handler(input), (cause) =>
        cause.status === 302 && cause.location === destination);
      assert.equal(input.headers['cache-control'], 'private, no-store');
      assert.equal(input.headers.vary, 'Accept-Language');
    }
  });
}

test('la portada separa la caché por idioma del navegador y respeta el idioma explícito', async () => {
  for (const language of ['it', 'pt', 'es', 'en']) {
    const input = event(language);
    assert.equal((await home(input)).language, language);
    assert.equal(input.headers.vary, 'Accept-Language');
  }
  const explicit = event('it', { lang: 'es' });
  assert.equal((await home(explicit)).language, 'es');
});

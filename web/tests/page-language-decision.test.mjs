import test from 'node:test';
import assert from 'node:assert/strict';
import { languageDecision } from '../src/lib/server/language.js';
import { recordVisit } from '../src/lib/stats.js';
const supported = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
const event = (header, saved) => ({ request: new Request('https://example.com/es/observation/test', { headers: header ? { 'accept-language': header } : {} }), cookies: { get: () => saved } });

test('distingue carga sin idioma del registro posterior en inglés', async () => {
  const decision = languageDecision(event(''), supported, 'es');
  assert.deepEqual(decision, { language: 'es', reason: 'url', requestLanguages: '' });
  const previous = globalThis.fetch;
  let sent;
  globalThis.fetch = async (_, init) => { sent = JSON.parse(init.body); return new Response(null, { status: 204 }); };
  try {
    recordVisit({ provider: 'AEMET', stationId: '3386A', language: decision.language, decision });
    assert.equal(sent.language, 'es');
    assert.equal(sent.language_reason, 'url');
    assert.equal(sent.page_request_languages, '');
  } finally { globalThis.fetch = previous; }
});
test('en-US en la carga selecciona inglés, salvo elección guardada', () => {
  assert.deepEqual(languageDecision(event('en-US'), supported, 'es'), { language: 'en', reason: 'browser', requestLanguages: 'en-us' });
  assert.deepEqual(languageDecision(event('en-US', 'fr'), supported, 'es'), { language: 'fr', reason: 'saved', requestLanguages: 'en-us' });
});

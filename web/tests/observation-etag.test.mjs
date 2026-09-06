/**
 * Las revisitas de Googlebot a una ficha que no ha cambiado.
 *
 * Son ~300.000 URLs y el rastreador vuelve a todas mucho más de lo que
 * publican las estaciones: sin validador cada vuelta se paga con el HTML
 * entero. Lo que se comprueba aquí es que el ETag distingue lo que tiene que
 * distinguir y no distingue lo que no.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { contentEtag, matchesEtag, observationVersion } from '../src/lib/server/etag.js';

test('la misma ficha con la misma observación da el mismo ETag', () => {
  const parts = ['observation', 'es', 'barcelona-drassanes-0201x', 'es', '1757000000', ''];
  assert.equal(contentEtag(...parts), contentEtag(...parts));
});

test('cambiar la medición, el idioma o la estación cambia el ETag', () => {
  const base = ['observation', 'es', 'barcelona-drassanes-0201x', 'es', '1757000000', ''];
  const etag = contentEtag(...base);
  assert.notEqual(etag, contentEtag('observation', 'es', 'barcelona-drassanes-0201x', 'es', '1757003600', ''));
  assert.notEqual(etag, contentEtag('observation', 'en', 'barcelona-drassanes-0201x', 'en', '1757000000', ''));
  assert.notEqual(etag, contentEtag('observation', 'es', 'recoaro-mille-cmt-22577', 'es', '1757000000', ''));
});

test('el ETag es débil y viene entrecomillado', () => {
  assert.match(contentEtag('observation', 'es', 'x', 'es', '1', ''), /^W\/"[0-9a-z]+-[0-9a-z]+"$/);
});

test('la versión de la observación sale de la medición', () => {
  assert.equal(observationVersion({ epoch: 1757000000 }), '1757000000');
});

test('una estación que deja de publicar cambia de versión', () => {
  const viva = observationVersion({ epoch: 1757000000 });
  const caida = observationVersion({ unavailable: { status: 502, code: 'provider_error' } });
  const historica = observationVersion({ unavailable: { status: 410, code: 'historical_station' } });
  assert.notEqual(viva, caida);
  assert.notEqual(caida, historica);
});

test('sin marca de tiempo utilizable no se versiona: mejor no poner ETag', () => {
  assert.equal(observationVersion({ epoch: 0 }), null);
  assert.equal(observationVersion({ epoch: null }), null);
  assert.equal(observationVersion({}), null);
});

test('If-None-Match casa como comparación débil', () => {
  const etag = 'W/"abc-12"';
  assert.ok(matchesEtag('W/"abc-12"', etag));
  assert.ok(matchesEtag('"abc-12"', etag), 'el prefijo W/ no cuenta al comparar');
  assert.ok(matchesEtag('W/"otra", W/"abc-12"', etag), 'la cabecera admite lista');
  assert.ok(matchesEtag('*', etag));
});

test('If-None-Match de otra versión no casa', () => {
  assert.equal(matchesEtag('W/"otra"', 'W/"abc-12"'), false);
  assert.equal(matchesEtag('', 'W/"abc-12"'), false);
  assert.equal(matchesEtag('W/"abc-12"', ''), false);
});

/**
 * De dónde llega quien abre una ficha.
 *
 * La clasificación decide lo que enseña el panel interno, así que conviene
 * fijarla: un fallo aquí no rompe nada visible, solo cuenta mal.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { classifyEntry } from '../src/lib/stats.js';

const HOST = 'www.meteolabx.com';

test('un buscador se reconoce por su dominio, con Google de cualquier país', () => {
  for (const referente of [
    'https://www.google.es/',
    'https://google.com/search?q=x',
    'https://www.google.co.uk/',
    'https://duckduckgo.com/',
    'https://search.brave.com/search?q=x',
    'https://es.search.yahoo.com/'
  ]) {
    const { kind } = classifyEntry(referente, HOST);
    assert.equal(kind, 'search', referente);
  }
  assert.equal(classifyEntry('https://www.google.es/', HOST).domain, 'google.es');
});

test('otro sitio es un enlace externo y se guarda su dominio, no la URL', () => {
  const entrada = classifyEntry('https://es.wikipedia.org/wiki/Tivissa', HOST);
  assert.deepEqual(entrada, { kind: 'external', domain: 'es.wikipedia.org' });
});

test('el propio sitio y los saltos dentro de la aplicación son navegación interna', () => {
  assert.equal(classifyEntry('https://www.meteolabx.com/es/map', HOST).kind, 'internal');
  // El enrutador lo dice aunque el referente siga siendo el de la primera carga.
  assert.equal(
    classifyEntry('https://www.google.es/', HOST, { interna: true }).kind,
    'internal'
  );
});

test('sin referente es directa, y no se inventa dominio', () => {
  for (const referente of ['', null, undefined, 'no-es-una-url']) {
    assert.deepEqual(classifyEntry(referente, HOST), { kind: 'direct', domain: '' });
  }
});

test('el interruptor de exclusión recuerda la decisión y sabe deshacerla', async () => {
  const { resolveOptOut } = await import('../src/lib/stats.js');
  // `?stats=off` apaga este navegador y lo deja apagado.
  assert.deepEqual(resolveOptOut('off', null), { excluido: true, guardar: true });
  assert.deepEqual(resolveOptOut(null, '1'), { excluido: true, guardar: true });
  // `?stats=on` vuelve a contarlo y borra la marca.
  assert.deepEqual(resolveOptOut('on', '1'), { excluido: false, guardar: false });
  // Sin parámetro ni marca previa se cuenta, que es lo normal.
  assert.deepEqual(resolveOptOut(null, null), { excluido: false, guardar: false });
});

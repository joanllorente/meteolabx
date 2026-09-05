import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer, get } from 'node:http';
import { createMetadataCache } from '../src/lib/server/metadata-cache.js';
import { createApiAgent } from '../src/lib/server/proxy-agent.js';
import { startLiveObservation } from '../src/lib/live.svelte.js';

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('los metadatos simultáneos y entre pestañas comparten una consulta, con copias independientes', async () => {
  const cache = createMetadataCache();
  let calls = 0;
  const load = async () => { calls++; return { name: 'Estación' }; };
  const [a, b] = await Promise.all([cache('public:A', load), cache('public:A', load)]);
  a.name = 'modificado';
  assert.equal(b.name, 'Estación');
  assert.equal((await cache('public:A', load)).name, 'Estación');
  assert.equal(calls, 1);
});

test('el catálogo caduca, acota memoria y no cachea errores', async () => {
  let now = 0;
  const cache = createMetadataCache({ ttlMs: 10, maxEntries: 2, now: () => now });
  let calls = 0;
  const load = async () => ++calls;
  assert.equal(await cache('a', load), 1);
  now = 11;
  assert.equal(await cache('a', load), 2);
  await cache('b', load);
  await cache('c', load);
  assert.equal(await cache('a', load), 5);
  await assert.rejects(cache('bad', async () => { throw Error('provider'); }));
  assert.equal(await cache('bad', load), 6);
});

test('la conexión HTTP al backend se reutiliza', async (t) => {
  let connections = 0;
  const server = createServer((_req, res) => res.end('ok'));
  server.on('connection', () => connections++);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const origin = `http://127.0.0.1:${server.address().port}`;
  const agent = createApiAgent(origin);
  t.after(() => { agent.destroy(); server.close(); });
  const request = () => new Promise((resolve, reject) => {
    get(origin, { agent }, (res) => { res.resume(); res.on('end', resolve); }).on('error', reject);
  });
  await request();
  await request();
  assert.equal(connections, 1);
  assert.equal(createApiAgent('https://example.com').protocol, 'https:');
});

test('el refresco no solapa peticiones, ignora pestañas ocultas y cancela al salir', async (t) => {
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const document = new EventTarget();
  document.hidden = false;
  globalThis.document = document;
  let calls = 0;
  let signal;
  let resolve;
  globalThis.fetch = (_url, options) => {
    calls++;
    signal = options.signal;
    return new Promise((done, reject) => {
      resolve = () => done({ ok: true, json: async () => ({ value: calls }) });
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  };
  t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let updates = 0;
  const stop = startLiveObservation({ provider: 'WU', stationId: 'A' }, () => updates++);
  t.after(() => {
    stop();
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
  });
  t.mock.timers.tick(15000);
  assert.equal(calls, 1);
  document.dispatchEvent(new Event('visibilitychange'));
  t.mock.timers.tick(15000);
  assert.equal(calls, 1);
  resolve();
  await flush();
  assert.equal(updates, 1);
  document.hidden = true;
  t.mock.timers.tick(15000);
  assert.equal(calls, 1);
  document.hidden = false;
  document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(calls, 2);
  stop();
  assert.equal(signal.aborted, true);
  await flush();
  assert.equal(updates, 1);
});

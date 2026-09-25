import assert from 'node:assert/strict';
import test from 'node:test';

import { isCrawlerRequest } from '../src/lib/server/crawler.js';

const request = (userAgent) => new Request('https://www.meteolabx.com/', {
  headers: { 'user-agent': userAgent }
});

test('reconoce los rastreadores que pueden recorrer el sitemap', () => {
  for (const agent of [
    'Mozilla/5.0 (compatible; Googlebot/2.1)',
    'Mozilla/5.0 (compatible; bingbot/2.0)',
    'Mozilla/5.0 (compatible; SemrushBot/7~bl)',
    'Mozilla/5.0 HeadlessChrome/140.0',
    'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)',
    'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +searchbot@anthropic.com)',
    'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)',
    'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot'
  ]) {
    assert.equal(isCrawlerRequest(request(agent)), true, agent);
  }
});

test('no clasifica un navegador normal como crawler', () => {
  assert.equal(
    isCrawlerRequest(request('Mozilla/5.0 AppleWebKit/537.36 Chrome/140.0 Safari/537.36')),
    false
  );
});

test('un rastreador no llega al proveedor, pero sí a la lectura guardada', async () => {
  const { handleFetch } = await import('../src/hooks.server.js');
  const googlebot = { request: request('Mozilla/5.0 (compatible; Googlebot/2.1)') };
  const pedidas = [];
  const fetch = async (outbound) => {
    pedidas.push(new URL(outbound.url).pathname);
    return new Response('{}', { status: 200 });
  };
  const pedir = (path) =>
    handleFetch({ event: googlebot, request: new Request(`http://api${path}`), fetch });

  assert.equal((await pedir('/v1/observations/current/processed')).status, 429);
  assert.equal((await pedir('/v1/climo/summary')).status, 429);
  assert.equal((await pedir('/v1/observations/snapshot?provider=METEOCAT&station_id=CG')).status, 200);
  assert.deepEqual(pedidas, ['/v1/observations/snapshot']);
});

test('sin lectura guardada, la ficha del buscador no enseña ningún aviso', async () => {
  const { isSnapshotMissing, SNAPSHOT_MISSING } = await import('../src/lib/observation/unavailable.js');
  assert.equal(isSnapshotMissing({ status: 404, code: SNAPSHOT_MISSING }), true);
  assert.equal(isSnapshotMissing({ status: 429, code: 'provider_ratelimit' }), false);
  assert.equal(isSnapshotMissing(undefined), false);
});

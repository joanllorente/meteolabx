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
    'Mozilla/5.0 HeadlessChrome/140.0'
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

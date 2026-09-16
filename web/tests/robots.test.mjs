import assert from 'node:assert/strict';
import test from 'node:test';

import { GET } from '../src/routes/robots.txt/+server.js';

test('pausa a los rastreadores de Anthropic sin cerrarles el sitio', async () => {
  const text = await GET().text();
  for (const agent of ['ClaudeBot', 'Claude-SearchBot', 'Claude-User']) {
    const group = text.split('\n\n').find((block) => block.startsWith(`User-agent: ${agent}\n`));
    assert.ok(group, agent);
    assert.match(group, /^Crawl-delay: 10$/m);
    assert.match(group, /^Allow: \/$/m);
    assert.doesNotMatch(group, /Disallow/);
  }
  assert.match(text, /User-agent: AhrefsBot\nDisallow: \//);
  assert.match(text, /User-agent: \*\nAllow: \//);
});

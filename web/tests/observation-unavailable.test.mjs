import assert from 'node:assert/strict';
import test from 'node:test';

import { ui } from '../src/lib/i18n/ui.js';
import { unavailableKey } from '../src/lib/observation/unavailable.js';

test('una red rechazada no se confunde con una estación callada', () => {
  assert.equal(
    unavailableKey({ status: 401, code: 'provider_unauthorized' }),
    'provider_unauthorized'
  );
  assert.equal(unavailableKey({ status: 403, code: 'provider_error' }), 'provider_unauthorized');
});

test('lentitud y falta de red se dicen aparte', () => {
  assert.equal(unavailableKey({ status: 504, code: 'provider_timeout' }), 'provider_timeout');
  assert.equal(unavailableKey({ status: 0, code: 'unreachable' }), 'provider_unreachable');
  assert.equal(
    unavailableKey({ status: 502, code: 'provider_network_error' }),
    'provider_unreachable'
  );
});

test('sin diagnóstico o sin lectura reciente, la estación está callada', () => {
  assert.equal(unavailableKey(null), 'data_unavailable');
  assert.equal(unavailableKey(undefined), 'data_unavailable');
  assert.equal(
    unavailableKey({ status: 200, code: 'provider_no_current_data' }),
    'data_unavailable'
  );
});

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
const keys = ['data_unavailable', 'provider_unauthorized', 'provider_timeout', 'provider_unreachable'];

for (const language of languages) {
  test(`los cuatro motivos están traducidos al ${language}`, () => {
    for (const key of keys) {
      const text = ui(language, key);
      assert.equal(typeof text, 'string');
      assert.ok(text.length > 10, `${language}.${key} sin traducir: ${text}`);
    }
  });
}

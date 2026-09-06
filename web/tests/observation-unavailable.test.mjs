import assert from 'node:assert/strict';
import test from 'node:test';

import { ui } from '../src/lib/i18n/ui.js';
import { describeRequestFailure, unavailableKey } from '../src/lib/observation/unavailable.js';

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

test('el límite de consultas del proveedor no acusa a la estación', () => {
  // 6076X (Marbella, AEMET) el 6/9/26: AEMET devolvió 429 y la ficha decía
  // «la estación no está publicando datos», cuando Marbella publicaba cada
  // diez minutos y quien no llegaba era este servidor.
  assert.equal(
    unavailableKey({ status: 429, code: 'provider_ratelimit' }),
    'provider_ratelimit'
  );
  assert.equal(unavailableKey({ status: 429, code: '' }), 'provider_ratelimit');
  assert.equal(unavailableKey({ status: 0, code: 'provider_ratelimit' }), 'provider_ratelimit');
});

test('sin diagnóstico o sin lectura reciente, la estación está callada', () => {
  assert.equal(unavailableKey(null), 'data_unavailable');
  assert.equal(unavailableKey(undefined), 'data_unavailable');
  assert.equal(
    unavailableKey({ status: 200, code: 'provider_no_current_data' }),
    'data_unavailable'
  );
});

test('una estación muda no acusa a su red de estar incomunicada', () => {
  // Monte Carpegna (MeteoHub Italia) el 6/9/26: llevaba dos días sin publicar
  // y MeteoHub respondía con un 200 y la lista vacía, pero la ficha decía «no
  // se ha podido contactar con el proveedor de esta red». El backend marca
  // este caso con un 502 —no pudo componer la respuesta—, así que el código
  // tiene que pesar más que el status.
  assert.equal(
    unavailableKey({ status: 502, code: 'provider_no_current_data' }),
    'data_unavailable'
  );
  // Un 502 sin ese código sigue siendo una red incomunicada.
  assert.equal(unavailableKey({ status: 502, code: 'provider_bad_response' }), 'provider_unreachable');
});

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
const keys = [
  'data_unavailable',
  'historical_station',
  'historical_station_relocated',
  'provider_unauthorized',
  'provider_timeout',
  'provider_ratelimit',
  'provider_unreachable'
];

for (const language of languages) {
  test(`los avisos de indisponibilidad e histórica están traducidos al ${language}`, () => {
    for (const key of keys) {
      const text = ui(language, key);
      assert.equal(typeof text, 'string');
      assert.ok(text.length > 10, `${language}.${key} sin traducir: ${text}`);
    }
  });
}

class FakeApiError extends Error {
  constructor(status, body) {
    super('api');
    this.status = status;
    this.body = body;
  }
}

test('quedarse sin tiempo no es lo mismo que no llegar', () => {
  // 10133, 10154, 10148 y 10125 (MeteoGalicia) daban «no se ha podido
  // contactar con el proveedor de esta red» sin código de estado. MeteoGalicia
  // contestaba: era el reloj de request() abortando a los 8 s una respuesta
  // que tarda entre 4 y 7.
  const abort = new Error('The operation was aborted');
  abort.name = 'AbortError';
  assert.deepEqual(describeRequestFailure(abort, { ApiError: FakeApiError }), {
    status: 504,
    code: 'provider_timeout'
  });
  assert.equal(unavailableKey(describeRequestFailure(abort, {})), 'provider_timeout');

  const timeout = new Error('timed out');
  timeout.name = 'TimeoutError';
  assert.equal(describeRequestFailure(timeout, {}).code, 'provider_timeout');
});

test('un fallo real de red sigue siendo inalcanzable', () => {
  assert.deepEqual(describeRequestFailure(new TypeError('fetch failed'), {}), {
    status: 0,
    code: 'unreachable'
  });
});

test('el error del backend conserva su código', () => {
  const failure = describeRequestFailure(
    new FakeApiError(429, { error_code: 'provider_ratelimit' }),
    { ApiError: FakeApiError }
  );
  assert.deepEqual(failure, { status: 429, code: 'provider_ratelimit' });
  assert.equal(unavailableKey(failure), 'provider_ratelimit');

  // Sin error_code en el cuerpo queda el genérico, no un "unreachable" falso.
  assert.equal(
    describeRequestFailure(new FakeApiError(500, {}), { ApiError: FakeApiError }).code,
    'provider_error'
  );
});

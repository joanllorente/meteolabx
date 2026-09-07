import assert from 'node:assert/strict';
import test from 'node:test';

import { refreshSecondsFor } from '../src/lib/personal.js';

test('Meteocat se refresca una vez por hora', () => {
  assert.equal(refreshSecondsFor('METEOCAT'), 3600);
  assert.equal(refreshSecondsFor('meteocat'), 3600);
});

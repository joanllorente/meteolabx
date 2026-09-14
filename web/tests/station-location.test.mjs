import assert from 'node:assert/strict';
import test from 'node:test';

import { countryLabel, stationLocationLabel } from '../src/lib/seo/i18n.js';

test('las estaciones de IEM enseñan su país aunque no tenga red propia', () => {
  assert.equal(stationLocationLabel({ provider: 'IEM', station_id: 'OIAG', country: 'IR' }, 'es'), 'Irán');
  assert.equal(countryLabel('IR', 'en'), 'Iran');
});

test('los códigos que no son un país no se enseñan', () => {
  for (const code of ['UN', 'AN', 'UNSPECIFIED', '']) assert.equal(countryLabel(code, 'es'), '');
  assert.equal(
    stationLocationLabel({ provider: 'WINDY', station_id: 'x', locality: '', country: 'UNSPECIFIED' }, 'es'),
    ''
  );
});

test('las etiquetas propias siguen mandando', () => {
  assert.equal(
    stationLocationLabel({ provider: 'EUSKALMET', station_id: 'C066', locality: 'Orozko', region: 'Bizkaia', country: 'ES' }, 'es'),
    'Orozko, Bizkaia, España'
  );
});

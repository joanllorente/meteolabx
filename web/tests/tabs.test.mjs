import assert from 'node:assert/strict';
import test from 'node:test';

import { observationTabs } from '../src/lib/tabs.js';

test('con slug, las tres vistas cuelgan del slug', () => {
  const tabs = observationTabs({ language: 'es', slug: 'el-prat-de-llobregat-0076' });
  assert.deepEqual(
    tabs.map((tab) => tab.href),
    [
      '/es/observation/el-prat-de-llobregat-0076',
      '/es/trends/el-prat-de-llobregat-0076',
      '/es/historical/el-prat-de-llobregat-0076'
    ]
  );
});

test('sin slug, la estación se identifica por red e identificador', () => {
  const tabs = observationTabs({ language: 'ca', provider: 'WU', stationId: 'IBARCELONA123' });
  assert.deepEqual(
    tabs.map((tab) => tab.href),
    [
      '/ca/observation/WU/IBARCELONA123',
      '/ca/trends/WU/IBARCELONA123',
      '/ca/historical/WU/IBARCELONA123'
    ]
  );
});

test('los identificadores con caracteres raros se escapan', () => {
  const [current] = observationTabs({
    language: 'es',
    provider: 'NETATMO',
    stationId: '70:ee:50:af:85:02'
  });
  assert.equal(current.href, '/es/observation/NETATMO/70%3Aee%3A50%3Aaf%3A85%3A02');
});

test('sin nada que identificar no hay barra', () => {
  assert.deepEqual(observationTabs({ language: 'es' }), []);
  assert.deepEqual(observationTabs({ language: 'es', provider: 'WU' }), []);
});

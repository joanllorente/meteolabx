import assert from 'node:assert/strict';
import test from 'node:test';

import {
  RECENT_LIMIT, forgetSearch, loadRecentSearches, matchRecentSearches, rememberSearch
} from '../src/lib/recent-searches.js';

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value))
  };
}

test('la última búsqueda va la primera y no se duplica', () => {
  const storage = memoryStorage();
  rememberSearch({ query: 'Girona', label: 'Girona, Catalunya' }, storage);
  rememberSearch({ query: 'Lleida', label: 'Lleida' }, storage);
  const list = rememberSearch({ query: '  girona ', label: 'Girona, Catalunya' }, storage);
  assert.deepEqual(list.map((item) => item.query), ['girona', 'Lleida']);
  assert.deepEqual(loadRecentSearches(storage), list);
});

test('se guardan como mucho las últimas', () => {
  const storage = memoryStorage();
  for (let i = 0; i < RECENT_LIMIT + 3; i += 1) rememberSearch({ query: `sitio ${i}` }, storage);
  const list = loadRecentSearches(storage);
  assert.equal(list.length, RECENT_LIMIT);
  assert.equal(list[0].query, `sitio ${RECENT_LIMIT + 2}`);
});

test('se pueden quitar', () => {
  const storage = memoryStorage();
  rememberSearch({ query: 'Girona' }, storage);
  rememberSearch({ query: 'Lleida' }, storage);
  assert.deepEqual(forgetSearch('GIRONA', storage).map((item) => item.query), ['Lleida']);
});

test('filtra sin mayúsculas ni acentos, también por el nombre del sitio', () => {
  const list = [
    { query: 'Cádiz', label: 'Cádiz, Andalucía' },
    { query: '41.38, 2.17', label: '41.38, 2.17' },
    { query: 'Vic', label: 'Vic, Osona' }
  ];
  assert.equal(matchRecentSearches(list, '').length, 3);
  assert.deepEqual(matchRecentSearches(list, 'cadi').map((item) => item.query), ['Cádiz']);
  assert.deepEqual(matchRecentSearches(list, 'osona').map((item) => item.query), ['Vic']);
});

test('sin almacenamiento, o con basura dentro, no revienta', () => {
  const broken = { getItem: () => { throw new Error('bloqueado'); }, setItem: () => { throw new Error('bloqueado'); } };
  assert.deepEqual(loadRecentSearches(broken), []);
  assert.equal(rememberSearch({ query: 'Girona' }, broken).length, 1);
  const junk = memoryStorage();
  junk.setItem('mlx-recent-searches', '{"no":"lista"}');
  assert.deepEqual(loadRecentSearches(junk), []);
});

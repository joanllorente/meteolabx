import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PRECIPITATION_TYPES, precipitationType, precipitationTypeLabel
} from '../src/data/precipitationTypes.js';

test('the eleven precipitation classes have distinct codes and colours', () => {
  assert.equal(PRECIPITATION_TYPES.length, 11);
  assert.equal(new Set(PRECIPITATION_TYPES.map((type) => type.code)).size, 11);
  assert.equal(new Set(PRECIPITATION_TYPES.map((type) => type.color)).size, 11);
  assert.equal(precipitationTypeLabel(precipitationType(8), 'es'), 'Gránulos de hielo');
  assert.equal(precipitationTypeLabel(precipitationType(10), 'en'), 'Hail');
  assert.equal(precipitationType(8.001)?.code, 8);
  assert.equal(precipitationType(2), undefined);
  assert.equal(precipitationType(9999), undefined);
});

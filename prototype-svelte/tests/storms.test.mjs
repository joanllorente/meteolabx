import test from 'node:test';
import assert from 'node:assert/strict';
import { nameStorms, stormPosition } from '../src/lib/storms.js';

const storm = {
  id: 'ep172026', name: 'Polo', classification: 'hurricane',
  track: [
    { time: '2026-09-27T12:00:00Z', latitude: 20, longitude: -115, stage: 'tropical' },
    { time: '2026-09-28T12:00:00Z', latitude: 22, longitude: -117, stage: 'remnant' }
  ]
};

// Rejilla de 0,25° con una baja profunda en (lat, lon).
function frameConBaja(lat, lon) {
  const bounds = [-125, 10, -105, 30];
  const width = 80, height = 80;
  const overlay = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const la = 30 - (y + 0.5) * 0.25, lo = -125 + (x + 0.5) * 0.25;
      const d2 = (la - lat) ** 2 + (lo - lon) ** 2;
      overlay[y * width + x] = 1010 - 40 * Math.exp(-d2 / 2);
    }
  }
  return { bounds, width, height, overlay };
}

test('interpola la posición del aviso y no inventa más allá del último punto', () => {
  const p = stormPosition(storm, '2026-09-28T00:00:00Z');
  assert.ok(Math.abs(p.latitude - 21) < 1e-9 && Math.abs(p.longitude + 116) < 1e-9);
  assert.equal(stormPosition(storm, '2026-09-29T00:00:00Z'), null);
  assert.equal(stormPosition(storm, '2026-09-27T06:00:00Z').latitude, 20);
  assert.equal(stormPosition(storm, '2026-09-26T12:00:00Z'), null);
});

test('el nombre va a la baja del modelo, no al punto del aviso', () => {
  const [polo] = nameStorms(frameConBaja(20.5, -114), [storm], '2026-09-27T12:00:00Z');
  assert.equal(polo.name, 'Polo');
  assert.ok(Math.abs(polo.latitude - 20.5) < 0.3 && Math.abs(polo.longitude + 114) < 0.3);
  assert.ok(polo.pressure < 975);
});

test('sin baja cerca no hay nombre', () => {
  assert.deepEqual(nameStorms(frameConBaja(12, -107), [storm], '2026-09-27T12:00:00Z'), []);
});

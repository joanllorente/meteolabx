import assert from 'node:assert/strict';
import test from 'node:test';

import { pointerFraction } from '../src/lib/observation/pointer.js';

/** Una gráfica cuyos extremos del eje caen donde diga la prueba. */
function chart(start, end) {
  const probe = (point) => ({
    getBoundingClientRect: () => ({ left: point.x, top: point.y, width: 0, height: 0 })
  });
  return { querySelectorAll: () => [probe(start), probe(end)] };
}

test('en horizontal, el puntero se sitúa a lo largo del eje', () => {
  const svg = chart({ x: 100, y: 50 }, { x: 500, y: 50 });
  assert.equal(pointerFraction(svg, { clientX: 300, clientY: 120 }), 0.5);
  assert.equal(pointerFraction(svg, { clientX: 200, clientY: 50 }), 0.25);
});

/**
 * El visor a pantalla completa gira la gráfica: el tiempo pasa a correr hacia
 * abajo. Es el caso que se quedaba clavado en la primera medida.
 */
test('girada, el tiempo corre hacia abajo y el cursor lo sigue', () => {
  const svg = chart({ x: 200, y: 100 }, { x: 200, y: 900 });
  assert.equal(pointerFraction(svg, { clientX: 260, clientY: 500 }), 0.5);
  assert.equal(pointerFraction(svg, { clientX: 140, clientY: 300 }), 0.25);
});

test('fuera del lienzo, el cursor se queda en los extremos', () => {
  const svg = chart({ x: 100, y: 50 }, { x: 500, y: 50 });
  assert.equal(pointerFraction(svg, { clientX: 20, clientY: 50 }), 0);
  assert.equal(pointerFraction(svg, { clientX: 900, clientY: 50 }), 1);
});

test('sin marcas o con el eje degenerado no se inventa posición', () => {
  assert.equal(pointerFraction(null, { clientX: 0, clientY: 0 }), null);
  assert.equal(pointerFraction({ querySelectorAll: () => [] }, { clientX: 0, clientY: 0 }), null);
  const punto = chart({ x: 100, y: 50 }, { x: 100, y: 50 });
  assert.equal(pointerFraction(punto, { clientX: 100, clientY: 50 }), null);
});

/**
 * Acumulado en ventana móvil: lo caído entre A y B es la diferencia de los
 * dos acumulados desde la pasada.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { clampWindowStart, windowBounds, windowedFrame } from '../src/lib/precipWindow.js';

const frame = (values, validTime) => ({ width: values.length, height: 1, valid_time: validTime, values: Float32Array.from(values) });

test('la ventana es B menos A, sin negativos y sin dato donde falte uno', () => {
  const b = frame([10, 5, 2.5, NaN, 3], 'B');
  const a = frame([4, 5, 2.51, 1, NaN], 'A');
  const ventana = windowedFrame(b, a);
  assert.deepEqual(Array.from(ventana.values.slice(0, 3)), [6, 0, 0]);
  assert.ok(Number.isNaN(ventana.values[3]) && Number.isNaN(ventana.values[4]));
  // El resto del frame es el de B: misma rejilla, misma hora.
  assert.equal(ventana.valid_time, 'B');
  assert.equal(ventana.window_start, 'A');
  assert.equal(Array.from(b.values)[0], 10, 'no toca el frame de la caché');
});

test('frames incompatibles no dan ventana', () => {
  assert.equal(windowedFrame(frame([1, 2], 'B'), frame([1], 'A')), null);
  assert.equal(windowedFrame(frame([1], 'B'), null), null);
});

test('la escala empieza en la pasada y A nunca alcanza a B', () => {
  const horas = [{ iso: 'h1', horizon: 1 }, { iso: 'h2', horizon: 2 }];
  const bordes = windowBounds('run', horas, () => ({ day: 'd', time: 't' }));
  assert.deepEqual(bordes.map((b) => [b.iso, b.horizon]), [['run', 0], ['h1', 1], ['h2', 2]]);
  assert.equal(clampWindowStart(5, 2), 1);
  assert.equal(clampWindowStart(-3, 2), 0);
  assert.equal(clampWindowStart(1, 3), 1);
});

test('el ruido de redondeo de los dos frames no se pinta como lluvia', () => {
  // Pasos de 0,05 mm: un paso de diferencia es ruido; por debajo de 0,1 mm,
  // o de los dos pasos juntos si son más gruesos, la ventana es cero.
  const paso = (step) => [{ name: 'value', offset: 0, step }];
  const b = { ...frame([12.1, 12.15, 13, 12.3, 12.2], 'B'), arrays: paso(0.05) };
  const a = { ...frame([12.05, 12.05, 12.05, 12.05, 12.05], 'A'), arrays: paso(0.05) };
  const ventana = windowedFrame(b, a);
  assert.equal(ventana.values[0], 0, '0,05 mm es un paso de ruido');
  assert.equal(ventana.values[1], 0, '0,1 mm no supera el umbral');
  assert.ok(Math.abs(ventana.values[2] - 0.95) < 1e-5);
  assert.ok(Math.abs(ventana.values[3] - 0.25) < 1e-5);
  // Con pasos de 0,1 mm el umbral sube a 0,2: 0,15 mm es ruido, 0,25 no.
  const gruesos = windowedFrame({ ...b, arrays: paso(0.1) }, { ...a, arrays: paso(0.1) });
  assert.equal(gruesos.values[4], 0);
  assert.ok(gruesos.values[3] > 0.2);
});

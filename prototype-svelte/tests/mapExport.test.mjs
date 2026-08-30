/** Aritmética de la exportación a PNG, que es donde se puede uno equivocar. */

import assert from 'node:assert/strict';
import test from 'node:test';

import { coloresDelDegradado, viewBoxAmpliado } from '../src/lib/mapExport.js';

test('sin margen, el viewBox es el de la rejilla', () => {
  const caja = { x: 40, y: 20, width: 800, height: 400 };
  assert.deepEqual(viewBoxAmpliado(caja, caja, 501, 241), [0, 0, 501, 241]);
});

test('el viewBox se amplía al área del mapa, con origen negativo', () => {
  // El SVG ocupa 800×400 dentro de un área de 900×450 y empieza 50 px a la
  // derecha y 25 más abajo: la imagen tiene que empezar antes de su propia caja
  // para no cortar lo que el mapa dibuja fuera de ella al ampliar.
  const svg = { x: 50, y: 25, width: 800, height: 400 };
  const area = { x: 0, y: 0, width: 900, height: 450 };
  assert.deepEqual(viewBoxAmpliado(svg, area, 800, 400), [-50, -25, 900, 450]);
});

test('la escala del viewBox respeta rejillas no cuadradas', () => {
  // 501×241 celdas en 1002×482 px: dos píxeles por celda en los dos ejes.
  const svg = { x: 10, y: 10, width: 1002, height: 482 };
  const area = { x: 0, y: 0, width: 1022, height: 502 };
  assert.deepEqual(viewBoxAmpliado(svg, area, 501, 241), [-5, -5, 511, 251]);
});

test('las paradas del degradado salen en orden', () => {
  const fondo = 'linear-gradient(90deg, rgb(59, 76, 192) 0%, rgb(50, 136, 189) 50%, rgba(118, 42, 131, 0.9) 100%)';
  assert.deepEqual(coloresDelDegradado(fondo), [
    'rgb(59, 76, 192)', 'rgb(50, 136, 189)', 'rgba(118, 42, 131, 0.9)'
  ]);
});

test('un fondo sin degradado no aporta colores', () => {
  assert.deepEqual(coloresDelDegradado('none'), []);
  assert.deepEqual(coloresDelDegradado(''), []);
});

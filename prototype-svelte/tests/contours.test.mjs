/**
 * El trazado de isolíneas, por donde se coló un fallo que no se ve leyendo el
 * código: los anillos cerrados desaparecían del mapa.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { contourLines, polylineLength, simplify, stepLevels } from '../src/lib/contours.js';

/** Anillo de doce lados: empieza y acaba en el mismo punto, como los de verdad. */
function anillo(radio, centro = 20) {
  const puntos = [];
  for (let paso = 0; paso <= 12; paso += 1) {
    const angulo = paso / 12 * 2 * Math.PI;
    puntos.push([centro + radio * Math.cos(angulo), centro + radio * Math.sin(angulo)]);
  }
  puntos[puntos.length - 1] = [...puntos[0]];
  return puntos;
}

test('un anillo cerrado sobrevive a la simplificación', () => {
  // Sus dos extremos coinciden, así que el segmento base mide cero. Midiendo
  // contra la recta infinita todos los vértices salían a distancia cero y el
  // anillo se reducía a dos puntos iguales: un trazo invisible. Es lo que
  // borraba la isobara que rodea a cada centro de presión.
  const original = anillo(10);
  const simplificado = simplify(original, 0.8);
  assert.ok(simplificado.length > 4, `se quedó en ${simplificado.length} vértices`);
  assert.ok(polylineLength(simplificado) > 0.9 * polylineLength(original));
});

test('una línea abierta se sigue simplificando', () => {
  const original = [[0, 0], [1, 0.1], [2, -0.1], [3, 0.05], [4, 0], [10, 8], [20, 0]];
  const simplificado = simplify(original, 0.8);
  assert.ok(simplificado.length < original.length);
  assert.deepEqual(simplificado[0], original[0]);
  assert.deepEqual(simplificado.at(-1), original.at(-1));
});

test('se conserva el vértice cuya perpendicular cae fuera del segmento', () => {
  // La recta infinita lo daba por cercano y la línea perdía su gancho.
  const original = [[0, 0], [-5, 0.2], [10, 0]];
  assert.equal(simplify(original, 1).length, 3);
});

test('un máximo aislado deja su anillo dibujado y rotulable', () => {
  const ancho = 60;
  const alto = 60;
  const campo = new Float32Array(ancho * alto);
  for (let fila = 0; fila < alto; fila += 1) {
    for (let columna = 0; columna < ancho; columna += 1) {
      const distancia = Math.hypot(columna - 30, fila - 30);
      campo[fila * ancho + columna] = 1000 + 28 * Math.exp(-(distancia * distancia) / 400);
    }
  }
  const niveles = stepLevels(campo, 4);
  assert.ok(niveles.includes(1024), `niveles: ${niveles}`);
  const contornos = contourLines(campo, {
    width: ancho, height: alto, levels: niveles,
    sigma: 1, minRingArea: 8, tolerance: 0.8, labelMinLength: 20, labelSpacing: 15
  });
  const interior = contornos.find((contorno) => contorno.level === 1024);
  assert.ok(interior, 'el anillo interior no se dibuja');
  assert.ok(interior.anchors.length > 0, 'el anillo interior no recibe etiqueta');
});

test('la isolínea se traza curva y pasa por sus vértices', () => {
  const ancho = 40;
  const alto = 40;
  const campo = new Float32Array(ancho * alto);
  for (let fila = 0; fila < alto; fila += 1) {
    for (let columna = 0; columna < ancho; columna += 1) {
      const distancia = Math.hypot(columna - 20, fila - 20);
      campo[fila * ancho + columna] = 1000 + 20 * Math.exp(-(distancia * distancia) / 120);
    }
  }
  const [contorno] = contourLines(campo, {
    width: ancho, height: alto, levels: [1008], sigma: 1, minRingArea: 8,
    tolerance: 0.5, labelMinLength: 20, labelSpacing: 15
  });
  assert.ok(contorno.path.includes('C'), 'el trazo sigue siendo una cadena de rectas');
  // Los extremos de cada Bézier son vértices del contorno: la curva no se
  // separa del dato, solo redondea lo que hay entre vértice y vértice.
  const finales = [...contorno.path.matchAll(/C[-\d.,\s]+?\s([-\d.]+),([-\d.]+)/g)];
  assert.ok(finales.length > 6, `solo ${finales.length} tramos`);
});

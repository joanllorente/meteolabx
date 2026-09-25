/**
 * Reparto de las líneas de corriente: separación pareja, sin cruces y con
 * flechas repartidas a lo largo de cada línea.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STREAM_MIN_LENGTH, STREAM_TEST_RATIO, evenlySpacedStreamlines, fadeSegments, sampleVectorField,
  streamlineArrows
} from '../src/lib/streamlines.js';

const CUADRO = { west: 0, east: 200, north: 0, south: 200 };
const SEPARACION = 10;

test('el viento de 10 m se traza aunque falte el diagnóstico vertical', () => {
  const frame = {
    width: 4, height: 4,
    values: new Float32Array(16).fill(NaN),
    u: new Float32Array(16).fill(4),
    v: new Float32Array(16).fill(1)
  };
  const sample = sampleVectorField(frame, 1.5, 1.5);
  assert.ok(sample);
  assert.equal(sample.u, 4);
  assert.equal(sample.v, 1);
  frame.u[5] = NaN;
  assert.equal(sampleVectorField(frame, 1.5, 1.5), null);
});

const trazar = (sample, extra = {}) => evenlySpacedStreamlines({
  sample, bounds: CUADRO, separation: SEPARACION, ...extra
});

/** Distancia mínima entre puntos de dos líneas distintas. */
function separacionEntreLineas(lineas) {
  let minima = Infinity;
  for (let a = 0; a < lineas.length; a += 1) {
    for (let b = a + 1; b < lineas.length; b += 1) {
      for (const [x0, y0] of lineas[a].points) {
        for (const [x1, y1] of lineas[b].points) {
          minima = Math.min(minima, Math.hypot(x1 - x0, y1 - y0));
        }
      }
    }
  }
  return minima;
}

test('un viento uniforme sale en líneas paralelas y parejas', () => {
  const lineas = trazar(() => ({ u: 1, v: 0 }));
  // El cuadro mide veinte separaciones de alto: ese es el orden de líneas.
  assert.ok(lineas.length >= 15 && lineas.length <= 25, `${lineas.length} líneas`);
  // Cada una cruza el cuadro entero en vez de aparecer y morir a medio mapa.
  const largas = lineas.filter((linea) => linea.length > 150).length;
  assert.ok(largas >= lineas.length - 2, `solo ${largas} llegan de lado a lado`);
  assert.ok(
    separacionEntreLineas(lineas) >= SEPARACION * STREAM_TEST_RATIO - 0.01,
    'dos líneas se tocan'
  );
});

test('las líneas no se pisan aunque el campo gire', () => {
  const remolino = (x, y) => ({ u: -(y - 100), v: -(x - 100) });
  const lineas = trazar(remolino);
  assert.ok(lineas.length > 5);
  assert.ok(
    separacionEntreLineas(lineas) >= SEPARACION * STREAM_TEST_RATIO - 0.01,
    'dos líneas se tocan en el giro'
  );
  // Una línea puede dar la vuelta entera al remolino sin cortarse contra sí
  // misma: eso es lo que distingue este reparto del de semillas en malla.
  assert.ok(Math.max(...lineas.map((linea) => linea.length)) > 200);
});

test('el hueco sin dato no se rellena ni corta el reparto', () => {
  const conIsla = (x, y) => (Math.hypot(x - 100, y - 100) < 30 ? null : { u: 1, v: 0 });
  const lineas = trazar(conIsla);
  for (const linea of lineas) {
    for (const [x, y] of linea.points) {
      assert.ok(Math.hypot(x - 100, y - 100) >= 30 - 1e-6, 'una línea entra en el hueco');
    }
  }
  assert.ok(lineas.length >= 15, `${lineas.length} líneas con isla`);
});

test('todo queda dentro del encuadre pedido', () => {
  for (const linea of trazar(() => ({ u: 0.6, v: 0.8 }))) {
    for (const [x, y] of linea.points) {
      assert.ok(x >= CUADRO.west && x <= CUADRO.east && y >= CUADRO.north && y <= CUADRO.south);
    }
  }
});

test('un campo sin viento no dibuja nada', () => {
  assert.deepEqual(trazar(() => null), []);
});

test('las flechas van repartidas a lo largo de la línea', () => {
  const [linea] = trazar(() => ({ u: 1, v: 0 }));
  const flechas = streamlineArrows(linea.points, 40);
  assert.ok(flechas.length >= 3, `${flechas.length} flechas`);
  for (let index = 1; index < flechas.length; index += 1) {
    const paso = Math.hypot(
      flechas[index].x - flechas[index - 1].x,
      flechas[index].y - flechas[index - 1].y
    );
    assert.ok(Math.abs(paso - 40) < 5, `flechas a ${paso.toFixed(1)}`);
  }
  // Viento del oeste: la punta mira a la derecha del mapa.
  assert.ok(Math.abs(flechas[0].angle) < 1);
});

test('una línea corta no lleva flecha', () => {
  assert.deepEqual(streamlineArrows([[0, 0], [1, 0]], 40), []);
});


test('los trozos sueltos se descartan', () => {
  // Un pasillo estrecho entre dos paredes sin dato: la semilla que caiga ahí
  // solo puede dar un rabito, y un rabito no se dibuja.
  const pasillo = (x, y) => (y > 98 && y < 108 && x > 60 ? { u: 1, v: 0 } : (y <= 98 ? { u: 1, v: 0 } : null));
  const lineas = trazar(pasillo);
  for (const linea of lineas) {
    assert.ok(linea.length >= SEPARACION * STREAM_MIN_LENGTH, `línea de ${linea.length.toFixed(1)}`);
  }
  // Y el mínimo se puede aflojar cuando quien llama sabe lo que hace.
  const conRabitos = trazar(pasillo, { minLength: SEPARACION * 0.5 });
  assert.ok(conRabitos.length >= lineas.length);
});

test('las puntas se desvanecen y los tramos cubren la línea entera', () => {
  const recta = Array.from({ length: 41 }, (unused, index) => [index * 2, 0]);
  const tramos = fadeSegments(recta, { fade: 16 });
  // Primero y último tramo, casi transparentes; el cuerpo, a plena tinta.
  assert.ok(tramos[0].opacity < 0.3 && tramos.at(-1).opacity < 0.3);
  assert.ok(tramos.some((tramo) => tramo.opacity === 1));
  // La tinta sube hasta el cuerpo y baja después: nada de saltos.
  const cuerpo = tramos.findIndex((tramo) => tramo.opacity === 1);
  for (let index = 1; index <= cuerpo; index += 1) {
    assert.ok(tramos[index].opacity >= tramos[index - 1].opacity);
  }
  for (let index = cuerpo + 1; index < tramos.length; index += 1) {
    assert.ok(tramos[index].opacity <= tramos[index - 1].opacity);
  }
  // Y los tramos van seguidos: el final de uno es el principio del siguiente.
  assert.deepEqual(tramos[0].points[0], recta[0]);
  assert.deepEqual(tramos.at(-1).points.at(-1), recta.at(-1));
  for (let index = 1; index < tramos.length; index += 1) {
    assert.deepEqual(tramos[index].points[0], tramos[index - 1].points.at(-1));
  }
});

test('una línea corta reparte el desvanecido sin quedarse sin cuerpo', () => {
  const corta = [[0, 0], [2, 0], [4, 0], [6, 0]];
  const tramos = fadeSegments(corta, { fade: 40 });
  assert.ok(tramos.length >= 2);
  assert.deepEqual(tramos[0].points[0], corta[0]);
  assert.deepEqual(tramos.at(-1).points.at(-1), corta.at(-1));
  assert.ok(tramos.every((tramo) => tramo.opacity > 0 && tramo.opacity <= 1));
});

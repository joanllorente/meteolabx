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

test('el viento se traza con su dirección real en una rejilla de latitud y longitud', async () => {
  const { gridDirection } = await import('../src/lib/streamlines.js');
  // Rejilla de 1° entre 50 y 70° N: la fila 10,5 cae a 59,5° N.
  const frame = { width: 20, height: 20, bounds: [0, 50, 20, 70] };
  const { u, v, magnitude } = gridDirection(frame, 10, 10, 10.5);
  // Un suroeste de 45° cruza 1/cos φ veces más columnas que filas.
  const esperado = Math.atan(Math.cos(59.5 * Math.PI / 180)) * 180 / Math.PI;
  assert.ok(Math.abs(Math.atan2(v, u) * 180 / Math.PI - esperado) < 1e-9);
  assert.ok(Math.abs(magnitude - Math.hypot(10, 10)) < 1e-9);
  assert.ok(Math.abs(Math.hypot(u, v) - magnitude) < 1e-9);
  // Del oeste o del sur puros no cambian.
  const oeste = gridDirection(frame, 10, 0, 10.5);
  assert.ok(Math.abs(oeste.v) < 1e-12 && oeste.u > 0);
});

test('los tramos se juntan en un trazo por opacidad', async () => {
  const { pathsByOpacity } = await import('../src/lib/streamlines.js');
  const trazo = (puntos) => `M${puntos.map(([x, y]) => `${x},${y}`).join('L')}`;
  const capas = pathsByOpacity([
    { points: [[0, 0], [1, 0]], opacity: 1 },
    { points: [[5, 5], [6, 5]], opacity: 0.4 },
    { points: [[2, 0], [3, 0]], opacity: 1 },
    { points: [[7, 5], [8, 5]], opacity: 0.4001 }
  ], trazo);
  assert.deepEqual(capas, [
    { opacity: 0.4, d: 'M5,5L6,5M7,5L8,5' },
    { opacity: 1, d: 'M0,0L1,0M2,0L3,0' }
  ]);
});

test('las puntas de flecha apuntan en el sentido de la línea', async () => {
  const { arrowHeadsPath } = await import('../src/lib/streamlines.js');
  const numeros = (d) => d.match(/-?\d+(\.\d+)?/g).map(Number);
  // Hacia la derecha: las alas quedan detrás (x menor), una a cada lado.
  const [ax, ay, px, py, bx, by] = numeros(arrowHeadsPath([{ x: 10, y: 10, angle: 0 }], 4));
  assert.deepEqual([px, py], [10, 10]);
  assert.ok(ax < 10 && bx < 10);
  assert.ok(Math.abs(ay - 10 + (by - 10)) < 1e-9 && ay !== by);
  // Hacia abajo (90°): las alas quedan por encima.
  const abajo = numeros(arrowHeadsPath([{ x: 10, y: 10, angle: 90 }], 4));
  assert.ok(abajo[1] < 10 && abajo[5] < 10);
  // Varias flechas, un solo trazo con un subtrazo por flecha.
  assert.equal(arrowHeadsPath([{ x: 0, y: 0, angle: 0 }, { x: 5, y: 5, angle: 45 }], 2).split('M').length - 1, 2);
});

test('el reparto no se dispara con muchas semillas', () => {
  // Con `shift()` sobre la cola, un encuadre ampliado tardaba casi un segundo
  // por el coste cuadrático. Un campo que gira siempre deja miles de semillas.
  const ancho = 600;
  const sample = (x, y) => ({ u: Math.sin(y / 23) + 1.5, v: Math.cos(x / 31), magnitude: 1 });
  const inicio = performance.now();
  const lineas = evenlySpacedStreamlines({
    sample, bounds: { west: 0, east: ancho, north: 0, south: ancho }, separation: 6, maxLines: 400
  });
  assert.ok(lineas.length > 50);
  assert.ok(performance.now() - inicio < 1500, 'el reparto es cuadrático otra vez');
});

test('una zona a la que no llega la propagación también recibe líneas', () => {
  // Dos islas de dato separadas por una franja vacía: las semillas nacen al
  // lado de líneas aceptadas y no pueden cruzarla. Antes, la segunda isla se
  // quedaba en blanco.
  const sample = (x) => (x < 80 || x > 120 ? { u: 0, v: 1, magnitude: 1 } : null);
  const lineas = trazar(sample);
  const izquierda = lineas.filter((linea) => linea.points.every(([x]) => x < 80));
  const derecha = lineas.filter((linea) => linea.points.every(([x]) => x > 120));
  assert.ok(izquierda.length >= 5, `${izquierda.length} líneas a la izquierda`);
  assert.ok(derecha.length >= 5, `${derecha.length} líneas a la derecha`);
});

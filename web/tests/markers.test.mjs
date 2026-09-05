/**
 * Las cajas del mapa tienen que salir con el mismo color y el mismo agrupado
 * que las de la aplicación actual: es el mismo mapa visto desde otro
 * frontend, y una escala distinta se nota a simple vista.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  cellSizeForZoom,
  clusterByGrid,
  colorForTemperature,
  meanDirection,
  meanOf,
  textColor
} from '../src/lib/map/markers.js';

test('la paleta reproduce los cortes de la app actual', () => {
  assert.deepEqual(colorForTemperature(0), [88, 176, 245]);
  assert.deepEqual(colorForTemperature(30), [238, 92, 28]);
  // Interpolación a mitad de tramo, entre 20 °C y 25 °C.
  assert.deepEqual(colorForTemperature(22.5), [249, 184, 44]);
});

test('los extremos se recortan en vez de extrapolar', () => {
  assert.deepEqual(colorForTemperature(-80), colorForTemperature(-20));
  assert.deepEqual(colorForTemperature(120), colorForTemperature(46));
});

test('el número se lee sobre su fondo', () => {
  assert.equal(textColor([250, 210, 50]), '#191b21');
  assert.equal(textColor([98, 22, 146]), '#ffffff');
});

test('la celda se encoge al acercarse', () => {
  assert.equal(cellSizeForZoom(3), 112);
  assert.equal(cellSizeForZoom(9), 68);
  assert.equal(cellSizeForZoom(17), 14);
  assert.equal(cellSizeForZoom(Number.NaN), 112);
});

test('los puntos de una misma celda caen en el mismo grupo', () => {
  const grupos = clusterByGrid(
    [
      { x: 10, y: 10, t: 20 },
      { x: 30, y: 20, t: 22 },
      { x: 500, y: 500, t: 10 }
    ],
    100
  );
  assert.equal(grupos.length, 2);
  assert.equal(grupos.find((g) => g.length === 2).length, 2);
});

test('la media de rumbos promedia vectores, no grados', () => {
  // 350° y 10° están a 20° entre sí: su media es el norte, no el sur.
  const media = meanDirection([{ direction: 350 }, { direction: 10 }]);
  assert.ok(media < 1 || media > 359, `media inesperada: ${media}`);
  assert.equal(meanDirection([{ direction: null }]), null);
});

test('la media ignora los huecos', () => {
  assert.equal(meanOf([{ t: 10 }, { t: null }, { t: 20 }], 't'), 15);
  assert.equal(meanOf([{ t: null }], 't'), null);
});

test('el rango de las tendencias tiene suelo simétrico', async () => {
  const { symmetricRange } = await import('../src/lib/observation/scale.js');
  // Serie plana: el eje no baja del mínimo, así que el ruido no se amplifica
  // hasta parecer una tormenta.
  assert.deepEqual(symmetricRange([0.1, -0.2, 0.05], 5), [-5, 5]);
  // Serie que lo supera: se ajusta a ella con un 10 % de aire y se redondea
  // al salto del eje, para que el borde sea una marca y no un 8,8 suelto.
  assert.deepEqual(symmetricRange([0, 8, -3], 5), [-10, 10]);
  // Los huecos no cuentan.
  assert.deepEqual(symmetricRange([null, undefined, Number.NaN], 20), [-20, 20]);
  // Simétrico aunque los valores sean todos del mismo signo.
  assert.deepEqual(symmetricRange([30, 20], 5), [-40, 40]);
});

// --- Cursor compartido de las tendencias -----------------------------------

const { nearestIndex } = await import('../src/lib/observation/cursor.js');
const require_scale = await import('../src/lib/observation/scale.js');

test('nearestIndex empareja el punto del mismo momento', () => {
  const cada20min = [0, 1200, 2400, 3600, 4800];
  assert.equal(nearestIndex(cada20min, 2400), 2);
  assert.equal(nearestIndex(cada20min, 2500), 2);
});

test('nearestIndex cruza series de paso distinto', () => {
  // Presión cada 3 h frente a θe cada 20 min: el cursor sobre las 4:00 de θe
  // tiene que caer en el punto de las 3:00 de presión, no quedarse en blanco.
  const cada3h = [0, 10800, 21600, 32400];
  assert.equal(nearestIndex(cada3h, 14400), 1);
});

test('nearestIndex no marca nada dentro de un hueco', () => {
  // Sensor caído entre las 2:00 y las 6:00: ahí no hubo medida y la gráfica
  // no debe fingir uná.
  const conHueco = [0, 3600, 7200, 21600, 25200];
  assert.equal(nearestIndex(conHueco, 14400), null);
});

test('nearestIndex tolera series vacías y valores no numéricos', () => {
  assert.equal(nearestIndex([], 100), null);
  assert.equal(nearestIndex([0, 1200], null), null);
  assert.equal(nearestIndex([null, 1200, null], 1250), 1);
});

// --- Ejes con saltos redondos ----------------------------------------------

test('niceTicks marca el cero y salta de número redondo en número redondo', () => {
  const { niceTicks } = require_scale;
  assert.deepEqual(niceTicks(-20, 20), [-20, -10, 0, 10, 20]);
  assert.deepEqual(niceTicks(-5, 5), [-5, 0, 5]);
});

test('niceTicks usa un decimal solo cuando el rango es pequeño', () => {
  const { niceTicks } = require_scale;
  // Presión: ±1 hPa/h. Con saltos enteros el eje se quedaría en -1, 0, 1.
  assert.deepEqual(niceTicks(-1, 1), [-1, -0.5, 0, 0.5, 1]);
});

test('symmetricRange alinea el borde con la marca del eje', () => {
  const { symmetricRange, niceTicks } = require_scale;
  // 0,64 · 1,1 = 0,704: antes el eje remataba en 0,7 y en -0,23.
  const range = symmetricRange([0.64, -0.5, 0.2], 0);
  assert.deepEqual(range, [-1, 1]);
  assert.ok(niceTicks(...range).includes(0));
});

test('symmetricRange sigue respetando la escala mínima de cada magnitud', () => {
  const { symmetricRange } = require_scale;
  assert.deepEqual(symmetricRange([0.1, -0.2], 20), [-20, 20]);
  assert.deepEqual(symmetricRange([0.1, -0.2], 5), [-5, 5]);
});

// --- Nombre del PNG que se descarga ----------------------------------------

test('fileSlug produce un nombre de fichero utilizable', async () => {
  const { fileSlug } = await import('../src/lib/chart-export.js');
  // Acentos, punto medio y mayúsculas: nada de eso puede ir en el nombre.
  assert.equal(
    fileSlug('meteolabx Tendencia de Presión Absoluta · Can Bruixa'),
    'meteolabx-tendencia-de-presion-absoluta-can-bruixa'
  );
  // Sin nombre, un nombre igualmente.
  assert.equal(fileSlug(''), 'grafica');
  assert.equal(fileSlug(null), 'grafica');
  // Largo acotado: hay estaciones con nombres kilométricos.
  assert.ok(fileSlug('x'.repeat(200)).length <= 80);
});

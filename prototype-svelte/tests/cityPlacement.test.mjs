/**
 * Los rótulos de ciudad: qué entra en cada nivel de zoom, dónde cae cada
 * nombre sobre la rejilla y quién gana cuando dos se pisan.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { CITY_LABELS } from '../src/data/cityLabels.js';
import { CITY_LABELS_DETAIL } from '../src/data/cityLabelsDetail.js';
import { cityRank, cityRoom, placeCities } from '../src/lib/cityPlacement.js';
import { frameGeo } from '../src/lib/projection.js';

// Rejilla de juguete con el encuadre de AROME: 0,05° por celda.
const FRAME = {
  width: 200,
  height: 200,
  bounds: [-10, 35, 10, 55],
  values: new Float32Array(200 * 200).fill(12),
  unit: '°C'
};
const TODO = { west: 0, east: 200, north: 0, south: 200 };
const opciones = (extra = {}) => ({
  catalogue: CITY_LABELS,
  frame: FRAME,
  bounds: TODO,
  viewZoom: 1,
  labelScale: 1,
  format: (value) => `${value.toFixed(0)} °C`,
  ...extra
});

test('el nivel de detalle crece con el zoom y se para en 7', () => {
  assert.equal(cityRank(1), 3);
  assert.equal(cityRank(2.5), 3);
  assert.equal(cityRank(3.5), 4);
  assert.equal(cityRank(4.5), 5);
  assert.equal(cityRank(6), 6);
  assert.equal(cityRank(8), 7);
  // Nunca retrocede: un mapa más cerca no puede pedir menos detalle.
  let previo = 0;
  for (let zoom = 1; zoom <= 8; zoom += 0.1) {
    const nivel = cityRank(zoom);
    assert.ok(nivel >= previo, `retrocede en ${zoom}`);
    previo = nivel;
  }
});

test('con el mapa entero entran más ciudades que las capitales, repartidas', () => {
  const dominio = {
    ...FRAME, width: 1121, height: 717, bounds: [-12, 37.5, 16, 55.4],
    values: new Float32Array(1121 * 717).fill(12)
  };
  const puestas = placeCities(opciones({
    frame: dominio, bounds: { west: 0, east: 1121, north: 0, south: 717 }
  }));
  const nombres = puestas.map((ciudad) => ciudad.name);
  for (const nombre of ['Zaragoza', 'Bilbao', 'Toulouse', 'Lyon', 'Fráncfort']) {
    assert.ok(nombres.includes(nombre), `falta ${nombre} al 100 %`);
  }
  assert.ok(puestas.length < 60, 'al 100 % no se debe llegar al tope');
  assert.ok(Math.abs(cityRoom(1) - 1.8) < 1e-9);
  assert.equal(cityRoom(2.8), 1);
});

test('ampliar el mapa nunca quita ciudades que ya estaban', () => {
  const cerca = placeCities(opciones({ viewZoom: 4 })).map((ciudad) => ciudad.name);
  for (const nombre of placeCities(opciones({ viewZoom: 1 })).map((c) => c.name)) {
    assert.ok(cerca.includes(nombre), `${nombre} desaparece al ampliar`);
  }
  assert.ok(cerca.length > placeCities(opciones()).length);
});

test('la ciudad cae en la celda que le toca', () => {
  const [, latitude, longitude] = CITY_LABELS.find(([name]) => name === 'Madrid');
  const madrid = placeCities(opciones()).find((ciudad) => ciudad.name === 'Madrid');
  assert.ok(madrid);
  assert.ok(Math.abs(madrid.x - (longitude + 10) / 20 * 200) < 0.01);
  assert.ok(Math.abs(madrid.y - (55 - latitude) / 20 * 200) < 0.01);
});

test('el valor rotulado es el de la celda, con el formato del mapa', () => {
  const [primera] = placeCities(opciones());
  assert.equal(primera.text, '12 °C');
});

test('una celda sin dato deja la ciudad sin rótulo', () => {
  const values = new Float32Array(FRAME.values);
  const madrid = placeCities(opciones()).find((ciudad) => ciudad.name === 'Madrid');
  values[Math.floor(madrid.y) * FRAME.width + Math.floor(madrid.x)] = Number.NaN;
  const conHueco = placeCities(opciones({ frame: { ...FRAME, values } }));
  assert.ok(!conHueco.some((ciudad) => ciudad.name === 'Madrid'));
});

test('fuera del encuadre visible no se rotula nada', () => {
  const puestas = placeCities(opciones({
    bounds: { west: 0, east: 10, north: 0, south: 10 }
  }));
  for (const ciudad of puestas) {
    assert.ok(ciudad.x <= 10 && ciudad.y <= 10, `${ciudad.name} se cuela fuera de cuadro`);
  }
});

test('dos rótulos no se pisan y gana el que manda el catálogo', () => {
  const puestas = placeCities(opciones({ viewZoom: 2 }));
  for (let uno = 0; uno < puestas.length; uno += 1) {
    for (let otro = uno + 1; otro < puestas.length; otro += 1) {
      const a = puestas[uno];
      const b = puestas[otro];
      const solapan = Math.abs(a.x - b.x) < (a.anchura + b.anchura) / 2
        && Math.abs(a.y - b.y) < 34 / 2;
      assert.ok(!solapan, `${a.name} y ${b.name} se pisan`);
    }
  }
  const orden = CITY_LABELS.map(([name]) => name);
  const indices = puestas.map((ciudad) => orden.indexOf(ciudad.name));
  assert.deepEqual(indices, [...indices].sort((uno, otro) => uno - otro));
});

test('el tope de rótulos se respeta', () => {
  assert.ok(placeCities(opciones({ viewZoom: 8, max: 6 })).length <= 6);
});

test('el catálogo está bien formado', () => {
  assert.ok(CITY_LABELS.length > 3000);
  const vistos = new Set();
  for (const [name, latitude, longitude, rank] of CITY_LABELS) {
    assert.equal(typeof name, 'string');
    assert.ok(name.length > 1, `nombre vacío: ${JSON.stringify(name)}`);
    assert.ok(latitude >= 33 && latitude <= 59, `${name} fuera de ventana`);
    assert.ok(longitude >= -16 && longitude <= 24, `${name} fuera de ventana`);
    assert.ok(!/\d/.test(name), `${name} parece un distrito numerado`);
    assert.ok(Number.isInteger(rank) && rank >= 1 && rank <= 6, `${name} con rango ${rank}`);
    // El nombre sí se repite —hay dos Soest y varias Halle—, la coordenada no.
    const clave = `${latitude}|${longitude}`;
    assert.ok(!vistos.has(clave), `${name} repetida en ${clave}`);
    vistos.add(clave);
  }
  // Las capitales grandes tienen que estar en la primera tanda.
  const primeras = CITY_LABELS.filter(([, , , rank]) => rank === 1).map(([name]) => name);
  for (const capital of ['Madrid', 'París', 'Londres', 'Roma', 'Berlín', 'Lisboa']) {
    assert.ok(primeras.includes(capital), `falta ${capital} en el primer nivel`);
  }
});

test('con el zoom al máximo entran los núcleos pequeños, y no antes', () => {
  const catalogue = [...CITY_LABELS, ...CITY_LABELS_DETAIL];
  assert.ok(CITY_LABELS_DETAIL.every((ciudad) => ciudad[3] === 7));
  assert.ok(CITY_LABELS.every((ciudad) => ciudad[3] < 7));
  // Pirineo catalán sobre la rejilla de AROME, 0,025° por celda.
  const frame = {
    ...FRAME, width: 1121, height: 717, bounds: [-12.0125, 37.4875, 16.0125, 55.4125],
    values: new Float32Array(1121 * 717).fill(12)
  };
  const geo = frameGeo(frame);
  const [west, north] = geo.toGrid(1.3, 42.6);
  const [east, south] = geo.toGrid(2.5, 41.8);
  const bounds = { west, east, north, south };
  const nombres = (viewZoom) => placeCities(opciones({ catalogue, frame, bounds, viewZoom }))
    .map((ciudad) => ciudad.name);
  assert.ok(nombres(8).includes('Puigcerdà'), 'falta Puigcerdà a 8×');
  assert.ok(!nombres(5.9).includes('Puigcerdà'), 'Puigcerdà no debe entrar a 5,9×');
});

test('lo que se ve no depende de las ciudades que quedan fuera de la pantalla', () => {
  // Valle del Ebro a 8×: el margen de fuera de la vista llega a Pamplona, Logroño
  // o Lleida, que van antes en el catálogo. Contaban para el tope y dejaban sin
  // sitio a Tauste, que sí estaba en pantalla, según hacia dónde se moviera.
  const catalogue = [...CITY_LABELS, ...CITY_LABELS_DETAIL];
  const frame = {
    ...FRAME, width: 1121, height: 717, bounds: [-12.0125, 37.4875, 16.0125, 55.4125],
    values: new Float32Array(1121 * 717).fill(12)
  };
  const geo = frameGeo(frame);
  const encuadre = (oeste, norte, este, sur, margen) => {
    const [west, north] = geo.toGrid(oeste - margen, norte + margen);
    const [east, south] = geo.toGrid(este + margen, sur - margen);
    return { west, east, north, south };
  };
  const nombres = (oeste, norte, este, sur) => placeCities(opciones({
    catalogue, frame, viewZoom: 8,
    bounds: encuadre(oeste, norte, este, sur, 1.2),
    visible: encuadre(oeste, norte, este, sur, 0)
  })).map((ciudad) => ciudad.name);
  for (const vista of [nombres(-1.9, 42.4, 0.2, 41.2), nombres(-1.6, 42.9, 0.5, 41.7)]) {
    assert.ok(vista.includes('Tauste'), 'Tauste debe salir en los dos encuadres');
  }
});

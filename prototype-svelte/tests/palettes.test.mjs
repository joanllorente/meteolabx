/**
 * La escala con nodos, que es la que decide cuánta rampa se lleva cada tramo
 * de temperatura: si un nodo se saltara o se extrapolara fuera de rango, el
 * mapa pintaría el mismo color para −25 y para 45.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { readFileSync } from 'node:fs';

import { anchorFraction } from '../src/lib/palettes.js';

// El catálogo de productos se lee del fuente en vez de importarse: depende de
// `import.meta.env`, que solo existe dentro de Vite.
const fuente = readFileSync(new URL('../src/data/forecastProducts.js', import.meta.url), 'utf8');

function listaDe(clave) {
  const bloque = fuente.slice(fuente.indexOf("id: 'temperature-2m'"));
  const encontrado = bloque.match(new RegExp(`${clave}: (\\[[^;]*?\\]),\\n`));
  assert.ok(encontrado, `${clave} no está en temperature-2m`);
  return JSON.parse(encontrado[1]);
}

function numeroDe(clave) {
  const bloque = fuente.slice(fuente.indexOf("id: 'temperature-2m'"));
  const encontrado = bloque.match(new RegExp(`${clave}: (-?\\d+(?:\\.\\d+)?)`));
  assert.ok(encontrado, `${clave} no está en temperature-2m`);
  return Number(encontrado[1]);
}

const T2M = {
  min: numeroDe('min'),
  max: numeroDe('max'),
  scaleAnchors: listaDe('scaleAnchors'),
  scaleTicks: listaDe('scaleTicks')
};

test('los nodos caen exactamente en su fracción', () => {
  for (const [valor, fraccion] of T2M.scaleAnchors) {
    assert.equal(anchorFraction(valor, T2M.scaleAnchors), fraccion);
  }
});

test('dentro de un tramo el reparto sigue siendo lineal', () => {
  // Mitad del tramo 0–10 °C, que va de 0,22 a 0,42 de la rampa.
  assert.ok(Math.abs(anchorFraction(5, T2M.scaleAnchors) - 0.32) < 1e-9);
});

test('fuera de los nodos se recorta en vez de extrapolar', () => {
  assert.equal(anchorFraction(-40, T2M.scaleAnchors), 0);
  assert.equal(anchorFraction(60, T2M.scaleAnchors), 1);
});

test('la escala es monótona y da más rampa a la franja habitual', () => {
  let anterior = -Infinity;
  for (let valor = -30; valor <= 50; valor += 0.5) {
    const fraccion = anchorFraction(valor, T2M.scaleAnchors);
    assert.ok(fraccion >= anterior, `retrocede en ${valor}`);
    anterior = fraccion;
  }
  const central = anchorFraction(30, T2M.scaleAnchors) - anchorFraction(0, T2M.scaleAnchors);
  const cola = anchorFraction(0, T2M.scaleAnchors) - anchorFraction(-25, T2M.scaleAnchors);
  // Treinta grados de franja habitual pesan más que veinticinco de cola fría.
  assert.ok(central / 30 > cola / 25 * 2);
});

test('cada valor rotulado en la leyenda está dentro de la escala', () => {
  for (const tick of T2M.scaleTicks) {
    assert.ok(tick >= T2M.min && tick <= T2M.max, `${tick} se sale de la escala`);
  }
});

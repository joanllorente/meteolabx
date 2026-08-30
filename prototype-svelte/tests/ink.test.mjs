/** Elección de tinta de la marca de agua según el fondo. */

import assert from 'node:assert/strict';
import test from 'node:test';

import { TINTA_CLARA, TINTA_OSCURA, colorDeFondo, mezclaSobre, tintaLegible } from '../src/lib/ink.js';

const sobre = (color) => tintaLegible([color]);

test('sobre fondo claro escribe en negro y sobre oscuro en blanco', () => {
  assert.equal(sobre([255, 255, 255]), TINTA_OSCURA);
  assert.equal(sobre([0, 0, 0]), TINTA_CLARA);
  // Los dos fondos del visor: el claro del contenedor y el oscuro del tema.
  assert.equal(sobre([213, 225, 230]), TINTA_OSCURA);
  assert.equal(sobre([11, 25, 38]), TINTA_CLARA);
});

test('la luminancia va ponderada, que si no el verde engaña', () => {
  // Un verde claro de campo de cizalladura y un azul del mismo valor medio:
  // sin ponderar salían los dos igual y el verde se rotulaba en blanco.
  const verde = [120, 205, 165];
  const azul = [40, 70, 160];
  assert.equal(Math.round((verde[0] + verde[1] + verde[2]) / 3), 163);
  assert.equal(Math.round((azul[0] + azul[1] + azul[2]) / 3), 90);
  assert.equal(sobre(verde), TINTA_OSCURA);
  assert.equal(sobre(azul), TINTA_CLARA);
});

test('manda la media de las muestras, no una sola', () => {
  // Mitad claro y mitad oscuro: decide el conjunto.
  assert.equal(tintaLegible([[255, 255, 255], [255, 255, 255], [0, 0, 0]]), TINTA_OSCURA);
  assert.equal(tintaLegible([[0, 0, 0], [0, 0, 0], [255, 255, 255]]), TINTA_CLARA);
  assert.equal(tintaLegible([]), null);
});

test('una celda translúcida se resuelve contra el fondo que tiene detrás', () => {
  const fondo = [213, 225, 230];
  assert.deepEqual(mezclaSobre([0, 0, 0, 0], fondo), fondo);
  assert.deepEqual(mezclaSobre([0, 0, 0, 1], fondo), [0, 0, 0]);
  const medio = mezclaSobre([0, 0, 0, 0.5], fondo);
  assert.deepEqual(medio.map(Math.round), [107, 113, 115]);
});

test('el color del contenedor se lee del valor calculado por el navegador', () => {
  assert.deepEqual(colorDeFondo('rgb(213, 225, 230)'), [213, 225, 230]);
  assert.deepEqual(colorDeFondo('rgba(11, 25, 38, 0.9)'), [11, 25, 38]);
  // Un contenedor sin fondo declarado no debe romper la medida.
  assert.deepEqual(colorDeFondo('transparent'), [255, 255, 255]);
  assert.deepEqual(colorDeFondo(null), [255, 255, 255]);
});

/**
 * La rosa de observación reparte cada rumbo por intensidades: lo flojo en el
 * centro de la cuña y lo fuerte fuera, con los límites redondos en la unidad
 * que haya elegido el usuario.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { observationModel } from '../src/lib/observation/model.js';

const station = { provider: 'AEMET', station_id: '0201X', name: 'Drassanes', tz: 'Europe/Madrid' };

function rose(vientos, rumbos, preferences) {
  const inicio = Date.UTC(2026, 8, 4, 22, 0, 0) / 1000;
  return observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17 },
      derivatives: {},
      daily_extremes: {},
      series: {
        epochs: vientos.map((_, index) => inicio + index * 600),
        winds: vientos,
        wind_dirs: rumbos
      }
    },
    station,
    'es',
    preferences
  ).rose;
}

const sur = 180;
const oeste = 270;

test('cada sector se reparte por intensidades y suma su frecuencia', () => {
  // Diez lecturas activas: seis del sur flojas o moderadas, cuatro del oeste fuertes.
  const r = rose(
    [5, 8, 12, 15, 25, 35, 45, 50, 65, 70],
    [sur, sur, sur, sur, sur, sur, oeste, oeste, oeste, oeste]
  );
  const s = r.data[8];
  const o = r.data[12];
  assert.equal(s.dir, 'S');
  assert.deepEqual(s.bands, [20, 20, 10, 10, 0, 0]);
  assert.equal(s.pct, 60);
  assert.deepEqual(o.bands, [0, 0, 0, 0, 20, 20]);
  assert.equal(o.pct, 40);
  // Los porcentajes siguen siendo sobre las lecturas con viento, como antes.
  assert.equal(r.stats.dominant, 'S');
  assert.equal(r.stats.frequency, '60 %');
});

test('la leyenda solo trae las bandas que han soplado', () => {
  const r = rose([5, 6, 7, 12, 14, 16], [sur, sur, sur, oeste, oeste, oeste]);
  assert.deepEqual(r.bands.map((band) => band.label), ['<10', '10–20']);
});

test('los límites son redondos en la unidad elegida', () => {
  // 20 km/h son 5,6 m/s: cae en 3–6; 25 km/h (6,9 m/s) ya en 6–9.
  const r = rose([5, 12, 20, 25, 40, 70], [sur, sur, sur, sur, sur, sur], { wind: 'ms' });
  assert.deepEqual(r.bands.map((band) => band.label), ['<3', '3–6', '6–9', '9–12', '≥17']);
  assert.deepEqual(r.data[8].bands.map((pct) => Math.round(pct * 6 / 100)), [1, 2, 1, 1, 0, 1]);
});

test('las calmas y los rumbos sin velocidad no entran en la rosa', () => {
  const r = rose(
    [0.5, 1, null, 12, 14, 16, 18, 22, 24],
    [sur, sur, sur, oeste, oeste, oeste, oeste, oeste, oeste]
  );
  // Ocho lecturas con velocidad, dos de ellas en calma.
  assert.equal(r.stats.samples, '8');
  assert.equal(r.stats.calm, '25 %');
  assert.equal(r.data[8].pct, 0);
  assert.equal(Math.round(r.data[12].pct), 100);
});

function roseWithGusts(vientos, rachas, rumbos) {
  const inicio = Date.UTC(2026, 8, 4, 22, 0, 0) / 1000;
  return observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17 },
      derivatives: {},
      daily_extremes: {},
      series: {
        epochs: vientos.map((_, index) => inicio + index * 600),
        winds: vientos,
        gusts: rachas,
        wind_dirs: rumbos
      }
    },
    station,
    'es'
  ).rose;
}

test('con rachas en cada lectura se ofrece también su rosa', () => {
  // Girona: medias por debajo de 10 y rachas que llegan a 15,5.
  const r = roseWithGusts(
    [4, 6, 7, 8, 9, 5],
    [8, 11, 12.5, 15.5, 14, 9],
    [sur, sur, sur, oeste, oeste, oeste]
  );
  assert.deepEqual(r.bands.map((band) => band.label), ['<10']);
  assert.ok(r.gust);
  assert.deepEqual(r.gust.bands.map((band) => band.label), ['<10', '10–20']);
  assert.equal(r.gust.stats.calmThreshold, r.stats.calmThreshold);
});

test('una racha que solo se informa a ratos, como en los METAR, no da rosa', () => {
  const r = roseWithGusts(
    [10, 12, 14, 16, 18, 20, 22, 24, 26, 28],
    [null, null, null, null, 30, null, 35, null, null, 40],
    [sur, sur, sur, sur, sur, oeste, oeste, oeste, oeste, oeste]
  );
  assert.ok(r);
  assert.equal(r.gust, null);
});

test('sin serie de rachas no hay rosa de rachas', () => {
  const r = roseWithGusts([10, 12, 14, 16, 18, 20], [], [sur, sur, sur, oeste, oeste, oeste]);
  assert.equal(r.gust, null);
});

/**
 * Con la estación en calma, la veleta no gira: se queda clavada donde sopló
 * por última vez y sigue publicando ese rumbo. La tarjeta ya lo omitía; la
 * gráfica del día lo pintaba igual, y salía una hilera de puntos alineados en
 * un rumbo que nadie había medido.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { observationModel } from '../src/lib/observation/model.js';

const station = { provider: 'AEMET', station_id: '0201X', name: 'Drassanes', tz: 'Europe/Madrid' };

/** Serie del día, una lectura cada diez minutos desde las 00:00 locales. */
function series(vientos, rumbos) {
  const inicio = Date.UTC(2026, 8, 4, 22, 0, 0) / 1000; // 00:00 del día 5 en Madrid
  return {
    epochs: vientos.map((_, index) => inicio + index * 600),
    winds: vientos,
    gusts: vientos.map((viento) => (viento === null ? null : viento + 2)),
    wind_dirs: rumbos
  };
}

function rumbosDibujados(vientos, rumbos) {
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17 },
      derivatives: {},
      daily_extremes: {},
      series: series(vientos, rumbos)
    },
    station,
    'es'
  );
  return model.charts.windDirection?.data[2].filter((value) => value !== null) ?? null;
}

test('los rumbos de las calmas no se dibujan', () => {
  // Medio km/h es el umbral de la tarjeta: por debajo, la veleta no arranca.
  const dibujados = rumbosDibujados([0, 0.2, 0.49, 3, 7, 12], [45, 45, 45, 210, 225, 230]);
  assert.deepEqual(dibujados, [210, 225, 230]);
});

test('con viento el rumbo se dibuja aunque sea flojo', () => {
  const dibujados = rumbosDibujados([0.5, 1.2, 2, 3, 4, 5], [10, 20, 30, 40, 50, 60]);
  assert.deepEqual(dibujados, [10, 20, 30, 40, 50, 60]);
});

test('un día entero en calma se queda sin serie de dirección', () => {
  // Sin un solo rumbo que enseñar, tampoco hay interruptor en la leyenda.
  assert.equal(rumbosDibujados([0, 0, 0, 0.1, 0, 0.3], [45, 45, 45, 45, 45, 45]), null);
});

test('la velocidad y la racha se conservan intactas', () => {
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 17 },
      derivatives: {},
      daily_extremes: {},
      series: series([0, 0.2, 3, 7, 12, 15], [45, 45, 210, 225, 230, 240])
    },
    station,
    'es'
  );
  const [vientos, rachas] = model.charts.windDirection.data;
  assert.deepEqual(vientos.filter((value) => value !== null), [0, 0.2, 3, 7, 12, 15]);
  assert.deepEqual(rachas.filter((value) => value !== null), [2, 2.2, 5, 9, 14, 17]);
});

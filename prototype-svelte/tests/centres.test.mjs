/** La medida de cierre de un centro de presión. */

import assert from 'node:assert/strict';
import test from 'node:test';

import { closureDepth } from '../src/lib/pressureCentres.js';

/**
 * Perfil en 1D metido en una banda, con paredes altas arriba y abajo para que
 * la inundación no se escape por el borde antes de tiempo.
 *
 *   980  990 …sube hasta 1000 en el collado… baja al mínimo de 992 y vuelve a subir
 *
 * Desde el 992 el agua tiene que remontar el collado de 1000 para encontrar el
 * 990 de la izquierda, así que el cierre es de 8 hPa.
 */
function banda() {
  const perfil = [
    980, 990, 995, 998, 1000, 999, 998, 997, 996, 993,
    992, 993, 996, 997, 998, 1000, 1002, 1003, 1004, 1005, 1006
  ];
  const ancho = perfil.length;
  const alto = 5;
  const campo = new Float32Array(ancho * alto);
  for (let fila = 0; fila < alto; fila += 1) {
    for (let columna = 0; columna < ancho; columna += 1) {
      campo[fila * ancho + columna] = fila === 0 || fila === alto - 1 ? 1010 : perfil[columna];
    }
  }
  return { campo, ancho, alto };
}

test('el cierre mide hasta dónde subió el agua, no el último paso', () => {
  // El nivel se asignaba con cada celda que salía del montón, y salen celdas
  // por debajo del frente: al destapar la bajada del collado el nivel caía y
  // la profundidad devuelta era la de ese último paso. Una borrasca con miles
  // de celdas cerradas se anunciaba con centésimas de hectopascal y se
  // descartaba por poco marcada.
  const { campo, ancho, alto } = banda();
  const cierre = closureDepth(campo, ancho, alto, { x: 10, y: 2 }, -1);
  assert.equal(cierre.open, false, 'debería cerrar contra terreno más hondo');
  assert.ok(
    Math.abs(cierre.depth - 8) < 1e-6,
    `cierre de ${cierre.depth.toFixed(2)} hPa en vez de los 8 del collado`
  );
});

test('un mínimo sin collado que remontar apenas cierra', () => {
  // Control: si el terreno baja nada más salir, el cierre es pequeño de verdad.
  const { campo, ancho, alto } = banda();
  const cierre = closureDepth(campo, ancho, alto, { x: 19, y: 2 }, 1);
  assert.ok(cierre.depth < 8, `cierre de ${cierre.depth.toFixed(2)} hPa`);
});

import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { pressureCentres } from '../src/lib/pressureCentres.js';

function forecast(hour, invert = false) {
  const base = new URL(`./fixtures/mslp/20260926-${hour}`, import.meta.url);
  const meta = JSON.parse(readFileSync(`${base.pathname}.json`, 'utf8'));
  const bytes = gunzipSync(readFileSync(`${base.pathname}.f32.gz`));
  const field = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  if (invert) field.forEach((value, i) => { field[i] = 2048 - value; });
  const cellKm = (meta.bounds[3] - meta.bounds[1]) / meta.height * 100;
  const centres = pressureCentres(field, {
    width: meta.width, height: meta.height, cellKm, block: Math.max(1, Math.round(10 / cellKm))
  }).map(centre => ({
    ...centre,
    lon: meta.bounds[0] + centre.x / meta.width * (meta.bounds[2] - meta.bounds[0]),
    lat: meta.bounds[3] - centre.y / meta.height * (meta.bounds[3] - meta.bounds[1])
  }));
  return { centres, field, meta };
}

const near = (centres, type, lon, lat) => centres.find(c =>
  c.type === type && Math.abs(c.lon - lon) < 1 && Math.abs(c.lat - lat) < 1);

test('26 a las 00: conserva Vizcaya y la baja al oeste de Cerdeña como relativos', () => {
  const { centres } = forecast('00');
  const high = near(centres, 'high', -3.7, 43.1);
  const low = near(centres, 'low', 6, 39.75);
  assert.ok(high);
  assert.ok(low);
  assert.equal(high.main, false);
  assert.equal(low.main, false);
  assert.equal(low.value.toFixed(1), '1017.2');
  assert.equal(centres.length, 3, 'descarta las pequeñas irregularidades');
});

test('26 a las 12: la isobara cerrada británica permite A aunque cierre menos de 3 hPa', () => {
  const { centres } = forecast('12');
  const high = near(centres, 'high', -3.2, 49.9);
  assert.ok(high);
  assert.ok(high.prominence < 3);
  assert.equal(high.main, true);
  const low = near(centres, 'low', 4.8, 47.6);
  assert.ok(low);
  assert.equal(low.main, false);
  assert.equal(low.value.toFixed(1), '1020.3');
  assert.equal(Math.round(low.value), 1020);
});

test('la clasificación es simétrica: una borrasca con el mismo cierre lleva B', () => {
  const { centres } = forecast('12', true);
  const low = near(centres, 'low', -3.2, 49.9);
  assert.ok(low);
  assert.equal(low.main, true);
  assert.equal(near(centres, 'high', 4.8, 47.6).main, false);
});

test('todas las etiquetas coinciden con la celda que lee el cursor', () => {
  for (const hour of ['00', '12']) {
    const { centres, field, meta } = forecast(hour);
    for (const centre of centres) {
      assert.equal(centre.value, field[Math.floor(centre.y) * meta.width + Math.floor(centre.x)]);
    }
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { frameGeo, lcc, projectFrame, projectionForBox } from '../src/lib/projection.js';
import { troughAxesLonLat } from '../src/lib/troughs.js';

function marco(width, height, bounds, valor) {
  const values = new Float32Array(width * height);
  for (let fila = 0; fila < height; fila += 1) {
    for (let col = 0; col < width; col += 1) values[fila * width + col] = valor(col, fila);
  }
  return { width, height, bounds, values, u: null, v: null, overlay: null };
}

const AROME = [-12.0125, 37.4875, 16.0125, 55.4125];

test('una zona de AROME pegada al borde queda entera dentro de sus datos', () => {
  const zona = projectionForBox([-10, 37.5, 4.6, 44.2], { dataBounds: AROME, cellKm: 2.5 });
  const proy = lcc(zona);
  const [xmin, ymin, xmax, ymax] = zona.rect;
  for (let i = 0; i <= 20; i += 1) {
    for (const [x, y] of [
      [xmin + (xmax - xmin) * i / 20, ymin], [xmin + (xmax - xmin) * i / 20, ymax],
      [xmin, ymin + (ymax - ymin) * i / 20], [xmax, ymin + (ymax - ymin) * i / 20]
    ]) {
      const [lon, lat] = proy.inverse(x, y);
      assert.ok(lon >= AROME[0] && lon <= AROME[2] && lat >= AROME[1] && lat <= AROME[3], `${lon} ${lat}`);
    }
  }
  // Y apenas pierde nada: el borde sur solo sube lo justo.
  assert.ok(proy.inverse((xmin + xmax) / 2, ymin)[1] < 37.8);
});

test('una zona lee del frame completo el dato de cada punto', () => {
  // AROME simulado a 0,1°, con un valor que codifica la posición.
  const width = 280, height = 179;
  const frame = marco(width, height, [-12, 37.5, 16, 55.4], (col, fila) => {
    const lon = -12 + (col + .5) * 0.1;
    const lat = 55.4 - (fila + .5) * 0.1;
    return lon * 100 + lat;
  });
  frame.product = 'precip-type';
  const proyeccion = projectionForBox([5.8, 45.6, 16, 48.9], { dataBounds: frame.bounds, cellKm: 2.5 });
  const zona = projectFrame(frame, proyeccion, { nearest: true });
  assert.equal(zona.cellKm, 2.5);
  const geo = frameGeo(zona);
  const [lon, lat] = geo.toGeo(40.5, 30.5);
  const valor = zona.values[30 * zona.width + 40];
  // Con la celda más cercana, el valor es exactamente el de una celda de origen.
  const col = Math.round((lon + 12) / 0.1 - 0.5);
  const fila = Math.round((55.4 - lat) / 0.1 - 0.5);
  assert.equal(valor, frame.values[fila * width + col]);
  // La misma zona se reutiliza en vez de volver a proyectar.
  assert.equal(projectFrame(frame, proyeccion, { nearest: true }), zona);
});

test('el hemisferio sur busca las vaguadas en espejo y las devuelve a su sitio', () => {
  // Geopotencial con una vaguada que se descuelga hacia el ecuador. En el
  // norte baja hacia el sur; su espejo en el sur sube hacia el norte.
  const width = 240, height = 160;
  const norte = marco(width, height, [-30, 30, 30, 70], (col, fila) => {
    const lon = -30 + (col + .5) * 60 / width;
    const lat = 70 - (fila + .5) * 40 / height;
    // Geopotencial en dam que baja hacia el polo, con una vaguada en 0°.
    return 590 - 1.2 * (lat - 30) - 18 * Math.exp(-((lon / 8) ** 2));
  }).values;
  const sur = new Float32Array(width * height);
  for (let fila = 0; fila < height; fila += 1) {
    sur.set(norte.subarray((height - 1 - fila) * width, (height - fila) * width), fila * width);
  }
  const enNorte = troughAxesLonLat(norte, { width, height, bounds: [-30, 30, 30, 70], contourStep: 6 });
  const enSur = troughAxesLonLat(sur, { width, height, bounds: [-30, -70, 30, -30], contourStep: 6 });
  assert.ok(enNorte.axes.length > 0, 'la vaguada de prueba tiene que detectarse');
  assert.equal(enSur.axes.length, enNorte.axes.length);
  enNorte.axes.forEach((eje, i) => eje.forEach((punto, j) => {
    assert.ok(Math.abs(enSur.axes[i][j].x - punto.x) < 1e-6);
    assert.ok(Math.abs(enSur.axes[i][j].y - (height - punto.y)) < 1e-6);
  }));
});

test('una zona fuera de los datos no tiene proyección propia', () => {
  // Islas Británicas con datos solo de Cataluña: no hay nada en común.
  assert.equal(projectionForBox([-11, 49.8, 2.1, 55.4], { dataBounds: [0.1, 40.5, 3.4, 42.9], cellKm: 2.5 }), null);
});

test('AROME entero se pinta en el rectángulo con dato, y una zona del borde no se aplasta', async () => {
  const { aromeProjection, AROME_LCC } = await import('../src/lib/projection.js');
  const completo = [-12.0125, 37.4875, 16.0125, 55.4125];
  assert.equal(aromeProjection(completo), AROME_LCC);
  // Suiza y Austria pasa del borde este: se mueve ese lado, no los otros.
  const alpes = aromeProjection(completo, [5.8, 45.6, 16, 48.9]);
  const [xmin, ymin, xmax, ymax] = alpes.rect;
  assert.ok((ymax - ymin) > 300, `alto ${ymax - ymin} km`);
  assert.ok((xmax - xmin) / (ymax - ymin) < 2.5);
  // Un frame recortado usa su propio recuadro.
  assert.notEqual(aromeProjection([0.1, 40.5, 3.4, 42.9]), AROME_LCC);
});

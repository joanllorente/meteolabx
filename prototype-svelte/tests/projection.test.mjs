import test from 'node:test';
import assert from 'node:assert/strict';
import { lcc, projectFrame, frameGeo, PROJECTED_CELL_KM } from '../src/lib/projection.js';

const EUROPA = { lon0: 12, lat0: 52, lat1: 35, lat2: 65, width_km: 1200, height_km: 800 };
const SUR = { lon0: -62, lat0: -35, lat1: -20, lat2: -50, width_km: 1200, height_km: 800 };

test('directa e inversa se deshacen en los dos hemisferios', () => {
  for (const p of [EUROPA, SUR]) {
    const proy = lcc(p);
    for (const [lon, lat] of [[p.lon0, p.lat0], [p.lon0 - 30, p.lat0 - 10], [p.lon0 + 25, p.lat0 + 8]]) {
      const [x, y] = proy.forward(lon, lat);
      const [lon2, lat2] = proy.inverse(x, y);
      assert.ok(Math.abs(lon2 - lon) < 1e-9 && Math.abs(lat2 - lat) < 1e-9);
    }
  }
});

// Frame de latitud y longitud de 0,25° que cubre de sobra el rectángulo.
function frameLatLon(p, campo) {
  const bounds = [p.lon0 - 15, p.lat0 - 8, p.lon0 + 15, p.lat0 + 8];
  const width = 120, height = 64;
  const values = new Float32Array(width * height);
  const u = new Float32Array(width * height);
  const v = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const lon = bounds[0] + (x + 0.5) * 0.25;
      const lat = bounds[3] - (y + 0.5) * 0.25;
      values[y * width + x] = campo(lon, lat);
      u[y * width + x] = 3;
      v[y * width + x] = 4;
    }
  }
  return { bounds, width, height, values, u, v, projection: p };
}

test('el mapa reproyectado lee en cada celda el dato de su latitud y longitud', () => {
  for (const p of [EUROPA, SUR]) {
    const campo = (lon, lat) => lon * 10 + lat;
    const frame = projectFrame(frameLatLon(p, campo));
    assert.equal(frame.width, Math.round(p.width_km / PROJECTED_CELL_KM));
    assert.equal(frame.cellKm, PROJECTED_CELL_KM);
    const geo = frameGeo(frame);
    for (const [col, row] of [[5, 5], [30, 20], [55, 35]]) {
      const [lon, lat] = geo.toGeo(col + 0.5, row + 0.5);
      assert.ok(Math.abs(frame.values[row * frame.width + col] - campo(lon, lat)) < 1e-2);
      const [x, y] = geo.toGrid(lon, lat);
      assert.ok(Math.abs(x - col - 0.5) < 1e-6 && Math.abs(y - row - 0.5) < 1e-6);
    }
  }
});

test('el viento se gira al eje de la rejilla siguiendo los meridianos', () => {
  for (const p of [EUROPA, SUR]) {
    const frame = projectFrame(frameLatLon(p, () => 0));
    const proy = lcc(p);
    const geo = frameGeo(frame);
    // Una celda lejos del meridiano central, donde el giro se nota.
    const col = 3, row = 10;
    const [lon, lat] = geo.toGeo(col + 0.5, row + 0.5);
    const h = 1e-4;
    const [x0, y0] = proy.forward(lon, lat);
    const [xe, ye] = proy.forward(lon + h, lat);
    const [xn, yn] = proy.forward(lon, lat + h);
    const este = [(xe - x0), (ye - y0)].map((c, _, a) => c / Math.hypot(...a));
    const norte = [(xn - x0), (yn - y0)].map((c, _, a) => c / Math.hypot(...a));
    const esperado = [3 * este[0] + 4 * norte[0], 3 * este[1] + 4 * norte[1]];
    const i = row * frame.width + col;
    assert.ok(Math.abs(frame.u[i] - esperado[0]) < 1e-3, `${frame.u[i]} ${esperado[0]}`);
    assert.ok(Math.abs(frame.v[i] - esperado[1]) < 1e-3, `${frame.v[i]} ${esperado[1]}`);
    assert.ok(Math.abs(Math.hypot(frame.u[i], frame.v[i]) - 5) < 1e-3);
  }
});

test('un frame sin proyección no cambia', () => {
  const frame = { bounds: [0, 0, 1, 1], width: 2, height: 2, values: new Float32Array(4) };
  assert.equal(projectFrame(frame), frame);
});

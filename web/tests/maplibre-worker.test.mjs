/**
 * MapLibre 6 no deja que Rollup empaquete su worker: lo pide al arrancar como
 * `./maplibre-gl-worker.mjs` al lado del módulo, resuelto con
 * `import.meta.url`. Como Rollup no ve ese import, en producción la petición
 * moría en un 404 dentro de `_app/immutable/chunks/` y el mapa se quedaba sin
 * worker —lienzo en blanco en Chrome, «Cargando el mapa…» eterno en Safari—.
 *
 * La copia en `static/maplibre/` y el `setWorkerUrl` incondicional son lo que
 * lo sostiene, y las dos cosas son fáciles de deshacer sin querer.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (path) => readFileSync(join(root, path), 'utf8');

test('el build copia el worker antes de empaquetar', () => {
  const { scripts } = JSON.parse(read('package.json'));
  assert.match(scripts.build, /maplibre:worker/);
  // Antes de `vite build`, no después: `vite build` es quien vuelca `static/`
  // en `build/client`, así que copiarlo luego lo dejaría fuera de la imagen.
  assert.ok(scripts.build.indexOf('maplibre:worker') < scripts.build.indexOf('vite build'));
  assert.match(scripts.dev, /maplibre:worker/);
});

test('el script deja el worker y su módulo compartido en static', () => {
  const destino = join(root, 'static', 'maplibre');
  rmSync(destino, { recursive: true, force: true });
  execFileSync(process.execPath, [join(root, 'scripts', 'copy-maplibre-worker.mjs')]);

  for (const fichero of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
    assert.ok(existsSync(join(destino, fichero)), `falta ${fichero}`);
  }
  // El worker importa el compartido por ruta relativa: uno sin el otro no
  // arranca.
  assert.match(read('static/maplibre/maplibre-gl-worker.mjs'), /\.\/maplibre-gl-shared\.mjs/);
});

test('el mapa apunta al worker de static también en producción', () => {
  const fuente = read('src/lib/components/StationMap.svelte');
  const llamada = fuente.match(/^.*setWorkerUrl\(.*$/m);
  assert.ok(llamada, 'el mapa ya no llama a setWorkerUrl');
  assert.match(llamada[0], /'\/maplibre\/maplibre-gl-worker\.mjs'/);
  assert.doesNotMatch(fuente, /import\.meta\.env\.DEV[\s\S]{0,80}setWorkerUrl/);
});

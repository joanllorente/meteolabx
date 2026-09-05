/**
 * Copia el worker de MapLibre a `static/`. Hace falta en las dos mitades.
 *
 * En desarrollo, porque Vite, al servir un módulo desde `node_modules`, le
 * inyecta `import ... from "/@vite/client"`. Dentro de un Web Worker eso
 * revienta —no hay `document`— y el worker muere en silencio: la fuente de
 * teselas no carga nunca, el estilo no llega a «loaded» y el mapa se queda
 * negro sin dar un solo error.
 *
 * En producción, porque MapLibre 6 tampoco deja que lo empaquete Rollup: la
 * URL del worker la calcula al arrancar con `import.meta.url`, buscando
 * `./maplibre-gl-worker.mjs` al lado del módulo. Rollup no ve ese import, no
 * emite el fichero, y la petición muere en un 404 dentro de
 * `_app/immutable/chunks/`.
 *
 * Desde `static/` el fichero se sirve tal cual, sin pasar por la tubería de
 * transformación, y `vite build` lo copia a `build/client/`. Por eso este
 * script va enganchado tanto a `npm run dev` como a `npm run build`: si se
 * ejecuta después del build, el worker no llega a la imagen.
 */
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
const from = join(root, 'node_modules', 'maplibre-gl', 'dist');
const to = join(root, 'static', 'maplibre');

mkdirSync(to, { recursive: true });
for (const file of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
  copyFileSync(join(from, file), join(to, file));
}
console.log(`[maplibre] worker copiado a ${to}`);

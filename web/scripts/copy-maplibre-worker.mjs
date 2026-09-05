/**
 * Copia el worker de MapLibre a `static/` para poder usarlo en desarrollo.
 *
 * Vite, al servir un módulo desde `node_modules`, le inyecta
 * `import ... from "/@vite/client"`. Dentro de un Web Worker eso revienta —no
 * hay `document`— y el worker muere en silencio: la fuente de teselas no
 * carga nunca, el estilo no llega a «loaded» y el mapa se queda negro sin dar
 * un solo error.
 *
 * Desde `static/` el fichero se sirve tal cual, sin pasar por la tubería de
 * transformación. En producción no hace falta: Rollup empaqueta el worker
 * correctamente, así que el `setWorkerUrl` solo se aplica en desarrollo.
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

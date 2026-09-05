// El arranque de producción no pasa por Vite: `server.js` se ejecuta tal cual
// dentro de la imagen, y esa imagen no lleva `src/` entero, solo los módulos
// que se copian a mano. Un import nuevo desde `src/` funciona en local —donde
// el árbol está completo— y tumba el servicio al desplegar, con un
// ERR_MODULE_NOT_FOUND que ocurre antes de escuchar y sin más rastro que el
// contenedor reiniciándose. Ya ha pasado dos veces: con la frontera de rutas
// y con el agente del proxy.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Rutas relativas que `server.js` importa, resueltas desde la raíz de `web/`. */
function relativeImports() {
  const source = readFileSync(join(web, 'server.js'), 'utf8');
  return [...source.matchAll(/from\s+'(\.[^']+)'/g)]
    .map((match) => normalize(match[1].replace(/^\.\//, '')))
    .filter((path) => !path.startsWith('build/'));
}

/**
 * Lo que la etapa de runtime deja dentro de la imagen. Solo cuentan las COPY
 * posteriores al `FROM ... AS runtime`: las de la etapa de build viven en un
 * contenedor que se descarta.
 */
function copiedIntoRuntime() {
  const dockerfile = readFileSync(join(web, 'Dockerfile'), 'utf8');
  const runtime = dockerfile.slice(dockerfile.lastIndexOf('AS runtime'));
  return [...runtime.matchAll(/^COPY\s+(?!--from)(\S+)\s+(\S+)$/gm)].map(([, source]) =>
    normalize(source)
  );
}

test('la imagen lleva cada módulo que server.js importa de src/', () => {
  const copied = copiedIntoRuntime();
  for (const path of relativeImports()) {
    // Vale el fichero exacto o el directorio que lo contiene: el Dockerfile
    // copia `src/lib/seo` entero, no cada uno de sus módulos.
    const covered = copied.some((entry) => path === entry || path.startsWith(entry + '/'));
    assert.ok(
      covered,
      `server.js importa '${path}' y el Dockerfile no lo copia a la imagen de runtime. ` +
        `Añade una línea COPY tras 'AS runtime' o el servicio arrancará con ERR_MODULE_NOT_FOUND.`
    );
  }
});

test('el propio server.js viaja a la imagen', () => {
  assert.ok(copiedIntoRuntime().includes('server.js'));
});

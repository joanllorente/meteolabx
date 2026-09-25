import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Las páginas que el CDN comparte no pueden llevar datos de quien las pidió.
 *
 * Una ficha de observación se guarda en el borde de Cloudflare y se sirve tal
 * cual al resto de visitantes. Si su `load` mira la cookie o las cabeceras del
 * visitante y mete el resultado en el payload, ese dato queda congelado: todos
 * los que abran la ficha durante la vida de la copia reportan la decisión del
 * primero que pasó por allí.
 *
 * Pasó de verdad. `languageDecision` viajaba en el HTML y alimentaba
 * `recordVisit`, así que al activar la caché las estadísticas de idioma
 * empezaron a contar una sola muestra repetida. El navegador ya manda sus
 * propios idiomas por separado (`visitLanguageContext`), que es donde ese dato
 * es correcto y no depende de a quién le tocara llenar la caché.
 *
 * Esta comprobación es estática a propósito: no ejercita el `load`, lee el
 * fichero. Lo que vigila no es un valor concreto sino la forma del módulo, y
 * así falla en el momento en que alguien vuelve a pedir `cookies` o `request`
 * en una página que se declara compartible.
 */

const RAIZ = new URL('../src/routes/', import.meta.url).pathname;

/** Las mismas rutas que la regla de caché de Cloudflare marca compartibles. */
const COMPARTIBLE = /(^|\/)(observation|trends)\//;

async function loadsDePagina(directorio, relativo = '') {
  const encontrados = [];
  for (const entrada of await readdir(directorio, { withFileTypes: true })) {
    const ruta = join(directorio, entrada.name);
    const camino = relativo ? `${relativo}/${entrada.name}` : entrada.name;
    if (entrada.isDirectory()) encontrados.push(...(await loadsDePagina(ruta, camino)));
    else if (entrada.name === '+page.server.js') encontrados.push({ ruta, camino: relativo });
  }
  return encontrados;
}

/** El paréntesis de `export async function load(...)`, con su desestructuración. */
function firmaDelLoad(fuente) {
  const inicio = fuente.match(/export\s+(?:async\s+)?function\s+load\s*\(/);
  if (!inicio) return '';
  const desde = inicio.index + inicio[0].length;
  let profundidad = 1;
  for (let i = desde; i < fuente.length; i += 1) {
    if (fuente[i] === '(' || fuente[i] === '{') profundidad += 1;
    else if (fuente[i] === ')' || fuente[i] === '}') {
      profundidad -= 1;
      if (profundidad === 0) return fuente.slice(desde, i);
    }
  }
  return '';
}

test('ninguna página compartida en el CDN lee datos del visitante', async () => {
  const paginas = await loadsDePagina(RAIZ);
  const compartibles = paginas.filter(({ camino }) => COMPARTIBLE.test(`/${camino}/`));

  // Si la ruta se reorganiza y el filtro deja de encontrar nada, esta prueba
  // pasaría vacía sin vigilar ya nada.
  assert.ok(compartibles.length >= 4, `se esperaban varias rutas compartibles, hay ${compartibles.length}`);

  const culpables = [];
  for (const { ruta, camino } of compartibles) {
    const fuente = readFileSync(ruta, 'utf8');
    // Solo cuentan las que piden `public`: las de red personal conviven bajo
    // la misma ruta y declaran `private, no-store`, así que el CDN las deja
    // pasar y pueden mirar lo que necesiten.
    // `liveCacheControl(ok, 'public, …')` también cuenta: es `public` cuando
    // la consulta sale bien, que es justo cuando se comparte.
    if (!/['"]cache-control['"]\s*:\s*(['"]public|liveCacheControl\()/.test(fuente)) continue;
    const firma = firmaDelLoad(fuente);
    for (const campo of ['cookies', 'request']) {
      if (new RegExp(`\\b${campo}\\b`).test(firma)) culpables.push(`${camino} recibe \`${campo}\``);
    }
    // `locals.crawler` sí se permite: la versión del buscador lleva la lectura
    // guardada en vez de la actual, y por eso esa rama tiene que declararse
    // `no-store` ella misma, además de lo que ya fuerza `hooks.server.js`.
    if (/\blocals\b/.test(firma) && !/crawler\s*\?\s*NO_STORE/.test(fuente)) {
      culpables.push(`${camino} lee \`locals\` sin declarar no-store para el rastreador`);
    }
    if (/from\s+['"]\$lib\/server\/language\.js['"]/.test(fuente)) {
      culpables.push(`${camino} importa el negociador de idioma`);
    }
  }

  assert.deepEqual(culpables, [], `páginas compartidas que dependen del visitante:\n${culpables.join('\n')}`);
});

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';

const LIB = resolvePath(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'lib');

export function resolve(specifier, context, nextResolve) {
  // Las páginas que hablan con la API leen su URL de aquí; sin esto no se
  // podía probar ningún `load` de servidor.
  if (specifier === '$env/dynamic/private') {
    return nextResolve(
      pathToFileURL(resolvePath(LIB, '..', '..', 'tests', 'fixtures', 'env-dynamic-private.mjs')).href,
      context
    );
  }
  if (specifier.startsWith('$lib/')) {
    return nextResolve(pathToFileURL(resolvePath(LIB, specifier.slice(5))).href, context);
  }
  return nextResolve(specifier, context);
}

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve as resolvePath } from 'node:path';

const LIB = resolvePath(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'lib');

export function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('$lib/')) {
    return nextResolve(pathToFileURL(resolvePath(LIB, specifier.slice(5))).href, context);
  }
  return nextResolve(specifier, context);
}

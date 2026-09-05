/**
 * `$lib` fuera de Vite.
 *
 * Los tests corren con `node --test`, sin bundler, así que cualquier módulo
 * que importe `$lib/...` —el alias que resuelve SvelteKit— no se podía probar.
 * Este enlace lo resuelve igual que Vite: a `src/lib`.
 */
import { register } from 'node:module';
import { pathToFileURL } from 'node:url';

register(new URL('./alias-hook.mjs', import.meta.url), pathToFileURL('./'));

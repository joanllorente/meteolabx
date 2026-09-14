import { error } from '@sveltejs/kit';
import { dev } from '$app/environment';

import { demoWarnings } from '$lib/warnings/warnings.js';

/**
 * Avisos a partir de AROME, en construcción.
 *
 * Solo existe con `vite dev`: en producción la ruta contesta 404 aunque
 * alguien teclee la dirección, y la barra no enseña la pestaña. Mientras no
 * haya backend se sirven avisos de demostración con la forma que tendrá la
 * respuesta de la API.
 */
export function load({ params, setHeaders }) {
  if (!dev) error(404, 'Not found');

  setHeaders({ 'cache-control': 'no-store' });
  return { lang: params.lang, ...demoWarnings(), demo: true };
}

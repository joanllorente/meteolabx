import { redirect } from '@sveltejs/kit';

import { parseLegacyStationPath } from '$lib/seo/ownership.js';
import { observationPath } from '$lib/seo/station.js';

/**
 * Las fichas estáticas antiguas redirigen a su equivalente nueva.
 *
 * `/es/estaciones/aemet/barcelona-drassanes-0201x.html`
 *     → 301 → `/es/observation/barcelona-drassanes-0201x`
 *
 * El slug es el mismo en las dos: lo calcula `utils/station_url.py`, así que
 * la traducción es directa y no hace falta consultar el catálogo. Un 301
 * (permanente) traslada el posicionamiento a la URL nueva; un 302 lo dejaría
 * en el aire.
 */
export async function handle({ event, resolve }) {
  const legacy = parseLegacyStationPath(event.url.pathname);
  if (legacy) {
    const target = new URL(observationPath(legacy.language, legacy.slug), event.url.origin);
    target.search = event.url.search;
    redirect(301, target.pathname + target.search);
  }
  return resolve(event);
}

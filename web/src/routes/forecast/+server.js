/**
 * `/forecast` redirige a `/{idioma}/forecast`. En producción lo hace antes
 * `server.js` —el servidor de estáticos contestaría primero—; esta ruta cubre
 * `vite dev`. Ver `legacyForecastLocation`.
 */
import { redirect } from '@sveltejs/kit';

import { legacyForecastLocation } from '$lib/seo/ownership.js';

export function GET({ url }) {
  redirect(301, legacyForecastLocation('/forecast', url.search));
}

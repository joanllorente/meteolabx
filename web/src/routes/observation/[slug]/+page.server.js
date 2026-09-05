import { redirect } from '@sveltejs/kit';

import { DEFAULT_LANGUAGE } from '$lib/seo/i18n.js';
import { observationPath } from '$lib/seo/station.js';

/**
 * `/observation/{slug}` sin idioma es un atajo cómodo, no una URL publicable:
 * manda al castellano, que es el `x-default` del sitio. Si la estación no se
 * publica en castellano, la propia ficha vuelve a redirigir al idioma que le
 * toque.
 */
export function load({ params }) {
  redirect(301, observationPath(DEFAULT_LANGUAGE, params.slug));
}

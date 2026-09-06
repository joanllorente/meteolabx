import { redirect } from '@sveltejs/kit';

import { LANGUAGES } from '$lib/seo/i18n.js';
import { visitorLanguage } from '$lib/server/language.js';
import { observationPath } from '$lib/seo/station.js';

/**
 * `/observation/{slug}` sin idioma es un atajo cómodo, no una URL publicable:
 * elige el idioma del navegador. La redirección no se debe guardar porque
 * depende de las preferencias de cada visitante.
 */
export function load({ params, request, cookies, setHeaders }) {
  const language = visitorLanguage({ request, cookies }, Object.keys(LANGUAGES));
  setHeaders({ 'cache-control': 'private, no-store', vary: 'Accept-Language' });
  redirect(302, observationPath(language, params.slug));
}

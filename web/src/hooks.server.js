import { redirect } from '@sveltejs/kit';

import { parseLegacyStationPath } from '$lib/seo/ownership.js';
import { observationPath } from '$lib/seo/station.js';
import { LANGUAGE_CODES } from '$lib/seo/i18n.js';
import { LANGUAGE_COOKIE, visitorLanguage } from '$lib/server/language.js';

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
  const pathLanguage = event.url.pathname.split('/')[1];
  const localized = LANGUAGE_CODES.includes(pathLanguage);
  // El selector hace una navegación completa: no se precarga una petición
  // que guarda preferencias ni se reutilizan datos del idioma anterior.
  if (localized && event.url.searchParams.get('set_language') === pathLanguage) {
    const cookieOptions = {
      path: '/', maxAge: 60 * 60 * 24 * 365, sameSite: 'lax', httpOnly: true,
      secure: event.url.protocol === 'https:'
    };
    event.cookies.set(LANGUAGE_COOKIE, pathLanguage, cookieOptions);
    const target = new URL(event.url);
    target.searchParams.delete('set_language');
    const response = languageRedirect(target);
    response.headers.append('set-cookie', event.cookies.serialize(LANGUAGE_COOKIE, pathLanguage, cookieOptions));
    return response;
  }
  if (localized) {
    const language = visitorLanguage(event, LANGUAGE_CODES, pathLanguage);
    if (language !== pathLanguage) {
      const target = new URL(event.url);
      const legacyPath = parseLegacyStationPath(target.pathname);
      target.pathname = legacyPath
        ? observationPath(language, legacyPath.slug)
        : target.pathname.replace(/^\/[^/]+/, `/${language}`);
      return languageRedirect(target);
    }
  }
  const legacy = parseLegacyStationPath(event.url.pathname);
  if (legacy) {
    const target = new URL(observationPath(legacy.language, legacy.slug), event.url.origin);
    target.search = event.url.search;
    redirect(301, target.pathname + target.search);
  }
  const response = await resolve(event);
  if (localized || event.url.pathname === '/') {
    // Estas respuestas incluyen decisiones personales, también en las
    // navegaciones de SvelteKit. Nunca compartirlas entre visitantes.
    response.headers.set('cache-control', 'private, no-store');
    const vary = response.headers.get('vary');
    response.headers.set('vary', [vary, 'Cookie', 'Accept-Language'].filter(Boolean).join(', '));
  }
  return response;
}

function languageRedirect(target) {
  // Una redirección lanzada desde handle no incorpora event.setHeaders.
  return new Response(null, { status: 302, headers: {
    location: target.pathname + target.search,
    'cache-control': 'private, no-store',
    vary: 'Cookie, Accept-Language'
  } });
}

import { redirect } from '@sveltejs/kit';

import { parseLegacyStationPath } from '$lib/seo/ownership.js';
import { observationPath } from '$lib/seo/station.js';
import { LANGUAGE_CODES } from '$lib/seo/i18n.js';
import { LANGUAGE_COOKIE, visitorLanguage } from '$lib/server/language.js';
import { matchesEtag } from '$lib/server/etag.js';

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
    //
    // La excepción son las fichas de observación, que piden `public` a
    // propósito: son el grueso de lo que rastrea Google y no tiene sentido
    // prohibir que se guarden. Lo personal que llevan —la decisión de idioma—
    // queda cubierto por el `Vary`, que separa la copia de cada combinación de
    // cookie e idioma en lugar de mezclarlas.
    if (!isPubliclyCacheable(response)) response.headers.set('cache-control', 'private, no-store');
    const vary = response.headers.get('vary');
    response.headers.set('vary', [vary, 'Cookie', 'Accept-Language'].filter(Boolean).join(', '));
  }
  return notModified(event, response) || response;
}

/** Solo la ruta que sirve la página sabe si su contenido es compartible. */
function isPubliclyCacheable(response) {
  return (response.headers.get('cache-control') || '').includes('public');
}

/**
 * GET condicional: si el cliente ya tiene esta versión, se le ahorra el HTML.
 *
 * Es lo que convierte las revisitas de Googlebot a fichas que no han cambiado
 * en una respuesta vacía. Las cabeceras que describen la copia viajan igual en
 * el 304 —sin ellas el cliente no sabría cuánto vale ni bajo qué condiciones.
 */
function notModified(event, response) {
  if (event.request.method !== 'GET' && event.request.method !== 'HEAD') return null;
  if (response.status !== 200) return null;
  const etag = response.headers.get('etag');
  if (!matchesEtag(event.request.headers.get('if-none-match'), etag)) return null;
  const headers = new Headers();
  for (const name of ['cache-control', 'etag', 'vary', 'content-language']) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(null, { status: 304, headers });
}

function languageRedirect(target) {
  // Una redirección lanzada desde handle no incorpora event.setHeaders.
  return new Response(null, { status: 302, headers: {
    location: target.pathname + target.search,
    'cache-control': 'private, no-store',
    vary: 'Cookie, Accept-Language'
  } });
}

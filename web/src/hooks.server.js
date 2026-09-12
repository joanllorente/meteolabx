import { redirect } from '@sveltejs/kit';

import { parseLegacyStationPath } from '$lib/seo/ownership.js';
import { observationPath } from '$lib/seo/station.js';
import { LANGUAGE_CODES } from '$lib/seo/i18n.js';
import { LANGUAGE_COOKIE } from '$lib/server/language.js';
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
  // Una URL que ya lleva idioma se sirve EN ESE IDIOMA, sin mirar la cookie.
  //
  // Antes se renegociaba aquí: `/it/observation/tivissa` devolvía la página a
  // quien tuviera italiano y una redirección a `/es/...` a quien tuviera
  // español. Eso hacía que la misma dirección respondiera cosas distintas
  // según el visitante, y obligaba a marcarla `Vary: Cookie` —que es lo que
  // impedía a Cloudflare compartir una copia entre visitantes y mandaba cada
  // petición al origen.
  //
  // La negociación sigue existiendo, pero solo en `/`, que es donde alguien
  // llega sin haber elegido idioma. Un enlace compartido conserva el suyo, que
  // además es lo que espera quien lo recibe.
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
    const compartible = isPubliclyCacheable(event, response);
    if (!compartible) response.headers.set('cache-control', 'private, no-store');

    // `Vary: Cookie` vuelve la respuesta incacheable en el CDN: Cloudflare
    // solo respeta `Vary: Accept-Encoding` y ante cualquier otro valor manda
    // la petición al origen. Con él puesto, el `public, max-age` de las fichas
    // no ahorraba una sola petición, y el HTML de observación es el 83 % de la
    // salida de datos que factura Railway.
    //
    // Se puede quitar de lo compartible porque estas páginas ya no dependen de
    // quién pide: el idioma va en la URL y la negociación vive solo en `/`.
    // Las demás lo conservan, que ahí sí separa la copia de cada visitante.
    const vary = response.headers.get('vary');
    const separadores = compartible ? ['Accept-Language'] : ['Cookie', 'Accept-Language'];
    response.headers.set('vary', [vary, ...separadores].filter(Boolean).join(', '));

    if (compartible) response.headers.set('cache-control', sharedCacheControl(response));
  }
  return notModified(event, response) || response;
}

/**
 * TTL para el CDN, conservando el del navegador.
 *
 * `s-maxage` habla solo con las cachés compartidas: el navegador sigue con su
 * `max-age`, y Cloudflare guarda la copia el tiempo que aquí se diga. Cinco
 * minutos son suficientes —las estaciones publican cada 10-60— y
 * `stale-while-revalidate` evita que la caducidad se note: se sirve la copia
 * vieja mientras se pide la nueva por detrás.
 */
function sharedCacheControl(response) {
  const actual = response.headers.get('cache-control') || 'public';
  if (actual.includes('s-maxage')) return actual;
  return `${actual}, s-maxage=300`;
}


/**
 * Qué páginas se comparten entre visitantes.
 *
 * Fichas de observación y tendencias: las dos son lecturas de una estación,
 * iguales para todo el mundo, y son el grueso de lo que rastrean los
 * buscadores. Tendencias declaraba `public` desde su `load` pero acababa
 * aplastada aquí, así que iba al origen en cada visita.
 *
 * La condición sigue siendo estrecha: hace falta que la ruta sea una de esas
 * dos *y* que haya pedido `public` a propósito —las redes con credencial
 * personal están bajo la misma ruta y piden `no-store`—. Las demás páginas
 * localizadas llevan dentro búsquedas y filtros del visitante y se siguen
 * aplastando.
 */
const RUTAS_COMPARTIBLES = /^\/[^/]+\/(observation|trends)\//;

function isPubliclyCacheable(event, response) {
  if (!RUTAS_COMPARTIBLES.test(event.url.pathname)) return false;
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

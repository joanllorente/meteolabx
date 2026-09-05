/**
 * Qué URLs sirve ya el frontend nuevo y cuáles siguen siendo de la app vieja.
 *
 * Durante la migración conviven dos frontends. Este módulo es la frontera, y
 * lo consultan los dos lados: `server.js` para decidir si una petición va al
 * proxy, y `hooks.server.js` para redirigir las fichas `.html` antiguas.
 *
 * El criterio es explícito a propósito. Dejar que SvelteKit conteste 404 a lo
 * que no reconoce se llevaría por delante el mapa, el ranking y el histórico,
 * que siguen viviendo en Streamlit.
 */
import { LANGUAGES, isLanguage } from './i18n.js';

export const OBSERVATION_SEGMENT = 'observation';
// Secciones de estación que ya sirve este frontend. Al migrar una pestaña
// más, su segmento entra aquí y deja de reenviarse a Streamlit.
export const STATION_SEGMENTS = ['observation', 'trends', 'historical'];
// Secciones que no cuelgan de una estación concreta.
export const GLOBAL_SEGMENTS = ['ranking', 'map'];

/** Directorio de estaciones de cada idioma: `estaciones`, `weather-stations`… */
export function directorySlug(language) {
  return LANGUAGES[language]?.directory_slug || '';
}

/**
 * URL de una ficha estática antigua:
 * `/es/estaciones/aemet/barcelona-drassanes-0201x.html`.
 *
 * Devuelve `{ language, slug }` o `null`. Los índices de red y las páginas de
 * ciudad tienen otra forma y NO entran aquí: siguen sirviéndose desde la app
 * antigua hasta que se migren.
 */
export function parseLegacyStationPath(pathname) {
  const segments = pathname.replace(/^\/+/, '').split('/');
  if (segments.length !== 4) return null;
  const [language, directory, , file] = segments;
  if (!isLanguage(language) || directory !== directorySlug(language)) return null;
  if (!file.endsWith('.html')) return null;
  const slug = decodeURIComponent(file.slice(0, -'.html'.length));
  return slug ? { language, slug } : null;
}

/**
 * `/es/observation/algo` → `{ language, section, slug }`.
 *
 * También reconoce `/es/observation/RED/id`, la forma que usan las redes sin
 * ficha indexable (IEM, Netatmo, Windy): sus datos se consultan igual, pero
 * no tienen slug único a nivel mundial.
 */
export function parseObservationPath(pathname) {
  const segments = pathname.replace(/^\/+/, '').split('/');
  if (segments.length !== 3 && segments.length !== 4) return null;
  const [language, section, ...rest] = segments;
  if (!isLanguage(language) || !STATION_SEGMENTS.includes(section)) return null;
  if (segments.length === 4) {
    if (section !== OBSERVATION_SEGMENT || !rest[0] || !rest[1]) return null;
    return {
      language,
      section,
      provider: decodeURIComponent(rest[0]),
      stationId: decodeURIComponent(rest[1])
    };
  }
  if (!rest[0]) return null;
  return { language, section, slug: decodeURIComponent(rest[0]) };
}

/** `/es/ranking` → `{ language, section }`. */
export function parseGlobalSectionPath(pathname) {
  const segments = pathname.replace(/^\/+/, '').split('/');
  if (segments.length !== 2) return null;
  const [language, section] = segments;
  if (!isLanguage(language) || !GLOBAL_SEGMENTS.includes(section)) return null;
  return { language, section };
}

const OWNED_EXACT = new Set([
  // La portada ya es el panel vacío del frontend nuevo, no la app antigua.
  '/',
  '/robots.txt',
  '/sitemap.xml',
  '/forecast',
  // Panel interno de uso: no se indexa ni se enlaza, pero es nuestro.
  '/stats',
  // Estáticos que viajan con el frontend: sirviéndolos aquí, una ficha se
  // pinta entera sin tocar el servicio antiguo.
  '/favicon.ico',
  '/favicon.png',
  '/favicon-32x32.png',
  '/og-image.png'
]);
const OWNED_PREFIXES = [
  '/_app/',
  '/@fs/',
  '/node_modules/',
  '/.well-known/',
  // El visor de predicción ya viaja con este servicio: es un SPA estático
  // que solo necesita /v1, y así deja de depender de Streamlit.
  '/forecast/'
];

/**
 * La API tampoco es del servicio antiguo: en producción FastAPI solo escucha
 * dentro de su contenedor, así que el `/v1` que pide el navegador lo reenvía
 * este servidor al backend. Va aparte de `isOwnedPath` porque su destino es
 * otro: no lo contesta SvelteKit, lo proxya a la API.
 */
export function isApiPath(pathname) {
  const path = pathname.split('?')[0];
  return path === '/v1' || path.startsWith('/v1/');
}

/**
 * Directorios, índices y ciudades: `/es/estaciones.html`,
 * `/es/estaciones/aemet.html`, `/es/tiempo.html`, `/fr/meteo/barcelone.html`.
 *
 * Cada idioma tiene sus propios segmentos —`estaciones`/`weather-stations`,
 * `tiempo`/`weather`—, así que se preguntan a la tabla en vez de escribirlos
 * a mano.
 */
export function isDirectoryPath(pathname) {
  const segments = pathname.replace(/^\/+/, '').split('/');
  const [language, ...rest] = segments;
  if (!isLanguage(language) || !rest.length || rest.length > 2) return false;

  const directorio = LANGUAGES[language]?.directory_slug || '';
  const ciudad = LANGUAGES[language]?.city_slug || '';
  const raiz = rest[0].replace(/\.html$/, '');
  if (raiz !== directorio && raiz !== ciudad) return false;
  // Un segundo segmento es la red o la ciudad; siempre termina en `.html`.
  return rest.length === 1 ? rest[0].endsWith('.html') : rest[1].endsWith('.html');
}

/**
 * ¿Contesta el frontend nuevo a esta ruta?
 *
 * Todo lo que devuelva `false` acaba en la app antigua tal cual, incluidos el
 * WebSocket de Streamlit y los estáticos que sirve su paquete.
 */
export function isOwnedPath(pathname) {
  const path = pathname.split('?')[0];
  if (OWNED_EXACT.has(path)) return true;
  // Solo los sitemaps del frontend nuevo. `directories-sitemap.xml` lo
  // sigue escribiendo Python y tiene que llegar al servicio antiguo.
  if (path === '/sitemap-static.xml') return true;
  if (/^\/sitemap-observation-\d+\.xml$/.test(path)) return true;
  if (OWNED_PREFIXES.some((prefix) => path.startsWith(prefix))) return true;
  if (path === `/${OBSERVATION_SEGMENT}` || path.startsWith(`/${OBSERVATION_SEGMENT}/`)) return true;
  // Directorios de red, índices y páginas de ciudad. Eran lo último que
  // quedaba en Streamlit; ahora viajan como estáticos de este servicio, y
  // reenviarlas al proxy las dejaría en 404 el día que se apague.
  if (path === '/directories-sitemap.xml') return true;
  // Las URLs de la aplicación anterior: ahora redirigen a su equivalente en
  // vez de morir con ella.
  if (path === '/app' || path.startsWith('/app/')) return true;
  if (isDirectoryPath(path)) return true;
  if (parseObservationPath(path)) return true;
  if (parseGlobalSectionPath(path)) return true;
  if (parseLegacyStationPath(path)) return true;
  return false;
}

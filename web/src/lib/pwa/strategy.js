/**
 * Qué hace el service worker con cada petición.
 *
 * Aparte del propio worker para poder probarlo sin navegador. La regla de
 * fondo: esto es una app de datos en vivo, así que el service worker nunca
 * sirve un dato meteorológico ni una página de la caché mientras haya red.
 * Solo acelera lo que no puede quedarse viejo —los ficheros con hash— y da
 * una página decente cuando no hay conexión.
 */

/** Página que se enseña al navegar sin conexión. Se precachea al instalar. */
export const OFFLINE_URL = '/offline.html';

/**
 * Lo único que se descarga al instalar: la página sin conexión y lo que ella
 * usa. Precachear la build entera serían 3,5 MB y 177 ficheros en la primera
 * visita de cada persona —incluido MapLibre, aunque nunca abra el mapa— y el
 * tráfico de salida del servicio web se paga.
 */
export const PRECACHE_FILES = [OFFLINE_URL, '/icons/icon-192.png'];

/**
 * Ficheros que no cambian sin cambiar de nombre: la build de SvelteKit y la
 * del visor de predicción llevan hash, y las fuentes no se tocan. Se guardan
 * la primera vez que se piden y desde entonces salen de la caché.
 *
 * El worker de MapLibre no entra: se llama siempre igual y una copia vieja
 * dejaría el mapa roto tras actualizar la librería.
 */
const CACHE_FIRST = [/^\/_app\/immutable\//, /^\/forecast\/assets\//, /^\/fonts\/[^/]+\.woff2$/];

/**
 * @param {{ url: string, method: string, mode?: string }} request
 * @param {string} origin origen del sitio
 * @returns {'navigate' | 'cache-first' | 'network'}
 */
export function strategyFor(request, origin) {
  if (request.method !== 'GET') return 'network';
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return 'network';
  }
  // Teselas de CARTO, fuentes del mapa y cualquier otro dominio: no son
  // nuestras y no hay por qué meterse.
  if (url.origin !== origin) return 'network';
  // Datos en vivo y estadísticas. Un parte de hace tres horas presentado como
  // actual es peor que un error.
  if (url.pathname.startsWith('/v1/')) return 'network';
  if (request.mode === 'navigate') return 'navigate';
  if (CACHE_FIRST.some((pattern) => pattern.test(url.pathname))) return 'cache-first';
  return 'network';
}

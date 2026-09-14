/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />
/**
 * Service worker de la PWA.
 *
 * SvelteKit lo registra solo al encontrar este fichero. Las reglas de qué se
 * cachea y qué no están en `$lib/pwa/strategy.js`; aquí solo se aplican.
 */
import { version } from '$service-worker';

import { OFFLINE_URL, PRECACHE_FILES, strategyFor } from '$lib/pwa/strategy.js';

const sw = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));

// Una caché por despliegue. Al activarse la versión nueva se borran las
// anteriores: los ficheros con hash de otra build ya no se van a pedir.
const CACHE = `mlx-${version}`;

sw.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE_FILES))
      // Puede tomar el control enseguida: nunca sirve HTML ni datos de la
      // caché con red, así que no hay pestaña abierta a la que dejar a medias.
      .then(() => sw.skipWaiting())
  );
});

sw.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => sw.clients.claim())
  );
});

sw.addEventListener('fetch', (event) => {
  const strategy = strategyFor(event.request, sw.location.origin);
  // Lo que va a red se deja pasar sin `respondWith`: el navegador lo trata
  // como si no hubiera service worker, con su caché HTTP y sus errores.
  if (strategy === 'navigate') event.respondWith(navigate(event.request));
  else if (strategy === 'cache-first') event.respondWith(cacheFirst(event));
});

/** La página de siempre; sin conexión, la de aviso. */
async function navigate(request) {
  try {
    return await fetch(request);
  } catch {
    const offline = await caches.match(OFFLINE_URL);
    return offline || Response.error();
  }
}

/** @param {FetchEvent} event */
async function cacheFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  // Solo respuestas completas y buenas: un 404 guardado sería para siempre.
  if (response.ok && response.status === 200) {
    // La respuesta sale ya hacia la página, pero el worker tiene que seguir
    // vivo hasta terminar de guardarla: sin `waitUntil`, el navegador puede
    // pararlo en cuanto contesta y la copia no llega a escribirse.
    event.waitUntil(cache.put(request, response.clone()).catch(() => {}));
  }
  return response;
}

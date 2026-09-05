/**
 * Entrada del servicio web.
 *
 * El frontend nuevo escucha en $PORT y es quien recibe el tráfico de
 * www.meteolabx.com. Contesta él las rutas ya migradas —las fichas de
 * observación, sus redirecciones y los sitemaps— y hace de proxy con TODO lo
 * demás hacia el servicio antiguo: la app Streamlit, el visor de predicción y
 * las páginas estáticas de directorios y ciudades.
 *
 * El proxy reenvía también el `upgrade` de WebSocket. Sin eso Streamlit
 * carga la página y se queda en blanco: su sesión entera viaja por
 * `/_stcore/stream`.
 *
 *   PORT                    puerto público (Railway lo inyecta)
 *   METEOLABX_LEGACY_ORIGIN  servicio actual (Streamlit + FastAPI)
 *   METEOLABX_API_URL        backend FastAPI para el renderizado en servidor
 */
import { createServer } from 'node:http';

import httpProxy from 'http-proxy';

import { handler } from './build/handler.js';
import { normalizeBasePath, withLegacyBase } from './src/lib/legacy-path.js';
import { isApiPath, isOwnedPath } from './src/lib/seo/ownership.js';

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || '0.0.0.0';
const LEGACY_ORIGIN = (process.env.METEOLABX_LEGACY_ORIGIN || '').replace(/\/+$/, '');
// El backend FastAPI. En local es el de siempre; en Railway, la red privada
// del servicio Python.
const API_ORIGIN = (process.env.METEOLABX_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
// Prefijo bajo el que vive hoy Streamlit. Vaciar la variable desactiva la
// reescritura si algún día vuelve a la raíz.
const LEGACY_BASE_PATH = normalizeBasePath(process.env.METEOLABX_STREAMLIT_BASE_PATH ?? 'app');

const toLegacy = (url) => withLegacyBase(url, LEGACY_BASE_PATH);

const proxy = LEGACY_ORIGIN
  ? httpProxy.createProxyServer({
      target: LEGACY_ORIGIN,
      changeOrigin: true,
      ws: true,
      xfwd: true,
      // Streamlit mantiene la conexión abierta indefinidamente; un timeout
      // corto cortaría la sesión de quien esté usando la app.
      proxyTimeout: 0,
      timeout: 0
    })
  : null;

const apiProxy = httpProxy.createProxyServer({
  target: API_ORIGIN,
  changeOrigin: true,
  xfwd: true
});

for (const instance of [proxy, apiProxy]) {
  if (!instance) continue;
  instance.on('error', (error, _request, response) => {
    console.error('[proxy]', error.message);
    if (response && 'writeHead' in response && !response.headersSent) {
      response.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('El servicio no está disponible ahora mismo.');
    } else if (response && 'destroy' in response) {
      response.destroy();
    }
  });
}

function pathOf(request) {
  try {
    return new URL(request.url, 'http://localhost').pathname;
  } catch {
    return request.url || '/';
  }
}

const server = createServer((request, response) => {
  const path = pathOf(request);
  if (isApiPath(path)) {
    apiProxy.web(request, response);
    return;
  }
  if (proxy && !isOwnedPath(path)) {
    request.url = toLegacy(request.url);
    proxy.web(request, response);
    return;
  }
  handler(request, response, () => {
    // Ruta nuestra que SvelteKit no reconoce: si hay app antigua detrás, que
    // lo intente ella antes de dar por perdida la petición.
    if (proxy) {
      request.url = toLegacy(request.url);
      proxy.web(request, response);
      return;
    }
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  });
});

// El WebSocket de Streamlit nunca es una ruta nuestra: va entero al proxy.
server.on('upgrade', (request, socket, head) => {
  if (!proxy) {
    socket.destroy();
    return;
  }
  // El WebSocket ya llega con el prefijo —lo construye el propio Streamlit—,
  // pero se normaliza igual por si alguien abre la app sin él.
  request.url = toLegacy(request.url);
  proxy.ws(request, socket, head);
});

server.listen(PORT, HOST, () => {
  console.log(`[web] escuchando en http://${HOST}:${PORT}`);
  console.log(`[web] backend: ${process.env.METEOLABX_API_URL || 'http://127.0.0.1:8000'}`);
  console.log(
    `[web] app antigua: ${LEGACY_ORIGIN || '(sin proxy)'}` +
      (LEGACY_ORIGIN && LEGACY_BASE_PATH ? ` (prefijo ${LEGACY_BASE_PATH})` : '')
  );
});

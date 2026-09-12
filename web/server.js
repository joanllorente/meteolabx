/**
 * Entrada del servicio web.
 *
 * Escucha en $PORT y recibe el tráfico de www.meteolabx.com. Sirve las páginas
 * él mismo y reenvía al backend FastAPI únicamente lo que cuelga de `/v1`.
 *
 * Hubo aquí un proxy que mandaba a la aplicación de Streamlit todo lo que
 * este servicio no reconocía, incluido el `upgrade` de WebSocket que aquella
 * necesitaba para no quedarse en blanco. Ya no queda nada al otro lado: todas
 * las secciones están migradas y el visor de predicción se sirve como
 * estático desde `web/static/forecast`.
 *
 *   PORT               puerto público (Railway lo inyecta)
 *   METEOLABX_API_URL  backend FastAPI para el renderizado en servidor
 */
import { createServer } from 'node:http';

import httpProxy from 'http-proxy';
import { createApiAgent } from './src/lib/server/proxy-agent.js';

import { handler } from './build/handler.js';
import { isApiPath } from './src/lib/seo/ownership.js';

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || '0.0.0.0';
// El backend FastAPI. En local es el de siempre; en Railway, la red privada
// del servicio Python.
const API_ORIGIN = (process.env.METEOLABX_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');

const apiProxy = httpProxy.createProxyServer({
  target: API_ORIGIN,
  agent: createApiAgent(API_ORIGIN),
  changeOrigin: true,
  xfwd: true
});

for (const instance of [apiProxy]) {
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
  handler(request, response, () => {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  });
});

server.listen(PORT, HOST, () => {
  console.log(`[web] escuchando en http://${HOST}:${PORT}`);
  console.log(`[web] backend: ${API_ORIGIN}`);
});

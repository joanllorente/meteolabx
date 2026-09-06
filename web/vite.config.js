import { execSync } from 'node:child_process';

import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

/**
 * Qué código está sirviendo el navegador, en siete caracteres.
 *
 * Es el mismo dato que enseñaba el modal de novedades de Streamlit —de donde
 * viene `utils/build_info.py`— y la primera pregunta cuando se despliega
 * varias veces seguidas: la versión no cambia y el commit sí. En Railway lo
 * pone la propia plataforma; en local se pregunta a git, y si no hay ninguno
 * de los dos se queda en «local», que también es una respuesta.
 */
function buildId() {
  const railway = (process.env.RAILWAY_GIT_COMMIT_SHA || '').trim();
  if (railway) return railway.slice(0, 7);
  try {
    return execSync('git rev-parse --short=7 HEAD', { encoding: 'utf8', timeout: 1000 }).trim() || 'local';
  } catch {
    return 'local';
  }
}

export default defineConfig({
  define: {
    __APP_BUILD__: JSON.stringify(buildId())
  },
  plugins: [sveltekit()],
  optimizeDeps: {
    // MapLibre parsea las teselas en un Web Worker que carga con
    // `new Worker(new URL('./maplibre-gl-worker', import.meta.url))`. El
    // pre-bundling de Vite reescribe la librería pero no emite ese fichero,
    // así que en desarrollo el worker daba 404: el mapa pintaba el fondo del
    // estilo —negro— y nada más, sin lanzar un solo error.
    //
    // Excluyéndola, Vite la sirve tal cual y la URL del worker resuelve. En
    // el build de producción no hace falta: Rollup sí entiende ese patrón.
    exclude: ['maplibre-gl']
  },
  server: {
    // En local el backend FastAPI vive en :8000. El proxy deja que el
    // navegador pida /v1/... al mismo origen, igual que en producción.
    proxy: {
      '/v1': 'http://127.0.0.1:8000'
    }
  }
});

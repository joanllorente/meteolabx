import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
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

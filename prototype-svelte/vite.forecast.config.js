import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'node:path';

export default defineConfig({
  base: './',
  plugins: [svelte()],
  server: {
    proxy: {
      '/v1': 'http://127.0.0.1:8000'
    }
  },
  build: {
    outDir: '../static/forecast_app',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        forecast: resolve(import.meta.dirname, 'forecast.html')
      }
    }
  }
});

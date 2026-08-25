import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/v1': 'http://127.0.0.1:8000'
    }
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        forecast: resolve(import.meta.dirname, 'forecast.html')
      }
    }
  }
});

import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // adapter-node porque las fichas de estación se renderizan en el
    // servidor: Google tiene que recibir el panel ya pintado, no un div
    // vacío que se rellena con JavaScript.
    adapter: adapter({ out: 'build' }),
    alias: {
      $components: 'src/lib/components'
    }
  }
};

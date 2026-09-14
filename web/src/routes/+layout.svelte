<script>
  import { onMount } from 'svelte';

  import '../app.css';
  import { initPwa } from '$lib/pwa/install.svelte.js';

  let { children } = $props();

  // Aquí y no en la ficha: el aviso de «se puede instalar» llega una sola vez,
  // al cargar, sea cual sea la página por la que se entra.
  onMount(initPwa);
</script>

{@render children()}

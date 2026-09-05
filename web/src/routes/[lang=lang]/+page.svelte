<script>
  // Mismo componente que la raíz: la portada no cambia, solo su URL.
  import Home from '../+page.svelte';

  let { data } = $props();
</script>

<Home {data} />

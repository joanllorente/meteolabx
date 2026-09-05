<script>
  import { page } from '$app/state';

  /**
   * Página de error. Lleva `noindex` siempre: un 404 indexado es una URL
   * gastada, y estas fichas están precisamente para lo contrario.
   */
  const notFound = $derived(page.status === 404);
</script>

<svelte:head>
  <title>{notFound ? 'Estación no encontrada' : 'Error'} · MeteoLabX</title>
  <meta name="robots" content="noindex, follow" />
</svelte:head>

<main>
  <p class="code">{page.status}</p>
  <h1>{notFound ? 'Esta estación no existe' : 'Algo ha fallado'}</h1>
  <p class="lede">
    {#if notFound}
      La dirección no corresponde a ninguna estación del catálogo. Puede que la
      red la haya retirado o que el enlace esté incompleto.
    {:else}
      No hemos podido preparar esta página. Vuelve a intentarlo en un momento.
    {/if}
  </p>
  <a class="cta" href="/">Ir a MeteoLabX</a>
</main>

<style>
  main {
    width: min(640px, calc(100% - 34px));
    margin: 14vh auto;
    text-align: center;
  }
  .code { color: var(--muted); font-size: 0.9rem; font-weight: 700; letter-spacing: 0.1em; }
  h1 { font-size: clamp(1.7rem, 4vw, 2.6rem); margin: 10px 0 14px; font-weight: 800; }
  .lede { color: var(--ink-2); line-height: 1.6; }
  .cta {
    display: inline-block;
    margin-top: 26px;
    padding: 12px 20px;
    border-radius: var(--r-sm);
    background: var(--accent);
    color: var(--accent-ink);
    font-weight: 700;
    text-decoration: none;
  }
</style>

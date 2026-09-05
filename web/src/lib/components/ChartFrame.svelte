<script>
  /**
   * Marco de una gráfica: la envuelve y le añade el botón de descarga.
   *
   * El botón vive fuera del SVG a propósito —no debe salir en el PNG— y se
   * mantiene tenue hasta que el ratón entra en la gráfica. En pantallas
   * táctiles no hay hover, así que ahí se ve siempre.
   */
  import { downloadChartPng } from '$lib/chart-export.js';

  let { name = 'meteolabx', label = 'Descargar PNG', children } = $props();

  let frame;
  let busy = $state(false);

  async function download() {
    const svg = frame?.querySelector('svg');
    if (!svg || busy) return;
    busy = true;
    try {
      await downloadChartPng(svg, name);
    } finally {
      busy = false;
    }
  }
</script>

<div class="frame" bind:this={frame}>
  {@render children()}
  <button class="save" type="button" onclick={download} title={label} aria-label={label}>
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3v11m0 0 4-4m-4 4-4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
    </svg>
  </button>
</div>

<style>
  .frame { position: relative; }

  .save {
    position: absolute; top: 2px; right: 2px;
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px;
    border: 1px solid var(--border); border-radius: 7px;
    background: var(--card); color: var(--muted);
    opacity: 0; transition: opacity 0.16s, color 0.16s, border-color 0.16s;
  }
  .save svg { width: 14px; height: 14px; }
  .frame:hover .save, .save:focus-visible { opacity: 1; }
  .save:hover { color: var(--ink); border-color: var(--border-2); }

  /* Sin ratón no hay hover que revele el botón. */
  @media (hover: none) {
    .save { opacity: 1; }
  }
</style>

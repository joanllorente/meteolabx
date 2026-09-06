<script>
  /**
   * Marco de una gráfica: la envuelve y le añade los botones de descarga y de
   * ampliar.
   *
   * Los botones viven fuera del SVG a propósito —no deben salir en el PNG— y
   * se mantienen tenues hasta que el ratón entra en la gráfica. En pantallas
   * táctiles no hay hover, así que ahí se ven siempre.
   */
  import { getContext } from 'svelte';
  import { downloadChartPng } from '$lib/chart-export.js';
  import { ui } from '$lib/i18n/ui.js';

  let {
    name = 'meteolabx',
    label = 'Descargar PNG',
    // Si el lienzo es panorámico. Los de series lo son —el tiempo corre a lo
    // largo— y en un móvil de pie se ven girados; la rosa de los vientos, en
    // cambio, es cuadrada y girarla solo pondría los rumbos de lado.
    wide = true,
    children
  } = $props();

  // El idioma lo pone el armazón: aquí no llega por props, y estas dos
  // etiquetas no valen la pena propagarlas gráfica a gráfica.
  const language = getContext('mlx-language');
  const zoomLabel = $derived(ui(language?.() || 'es', 'chart_zoom'));
  const closeLabel = $derived(ui(language?.() || 'es', 'chart_close'));

  let frame;
  let busy = $state(false);
  let zoomed = $state(false);

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

  /**
   * Mientras el visor está abierto, la página de debajo no se mueve.
   *
   * Sin esto, en el móvil el dedo arrastra el panel entero al intentar seguir
   * la línea de la gráfica.
   */
  $effect(() => {
    if (!zoomed || typeof document === 'undefined') return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  });

  /**
   * Saca el visor del árbol de la tarjeta y lo cuelga del `body`.
   *
   * Mientras vivía dentro, le llovía el CSS pensado para la gráfica pequeña
   * —la rosa de los vientos se quedaba clavada a 300 px y echada a un lado—,
   * y cualquier ancestro con recorte o desplazamiento podía comérselo.
   */
  function portal(node) {
    document.body.appendChild(node);
    return {
      destroy() {
        node.remove();
      }
    };
  }

  function onKey(event) {
    if (event.key === 'Escape') zoomed = false;
  }
</script>

<svelte:window onkeydown={zoomed ? onKey : null} />

<!-- La gráfica entera abre el visor: en un móvil mide poco más de un dedo de
     alto, y a pantalla completa —girada, si el teléfono está de pie— se lee de
     verdad. Un botón para eso obligaba a apuntar a 26 px con el pulgar justo
     encima de la línea que se quiere mirar. -->
<div
  class="frame"
  bind:this={frame}
  role="button"
  tabindex="0"
  title={zoomLabel}
  aria-label={zoomLabel}
  onclick={() => (zoomed = true)}
  onkeydown={(event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      zoomed = true;
    }
  }}
>
  {@render children()}
  <div class="tools">
    <!-- Descargar no debe ampliar de paso. -->
    <button
      class="tool"
      type="button"
      onclick={(event) => {
        event.stopPropagation();
        download();
      }}
      title={label}
      aria-label={label}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3v11m0 0 4-4m-4 4-4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>
  </div>
</div>

{#if zoomed}
  <!-- La gráfica se vuelve a dibujar aquí dentro: es un SVG que solo depende
       de sus datos, así que la copia grande y la de la tarjeta muestran lo
       mismo, cursor incluido. -->
  <div class="viewer" use:portal role="dialog" aria-modal="true" aria-label={name}>
    <button class="backdrop" type="button" aria-label={closeLabel} onclick={() => (zoomed = false)}></button>
    <div class="stage" class:square={!wide}>
      {@render children()}
    </div>
    <button class="close" type="button" onclick={() => (zoomed = false)} aria-label={closeLabel}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>
  </div>
{/if}

<style>
  .frame { position: relative; cursor: zoom-in; }
  .frame:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 10px; }

  .tools { position: absolute; top: 2px; right: 2px; display: flex; gap: 4px; }
  .tool {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px;
    border: 1px solid var(--border); border-radius: 7px;
    background: var(--card); color: var(--muted);
    opacity: 0; transition: opacity 0.16s, color 0.16s, border-color 0.16s;
  }
  .tool svg { width: 14px; height: 14px; }
  .frame:hover .tool, .tool:focus-visible { opacity: 1; }
  .tool:hover { color: var(--ink); border-color: var(--border-2); }

  /* Sin ratón no hay hover que revele los botones. */
  @media (hover: none) {
    .tool { opacity: 1; }
  }

  .viewer { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; }
  .backdrop {
    position: absolute; inset: 0; border: 0; padding: 0;
    background: color-mix(in srgb, var(--bg) 82%, transparent);
    backdrop-filter: blur(6px);
  }
  /* Dentro del visor la gráfica ocupa la caja entera, y el dedo dibuja el
     cursor en vez de arrastrar la página. */
  .stage :global(svg) { touch-action: none; width: 100%; max-width: none; height: auto; }
  .stage {
    position: relative;
    width: min(1100px, 94vw);
    padding: 14px 16px;
    border: 1px solid var(--border); border-radius: var(--r-md);
    background: var(--panel); box-shadow: var(--shadow);
  }
  /* Cuadrada: el ancho manda sobre el alto, que si no se sale de pantalla. */
  .stage.square { width: min(560px, 92vw, 82vh); }

  .close {
    position: absolute; top: max(12px, env(safe-area-inset-top)); right: 14px;
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px;
    border: 1px solid var(--border); border-radius: 9px;
    background: var(--card); color: var(--ink-2);
  }
  .close svg { width: 18px; height: 18px; }

  /* De pie, el teléfono no da ancho para una gráfica panorámica: se gira, y
     entonces el lado largo de la pantalla es el eje del tiempo —que es lo que
     da resolución temporal—. La medida se cruza a propósito: el ancho de la
     caja es la altura de la ventana, porque lo que gira es la caja entera.

     Y se centra por posición, no con la retícula: la caja mide más que el
     ancho de la pantalla antes de girar, y una retícula con un hijo que no le
     cabe lo empuja hacia un lado en vez de repartir lo que sobra. */
  @media (max-width: 720px) and (orientation: portrait) {
    .stage:not(.square) {
      position: absolute; top: 50%; left: 50%;
      width: 92vh; padding: 10px 12px;
      transform: translate(-50%, -50%) rotate(90deg);
      transform-origin: center;
    }
    /* La rosa se queda de pie y ocupa el ancho: es un círculo, no una serie,
       y de lado solo se leerían mal los rumbos. */
    .stage.square { width: min(94vw, 70vh); padding: 10px 12px; }
  }
</style>

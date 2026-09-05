<script>
  /**
   * Barra de progreso de la navegación.
   *
   * Las páginas se renderizan en el servidor: entre el clic y el cambio de
   * pantalla puede haber segundos de consulta al proveedor, y sin señal
   * alguna la aplicación parece colgada. La barra aparece con un pequeño
   * retardo para no parpadear en las navegaciones instantáneas.
   */
  import { isNavigating } from '$lib/navigation.js';

  let visible = $state(false);

  $effect(() => {
    if (!isNavigating()) {
      visible = false;
      return;
    }
    // Por debajo de este umbral la navegación ya ha terminado y un destello
    // molesta más que ayuda.
    const timer = setTimeout(() => (visible = true), 140);
    return () => clearTimeout(timer);
  });
</script>

{#if visible}
  <div class="progress" role="progressbar" aria-busy="true"><span></span></div>
{/if}

<style>
  .progress {
    position: fixed; top: 0; left: 0; right: 0; z-index: 60;
    height: 2px; overflow: hidden; background: transparent;
  }
  .progress span {
    display: block; width: 40%; height: 100%;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    animation: slide 1.1s ease-in-out infinite;
  }
  @keyframes slide {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(350%); }
  }
  @media (prefers-reduced-motion: reduce) {
    .progress span { animation: none; width: 100%; opacity: 0.6; }
  }
</style>

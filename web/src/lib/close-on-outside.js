/**
 * Cierra un `<details>` al pinchar fuera o al pulsar Escape.
 *
 * Un `<details>` nativo solo se cierra volviendo a pulsar su resumen, y un
 * panel flotante que se queda abierto tapando la página no es lo que espera
 * nadie. Se usa como acción: `<details use:closeOnOutside>`.
 */
export function closeOnOutside(node) {
  const closeOutside = (event) => {
    if (node.open && !node.contains(event.target)) node.open = false;
  };
  const closeOnEscape = (event) => {
    if (event.key === 'Escape' && node.open) node.open = false;
  };
  // En fase de captura: hay componentes —el mapa, sin ir más lejos— que
  // consumen el evento antes de que llegue al documento.
  document.addEventListener('pointerdown', closeOutside, true);
  document.addEventListener('keydown', closeOnEscape);
  return {
    destroy() {
      document.removeEventListener('pointerdown', closeOutside, true);
      document.removeEventListener('keydown', closeOnEscape);
    }
  };
}

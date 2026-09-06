/**
 * Si la ventana es estrecha.
 *
 * Lo usan las gráficas. Son SVG con `viewBox` fijo y ancho al 100 %: el
 * navegador las escala enteras, así que en un móvil un lienzo pensado para el
 * escritorio se queda en poco más de un dedo de alto. Lo que se ajusta es la
 * altura y solo la altura: acortar el lienzo a lo ancho también las agrandaría,
 * pero a costa de la resolución temporal, que es justo lo que se mira en una
 * serie de veinticuatro horas.
 *
 * En el servidor no hay ventana: se renderiza con la medida de escritorio y,
 * si toca, el navegador la ajusta al hidratar.
 */

const QUERY = '(max-width: 720px)';

let narrow = $state(false);

/**
 * Lee la anchura al montar y se queda atento a los cambios.
 *
 * Devuelve la limpieza que espera `$effect`, como `loadTheme`: girar el móvil
 * o cambiar el tamaño de la ventana reajusta las gráficas al vuelo.
 */
export function loadViewport() {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {};
  const query = window.matchMedia(QUERY);
  narrow = query.matches;
  const follow = (event) => {
    narrow = event.matches;
  };
  query.addEventListener('change', follow);
  return () => query.removeEventListener('change', follow);
}

export function isNarrow() {
  return narrow;
}

/** Alto del lienzo de una gráfica: algo más generoso en pantallas pequeñas. */
export function chartHeight(wide) {
  return narrow ? Math.round(wide * 1.3) : wide;
}

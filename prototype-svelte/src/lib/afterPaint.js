/**
 * Ejecuta `tarea` cuando el navegador ya ha pintado el fotograma siguiente.
 *
 * El INP mide desde el toque hasta el siguiente pintado. Si el trabajo pesado
 * —desmontar el mapa, rehacer streamlines e isolíneas— va dentro del propio
 * manejador, el usuario no ve ni que su toque ha llegado: en móvil se medían
 * de 0,5 a 3,5 s. Así primero se pinta la respuesta inmediata y luego se
 * calcula. `requestAnimationFrame` corre justo antes de pintar; el
 * `setTimeout` lo deja para después.
 */
export function afterPaint(tarea) {
  let temporizador = 0;
  const fotograma = requestAnimationFrame(() => {
    temporizador = setTimeout(tarea, 0);
  });
  return () => {
    cancelAnimationFrame(fotograma);
    clearTimeout(temporizador);
  };
}

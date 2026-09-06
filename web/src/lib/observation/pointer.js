/**
 * Dónde cae el puntero a lo largo del eje del tiempo, entre 0 y 1.
 *
 * Suena a cuenta de tres líneas —restar el borde izquierdo y dividir por el
 * ancho— y lo era mientras la gráfica solo se escalaba. En el visor a pantalla
 * completa la caja va girada con una transformación CSS, y ahí fallan los dos
 * atajos: el rectángulo del elemento pasa a estar alineado con la pantalla, no
 * con la gráfica, y `getScreenCTM` no recoge las transformaciones CSS de los
 * ancestros, así que el dedo recorría el eje entero sin mover el cursor.
 *
 * Lo que sí sobrevive a cualquier giro es preguntarle al navegador dónde ha
 * pintado dos puntos conocidos del propio lienzo: los extremos del eje. Con
 * ellos, la posición del puntero es su proyección sobre esa recta, y la cuenta
 * vale igual escalada, girada o del revés.
 */
export function pointerFraction(svg, event) {
  const probes = svg?.querySelectorAll?.('[data-axis-end]');
  if (!probes || probes.length !== 2) return null;

  const centre = (element) => {
    const box = element.getBoundingClientRect();
    return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  };
  const start = centre(probes[0]);
  const end = centre(probes[1]);

  const axisX = end.x - start.x;
  const axisY = end.y - start.y;
  const length = axisX * axisX + axisY * axisY;
  if (!length) return null;

  const along = ((event.clientX - start.x) * axisX + (event.clientY - start.y) * axisY) / length;
  return Math.min(1, Math.max(0, along));
}

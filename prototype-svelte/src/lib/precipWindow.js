/**
 * Precipitación acumulada en ventana móvil.
 *
 * Cada frame del acumulado trae la lluvia caída desde el inicio de la pasada
 * hasta su hora. Lo caído entre A y B es la diferencia de los dos: no hace
 * falta pedir nada nuevo al servidor, solo el frame de A además del de B.
 */

/** Los mapas que lo admiten: los acumulados desde la pasada de AROME y ECMWF. */
export const MOVING_WINDOW_PRODUCTS = new Set(['accumulated-precip', 'ecmwf-precip-accumulated']);

/** Lo mínimo que se pinta como lluvia de la ventana, en mm. */
export const MIN_WINDOW_MM = 0.1;

/** Paso de cuantización de la matriz de valores, o 0 si viene en Float32. */
export function valueStep(frame) {
  const matrix = (frame?.arrays || []).find((item) => item.name === 'value');
  const step = Number(matrix?.step);
  return Number.isFinite(step) && step > 0 ? step : 0;
}

/**
 * El frame de B con la lluvia de la ventana (A, B].
 *
 * Cada frame viaja redondeado al paso más cercano de su propia escala, que
 * depende de su máximo: con 300 mm acumulados el paso es de 0,05 mm, y A y B
 * no tienen por qué compartirlo. Donde no llovió entre los dos, la resta deja
 * un paso de ruido, y como el mapa pinta desde 0,05 mm salía salpicado de
 * píxeles sueltos que aparecían y desaparecían al mover A o B.
 *
 * Por debajo de 0,1 mm —lo que mide un pluviómetro— o de los dos pasos
 * juntos, si son más gruesos, es cero. Medido contra la suma de los mapas
 * horarios en dos ventanas reales (02/10/2026): de 7.644 y 8.698 celdas
 * falsas quedan 121 y 0, y la lluvia de verdad que se pierde es una celda.
 * Una celda sin dato en cualquiera de los dos queda sin dato.
 */
export function windowedFrame(end, start) {
  if (!end?.values || !start?.values || end.values.length !== start.values.length) return null;
  const tolerance = Math.max(MIN_WINDOW_MM, valueStep(end) + valueStep(start)) + 1e-6;
  const values = new Float32Array(end.values.length);
  for (let index = 0; index < values.length; index += 1) {
    const total = end.values[index];
    const before = start.values[index];
    if (!Number.isFinite(total) || !Number.isFinite(before)) {
      values[index] = NaN;
      continue;
    }
    const difference = total - before;
    values[index] = difference > tolerance ? difference : 0;
  }
  return { ...end, values, window_start: start.valid_time };
}

/**
 * Los extremos que se pueden elegir: el inicio de la pasada y cada hora.
 *
 * El acumulado empieza en H+01, así que la pasada es un extremo más —A en
 * ella es «desde el principio»— y los dos deslizadores comparten esta misma
 * escala: la posición 0 es la pasada y la k, la hora k-ésima del mapa.
 */
export function windowBounds(runIso, hours, describe) {
  return [{ ...describe(runIso), iso: runIso, horizon: 0 }, ...hours];
}

/** A siempre por detrás de B: devuelve la posición de A corregida. */
export function clampWindowStart(start, end) {
  return Math.max(0, Math.min(start, end - 1));
}

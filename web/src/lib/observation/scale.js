/**
 * Rango vertical de las gráficas de tendencia.
 *
 * Vive aparte y sin dependencias para poder probarse sin arrastrar medio
 * frontend: es una regla de escala, no lógica de interfaz.
 */

/**
 * Rango simétrico alrededor de cero con un suelo, como el
 * `_symmetric_y_range_with_min` de la aplicación actual.
 *
 * Sin ese suelo, una tendencia plana se amplifica hasta parecer una tormenta:
 * con variaciones de centésimas el eje se ajusta a ellas y el gráfico se
 * llena de ruido. Por debajo del mínimo, el eje se queda quieto.
 */
export function symmetricRange(values, minimumAbs) {
  const floor = Number.isFinite(minimumAbs) ? Math.abs(minimumAbs) : 0;
  let maxAbs = 0;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    maxAbs = Math.max(maxAbs, Math.abs(value));
  }
  const limit = maxAbs <= floor ? floor : maxAbs * 1.1;
  // Redondear hacia arriba al salto del eje: así el borde del gráfico es una
  // marca y no un número suelto como 6,7. Solo amplía, nunca recorta, con lo
  // que la escala mínima de cada magnitud se sigue respetando.
  const step = niceStep(limit * 2);
  const aligned = Math.ceil(limit / step - 1e-9) * step;
  return [-aligned, aligned];
}

// Pasos admitidos para el eje vertical. Sin 2,5: el eje tiene que leerse de un
// vistazo, y «2,5 · 5 · 7,5» cuesta más que «2 · 4 · 6».
const NICE_STEPS = [1, 2, 5, 10];

/** Salto redondo que parte `span` en aproximadamente `count` tramos. */
export function niceStep(span, count = 4) {
  if (!Number.isFinite(span) || span <= 0) return 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of NICE_STEPS) {
    if (raw <= factor * magnitude) return factor * magnitude;
  }
  return 10 * magnitude;
}

/**
 * Marcas del eje vertical: múltiplos del salto redondo dentro de `[min, max]`.
 *
 * Al ser todas múltiplos del mismo salto, el cero cae siempre en una marca
 * cuando el rango lo contiene, que es justo lo que se busca en una tendencia:
 * la referencia es el cambio de signo.
 */
export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];
  const step = niceStep(max - min, count);
  const ticks = [];
  const first = Math.ceil(min / step - 1e-9);
  const last = Math.floor(max / step + 1e-9);
  for (let i = first; i <= last; i += 1) {
    // El paso puede ser 0,1 o 0,5: multiplicar acumula error binario.
    ticks.push(Number((i * step).toFixed(10)) || 0);
  }
  return ticks;
}

/** Decimales que necesita un salto para escribirse exacto. */
export function tickDecimals(step) {
  if (!Number.isFinite(step) || step <= 0) return 1;
  return Math.max(0, -Math.floor(Math.log10(step)));
}

/**
 * Emparejado por tiempo del cursor compartido de las gráficas.
 *
 * Las cuatro tendencias se dibujan a la vez para poder compararlas, pero cada
 * una descarta sus propios huecos y tiene su propio paso —θe cada 20 minutos,
 * presión cada 180—, así que el índice del punto bajo el ratón en una no
 * señala el mismo instante en las demás. Lo que se comparte es el instante.
 *
 * Sin dependencias a propósito: así se puede probar en Node.
 */

/** Mitad del paso típico de la serie: hasta ahí el punto sigue siendo «ese». */
function tolerance(epochs) {
  const steps = [];
  let previous = null;
  for (const epoch of epochs) {
    if (!Number.isFinite(epoch)) continue;
    if (previous !== null) steps.push(epoch - previous);
    previous = epoch;
  }
  if (!steps.length) return 60;
  steps.sort((a, b) => a - b);
  const median = steps[Math.floor(steps.length / 2)];
  return Math.max(60, median / 2);
}

/**
 * Punto más cercano en el tiempo a `target`, o `null` si el instante cae
 * fuera de lo que cubre esta serie. Devolver `null` es lo correcto: un hueco
 * es la ausencia de dato, y marcar ahí el vecino de hace dos horas mentiría
 * justo en la comparación que se está haciendo.
 */
export function nearestIndex(epochs, target) {
  if (!Number.isFinite(target) || !epochs?.length) return null;
  const limit = tolerance(epochs);
  let best = null;
  let bestGap = Infinity;
  for (let index = 0; index < epochs.length; index += 1) {
    const epoch = epochs[index];
    if (!Number.isFinite(epoch)) continue;
    const gap = Math.abs(epoch - target);
    if (gap < bestGap) {
      bestGap = gap;
      best = index;
    }
  }
  return bestGap <= limit ? best : null;
}

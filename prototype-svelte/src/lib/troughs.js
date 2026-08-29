/**
 * Ejes de vaguada a partir del geopotencial.
 *
 * La cadena es la de un análisis a mano, en seis pasos:
 *
 *   1. Suavizar Z a escala sinóptica, para que las ondas cortas de AROME no
 *      generen ejes que ningún meteorólogo dibujaría.
 *   2. Curvatura de las isohipsas y vorticidad geostrófica del campo suave.
 *   3. Sobre cada isohipsa, los puntos de máxima curvatura ciclónica.
 *   4. Encadenar esos puntos en ejes coherentes.
 *   5. Podar ramas cortas y ejes que no lleguen a longitud sinóptica.
 *   6. Separar las depresiones cerradas de las vaguadas abiertas.
 *
 * Todo el análisis va sobre una rejilla engrosada: a 2,5 km de paso, un
 * gaussiano de escala sinóptica necesitaría un núcleo de cincuenta celdas por
 * lado, y el eje de una vaguada no se define mejor por mirarlo más de cerca.
 */

import { contourLines, gaussianBlur, stepLevels } from './contours.js';

/** Lado del bloque de engrosado, en celdas de AROME. */
export const TROUGH_BLOCK = 8;
/** σ del suavizado sinóptico, en kilómetros. */
export const TROUGH_SIGMA_KM = 50;
/** Longitud mínima de un eje, en kilómetros. */
export const TROUGH_MIN_LENGTH_KM = 350;
/** Curvatura ciclónica mínima para considerar un punto, en 1/km. */
export const TROUGH_MIN_CURVATURE = 1 / 900;
/** Gradiente mínimo del campo para fiarse de la curvatura, en dam por km. */
export const TROUGH_MIN_GRADIENT = 0.0075;
/** Separación mínima entre dos picos de la misma isohipsa, en km. */
export const TROUGH_PEAK_SPACING_KM = 300;
/**
 * Percentil de curvatura por flujo a partir del cual un punto es candidato.
 *
 * Quien de verdad separa una vaguada del ruido no es este umbral, sino que los
 * candidatos se repitan isohipsa tras isohipsa en el mismo sitio y sumen
 * longitud sinóptica. Apretarlo demasiado deja fuera las vaguadas someras, que
 * son las que más falta hace señalar porque no se ven de un vistazo: con 0,95
 * se quedaba fuera la del Atlántico de esta pasada, que se ve a simple vista
 * en el giro de las isohipsas de 560 y 564 dam.
 *
 * Un umbral absoluto sobre la curvatura sería más estable de una hora a otra,
 * pero probado sobre el campo real da entre cuatro y seis ejes por mapa y
 * parte en seis una vaguada sintética que es una sola: la escala de curvatura
 * cambia demasiado entre situaciones para fijarla de una vez.
 */
export const TROUGH_PERCENTILE = 0.88;
/** Distancia bajo la cual dos ejes se consideran el mismo, en km. */
export const TROUGH_MERGE_KM = 150;
/**
 * Giro máximo admitido respecto al rumbo que lleva el eje, en grados.
 *
 * Se compara contra el rumbo acumulado y no contra el tramo anterior: entre
 * dos isohipsas hay 20 km, así que un temblor de una celda en la posición del
 * vértice ya son 45° de un tramo al siguiente y el límite se comía ejes
 * perfectamente rectos. El rumbo, en cambio, sigue la curva lenta de una
 * vaguada real y rechaza el codo de noventa grados que la partía en L.
 */
export const TROUGH_MAX_TURN_DEG = 55;
/**
 * Bajada mínima de la isohipsa en el eje respecto a su entorno, en km.
 *
 * Es la amplitud de la onda, y es lo que separa una vaguada de un recodo. Hace
 * falta porque la curvatura ciclónica no basta: los hombros de una dorsal
 * también curvan hacia el lado ciclónico —matemáticamente, un bulto tiene la
 * cima convexa y los flancos cóncavos— y el detector los marcaba como ejes. En
 * la pasada de prueba, esos falsos ejes daban amplitudes de -185 a +77 km
 * frente a los +191 a +295 de las vaguadas de verdad.
 */
export const TROUGH_MIN_AMPLITUDE_KM = 150;
/** Distancia a cada lado con la que se compara, en km. */
export const TROUGH_AMPLITUDE_SPAN_KM = 500;
/**
 * Distancia mínima utilizable a cada lado, en km.
 *
 * Con menos que esto la comparación deja de significar nada: se estaría
 * midiendo la onda contra sí misma. Por debajo, el eje no se juzga.
 */
export const TROUGH_MIN_SPAN_KM = 200;
/** Longitud mínima de un eje medido con ventana recortada por el borde. */
export const TROUGH_EDGE_MIN_LENGTH_KM = 250;
/** Vértices mínimos de un eje aceptado con medición recortada. */
export const TROUGH_EDGE_MIN_POINTS = 3;

/**
 * Desvío máximo del eje respecto al gradiente, en grados.
 *
 * Un eje de vaguada cruza las isohipsas; no discurre pegado a ellas. Sin esta
 * condición, la cadena podía saltar al vértice de otra onda y seguir un buen
 * trecho paralela a una isohipsa, dibujando una L con un giro de casi 90°.
 */
export const TROUGH_MAX_DRIFT_DEG = 60;

/** Media por bloques que ignora los huecos. */
export function coarsen(field, width, height, block) {
  const cols = Math.floor(width / block);
  const rows = Math.floor(height / block);
  const output = new Float32Array(cols * rows);
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < cols; column += 1) {
      let total = 0;
      let count = 0;
      for (let y = row * block; y < (row + 1) * block; y += 1) {
        for (let x = column * block; x < (column + 1) * block; x += 1) {
          const value = field[y * width + x];
          if (Number.isFinite(value)) {
            total += value;
            count += 1;
          }
        }
      }
      // Un bloque a medias entre dominio y vacío se descarta: su media sería
      // un valor de frontera que inventaría curvatura donde no hay campo.
      output[row * cols + column] = count > block * block * 0.6 ? total / count : NaN;
    }
  }
  return { field: output, width: cols, height: rows };
}

function derivatives(field, width, height, index) {
  const x = index % width;
  const y = Math.floor(index / width);
  if (x < 1 || y < 1 || x >= width - 1 || y >= height - 1) return null;
  const at = (dx, dy) => field[(y + dy) * width + (x + dx)];
  const values = [
    at(-1, -1), at(0, -1), at(1, -1),
    at(-1, 0), at(0, 0), at(1, 0),
    at(-1, 1), at(0, 1), at(1, 1)
  ];
  if (values.some((value) => !Number.isFinite(value))) return null;
  // El eje y de la rejilla baja hacia el sur; se le da la vuelta para que las
  // derivadas salgan en un sistema con el norte arriba y el signo de la
  // curvatura ciclónica sea el del hemisferio.
  const zx = (at(1, 0) - at(-1, 0)) / 2;
  const zy = -(at(0, 1) - at(0, -1)) / 2;
  const zxx = at(1, 0) - 2 * at(0, 0) + at(-1, 0);
  const zyy = at(0, 1) - 2 * at(0, 0) + at(0, -1);
  const zxy = -(at(1, 1) - at(-1, 1) - at(1, -1) + at(-1, -1)) / 4;
  return { zx, zy, zxx, zyy, zxy };
}

/**
 * Curvatura de la isohipsa que pasa por cada punto, en 1/celda.
 *
 * Es la curvatura de la línea de nivel de un campo escalar. Positiva donde la
 * isohipsa se cierra alrededor de alturas menores, que es la curvatura
 * ciclónica del hemisferio norte y la que dibuja una vaguada.
 */
export function contourCurvature(field, width, height, minGradient = 0) {
  const curvature = new Float32Array(field.length).fill(NaN);
  for (let index = 0; index < field.length; index += 1) {
    const d = derivatives(field, width, height, index);
    if (!d) continue;
    const gradient = Math.hypot(d.zx, d.zy);
    // La fórmula divide por el cubo del gradiente: donde el campo está plano
    // se dispara y una ondulación de un dam sale con la curvatura de una
    // vaguada. Sin flujo que curvar, no hay eje que dibujar.
    if (gradient < Math.max(1e-6, minGradient)) continue;
    curvature[index] = (
      d.zxx * d.zy * d.zy - 2 * d.zxy * d.zx * d.zy + d.zyy * d.zx * d.zx
    ) / (gradient ** 3);
  }
  return curvature;
}

/**
 * Curvatura por intensidad del flujo: κ·|∇Z|.
 *
 * Es el término de curvatura de la vorticidad geostrófica, y es lo que
 * distingue una vaguada de un recodo cualquiera: el mismo giro en una corriente
 * fuerte pesa, y en un campo parado no. Además se porta bien donde el gradiente
 * se anula, que es justo donde la curvatura sola no vale nada.
 */
export function curvatureVorticity(field, width, height, minGradient = 0) {
  const curvature = contourCurvature(field, width, height, minGradient);
  const output = new Float32Array(field.length).fill(NaN);
  for (let index = 0; index < field.length; index += 1) {
    if (!Number.isFinite(curvature[index])) continue;
    const d = derivatives(field, width, height, index);
    if (!d) continue;
    output[index] = curvature[index] * Math.hypot(d.zx, d.zy);
  }
  return output;
}

/** Laplaciana de Z: el signo de la vorticidad geostrófica. */
export function geostrophicVorticity(field, width, height) {
  const vorticity = new Float32Array(field.length).fill(NaN);
  for (let index = 0; index < field.length; index += 1) {
    const d = derivatives(field, width, height, index);
    if (d) vorticity[index] = d.zxx + d.zyy;
  }
  return vorticity;
}

/** Gradiente típico del campo, para llevar la curvatura a la misma escala. */
function gradientScale(field, width, height) {
  const valores = [];
  for (let index = 0; index < field.length; index += 40) {
    const d = derivatives(field, width, height, index);
    if (d) valores.push(Math.hypot(d.zx, d.zy));
  }
  if (!valores.length) return 0;
  valores.sort((a, b) => a - b);
  return valores[Math.floor(valores.length * 0.5)];
}

function sample(field, width, height, x, y) {
  const column = Math.round(x);
  const row = Math.round(y);
  if (column < 0 || row < 0 || column >= width || row >= height) return NaN;
  return field[row * width + column];
}

/**
 * Cuánto baja la isohipsa en un punto respecto a lo que hace a los lados.
 *
 * Positiva cuando la línea de nivel se descuelga hacia el ecuador, que es lo
 * que hace una vaguada. En el hombro de una dorsal sale negativa o cerca de
 * cero, aunque la curvatura allí sea ciclónica.
 */
export function waveAmplitude(field, width, height, point, spanCells, minSpanCells = 0) {
  const column = Math.round(point.x);
  const row = Math.round(point.y);
  if (column < 0 || row < 0 || column >= width || row >= height) return null;
  const level = field[row * width + column];
  if (!Number.isFinite(level)) return null;

  const crossing = (x) => {
    if (x < 0 || x >= width) return null;
    let mejor = null;
    for (let y = 0; y + 1 < height; y += 1) {
      const arriba = field[y * width + x];
      const abajo = field[(y + 1) * width + x];
      if (!Number.isFinite(arriba) || !Number.isFinite(abajo)) continue;
      if ((arriba - level) * (abajo - level) > 0) continue;
      const cruce = y + (level - arriba) / ((abajo - arriba) || 1e-9);
      if (mejor === null || Math.abs(cruce - row) < Math.abs(mejor - row)) mejor = cruce;
    }
    return mejor;
  };

  const centro = crossing(column);
  if (centro === null) return null;

  // El dominio de AROME es estrecho para una onda sinóptica y su borde va
  // inclinado, así que a 500 km al oeste de una vaguada atlántica no hay
  // campo. En vez de renunciar, la ventana se encoge por los dos lados a la
  // vez hasta encontrar dos cruces válidos, y se devuelve con cuánto se ha
  // podido medir para que el umbral se ajuste a eso.
  for (let span = spanCells; span >= minSpanCells && span > 0; span -= 1) {
    const oeste = crossing(column - span);
    const este = crossing(column + span);
    if (oeste === null || este === null) continue;
    // Contra el lado menos favorable, no contra la media de los dos: en el eje
    // de una vaguada la isohipsa está al sur por los dos lados. Con la media
    // bastaba con que un lado quedara al norte, y entraba el flanco oriental
    // de una dorsal. Nunca un solo lado, por lo mismo.
    return { amplitude: centro - Math.max(oeste, este), span };
  }
  return null;
}

/**
 * ¿Hay una isohipsa cerrada alrededor de este mínimo?
 *
 * Un mínimo local no basta: una vaguada abierta también los tiene, y
 * tomarlo por una baja partía el eje justo por su parte más marcada. La
 * comprobación es topológica: se inunda desde el mínimo hasta `delta` por
 * encima de su valor y se mira si la mancha queda contenida. Si toca el borde
 * del dominio o un hueco sin dato, la isohipsa no se cierra dentro del mapa y
 * no hay baja que dibujar.
 */
export function hasClosedContour(field, width, height, start, delta, maxCells = 4000) {
  const inicio = start.y * width + start.x;
  const nivel = field[inicio] + delta;
  const vistos = new Set([inicio]);
  const cola = [inicio];
  while (cola.length) {
    if (vistos.size > maxCells) return false;
    const actual = cola.pop();
    const x = actual % width;
    const y = Math.floor(actual / width);
    if (x === 0 || y === 0 || x === width - 1 || y === height - 1) return false;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const vecino = (y + dy) * width + (x + dx);
      if (vistos.has(vecino)) continue;
      const valor = field[vecino];
      // Un hueco sin dato es tan concluyente como el borde: por ahí la
      // isohipsa se escapa del mapa.
      if (!Number.isFinite(valor)) return false;
      if (valor >= nivel) continue;
      vistos.add(vecino);
      cola.push(vecino);
    }
  }
  return true;
}

/** Mínimos locales con isohipsa cerrada: centros de depresión. */
export function closedLows(field, width, height, radius, delta = 2) {
  const centres = [];
  for (let row = radius; row < height - radius; row += 1) {
    for (let column = radius; column < width - radius; column += 1) {
      const value = field[row * width + column];
      if (!Number.isFinite(value)) continue;
      let esMinimo = true;
      for (let dy = -radius; dy <= radius && esMinimo; dy += 1) {
        for (let dx = -radius; dx <= radius; dx += 1) {
          if (dx === 0 && dy === 0) continue;
          const vecino = field[(row + dy) * width + (column + dx)];
          if (Number.isFinite(vecino) && vecino < value) {
            esMinimo = false;
            break;
          }
        }
      }
      if (!esMinimo) continue;
      if (!hasClosedContour(field, width, height, { x: column, y: row }, delta)) continue;
      centres.push({ x: column, y: row, value });
    }
  }
  return centres;
}

/**
 * Máximos a lo largo de una isohipsa, uno por onda.
 *
 * Un campo suave sigue teniendo pequeños vaivenes, así que quedarse con todos
 * los máximos locales llena la línea de candidatos y el encadenado acaba
 * saltando de uno a otro. Se recorre la línea con una ventana del tamaño de la
 * separación entre vaguadas y solo sobrevive el mayor de cada ventana.
 */
function curvaturePeaks(points, strength, width, height, threshold, spacing) {
  const values = points.map(([x, y]) => sample(strength, width, height, x, y));
  const peaks = [];
  let mejor = -1;
  const cerrarVentana = () => {
    if (mejor >= 0) {
      peaks.push({ x: points[mejor][0], y: points[mejor][1], strength: values[mejor] });
    }
    mejor = -1;
  };
  let recorrido = 0;
  for (let index = 1; index < points.length - 1; index += 1) {
    recorrido += Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1]
    );
    if (recorrido >= spacing) {
      cerrarVentana();
      recorrido = 0;
    }
    const value = values[index];
    if (!Number.isFinite(value) || value < threshold) continue;
    if (!(value >= values[index - 1]) || !(value > values[index + 1])) continue;
    if (mejor < 0 || value > values[mejor]) mejor = index;
  }
  cerrarVentana();
  return peaks;
}

/** Umbral adaptado al caso: percentil de lo que de verdad hay en el campo. */
function strengthThreshold(strength, percentile) {
  const positivos = [];
  for (let index = 0; index < strength.length; index += 1) {
    const value = strength[index];
    if (Number.isFinite(value) && value > 0) positivos.push(value);
  }
  if (!positivos.length) return Infinity;
  positivos.sort((a, b) => a - b);
  return positivos[Math.min(positivos.length - 1, Math.floor(positivos.length * percentile))];
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Encadena los picos de isohipsas contiguas en ejes.
 *
 * Un eje de vaguada es la fila de puntos de máxima curvatura que se va
 * repitiendo isohipsa tras isohipsa. Se recorre de la más baja a la más alta y
 * cada punto busca continuación en la siguiente, dentro de un radio: así el
 * eje sale ordenado y no como una nube de puntos sueltos.
 */
export function chainAxes(peaksByLevel, maxGap, {
  maxTurnDeg = TROUGH_MAX_TURN_DEG,
  maxDriftDeg = TROUGH_MAX_DRIFT_DEG,
  gradientAt = null
} = {}) {
  const cosGiro = Math.cos((maxTurnDeg * Math.PI) / 180);
  const cosDeriva = Math.cos((maxDriftDeg * Math.PI) / 180);

  /**
   * Un tramo del eje vale si cruza las isohipsas y no tuerce de golpe.
   *
   * Cruzar las isohipsas es ir con el gradiente, no a lo largo de la línea de
   * nivel; y un eje de vaguada no gira noventa grados de una isohipsa a la
   * siguiente. Sin estas dos condiciones, la cadena saltaba al vértice de otra
   * onda y dibujaba una L.
   */
  const admisible = (desde, hasta, direccionPrevia) => {
    const paso = unit(desde, hasta);
    if (direccionPrevia && cosine(paso, direccionPrevia) < cosGiro) return false;
    if (gradientAt) {
      const g = gradientAt((desde.x + hasta.x) / 2, (desde.y + hasta.y) / 2);
      if (g && Math.abs(cosine(paso, g)) < cosDeriva) return false;
    }
    return true;
  };

  const axes = [];
  const usados = peaksByLevel.map(() => new Set());
  // Las cadenas se siembran por los picos más marcados, no por orden de
  // barrido. Con el orden de barrido, un pico flojo podía quedarse con el
  // punto que le tocaba al fuerte y el resultado dejaba de ser monótono: bajar
  // el umbral hacía aparecer candidatos que rompían un eje que ya estaba bien,
  // y una vaguada que llevaba horas ahí parpadeaba de una hora a otra.
  const semillas = [];
  peaksByLevel.forEach((picos, nivel) => {
    picos.forEach((pico, indice) => semillas.push({ nivel, indice, fuerza: pico.strength ?? 0 }));
  });
  semillas.sort((izquierda, derecha) => derecha.fuerza - izquierda.fuerza);

  // Desde la semilla se crece hacia las dos isohipsas vecinas, arriba y abajo.
  // Creciendo en un solo sentido, un eje sembrado por su pico más marcado
  // —que suele caer a media altura— se partía en dos cadenas cortas y las dos
  // se quedaban sin llegar a la longitud mínima.
  const crecer = (desdeNivel, desdePunto, paso) => {
    const tramo = [];
    let nivel = desdeNivel;
    let actual = desdePunto;
    let direccion = null;
    while (nivel + paso >= 0 && nivel + paso < peaksByLevel.length) {
      const siguiente = peaksByLevel[nivel + paso];
      let mejor = -1;
      let mejorDistancia = maxGap;
      for (let candidato = 0; candidato < siguiente.length; candidato += 1) {
        if (usados[nivel + paso].has(candidato)) continue;
        const separacion = distance(actual, siguiente[candidato]);
        if (separacion >= mejorDistancia) continue;
        if (!admisible(actual, siguiente[candidato], direccion)) continue;
        mejorDistancia = separacion;
        mejor = candidato;
      }
      // Sin vértice fiable el eje termina aquí: no se prolonga de lado con lo
      // primero que quede a mano.
      if (mejor < 0) break;
      usados[nivel + paso].add(mejor);
      siguiente[mejor].nivel = nivel + paso;
      const ultimo = unit(actual, siguiente[mejor]);
      // Rumbo acumulado: pesa lo andado y deja que la curva gire poco a poco.
      direccion = direccion
        ? unit({ x: 0, y: 0 }, {
            x: direccion.x * 0.6 + ultimo.x * 0.4,
            y: direccion.y * 0.6 + ultimo.y * 0.4
          })
        : ultimo;
      actual = siguiente[mejor];
      tramo.push(actual);
      nivel += paso;
    }
    return tramo;
  };

  for (const { nivel, indice } of semillas) {
    if (usados[nivel].has(indice)) continue;
    const semilla = peaksByLevel[nivel][indice];
    semilla.nivel = nivel;
    usados[nivel].add(indice);
    const eje = [
      ...crecer(nivel, semilla, -1).reverse(),
      semilla,
      ...crecer(nivel, semilla, 1)
    ];
    if (eje.length >= 2) axes.push(eje);
  }
  return axes;
}

function unit(from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const norm = Math.hypot(dx, dy) || 1e-9;
  return { x: dx / norm, y: dy / norm };
}

/** Coseno del ángulo entre dos direcciones ya normalizadas. */
function cosine(a, b) {
  return a.x * b.x + a.y * b.y;
}

/**
 * Recta de mejor ajuste por componentes principales.
 *
 * Con dos o tres vértices fiables no hay curva que dibujar: lo honrado es la
 * recta que mejor los representa, extendida solo hasta donde llegan.
 */
export function pcaLine(points) {
  const media = points.reduce(
    (suma, punto) => ({ x: suma.x + punto.x / points.length, y: suma.y + punto.y / points.length }),
    { x: 0, y: 0 }
  );
  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  for (const punto of points) {
    const dx = punto.x - media.x;
    const dy = punto.y - media.y;
    sxx += dx * dx;
    sxy += dx * dy;
    syy += dy * dy;
  }
  // Autovector mayor de la covarianza 2x2, en forma cerrada.
  const traza = sxx + syy;
  const determinante = sxx * syy - sxy * sxy;
  const lambda = traza / 2 + Math.sqrt(Math.max(0, (traza * traza) / 4 - determinante));
  const direccion = Math.abs(sxy) > 1e-9
    ? unit({ x: 0, y: 0 }, { x: lambda - syy, y: sxy })
    : (sxx >= syy ? { x: 1, y: 0 } : { x: 0, y: 1 });
  const proyecciones = points.map(
    (punto) => (punto.x - media.x) * direccion.x + (punto.y - media.y) * direccion.y
  );
  const desde = Math.min(...proyecciones);
  const hasta = Math.max(...proyecciones);
  return [
    { x: media.x + direccion.x * desde, y: media.y + direccion.y * desde },
    { x: media.x + direccion.x * hasta, y: media.y + direccion.y * hasta }
  ];
}

/**
 * Suavizado que conserva los extremos y no inventa recorrido.
 *
 * Una media móvil de tres puntos, repetida: basta para que la línea deje de
 * quebrarse de vértice en vértice sin desplazarla de donde están los datos.
 */
export function smoothAxis(points, passes = 2) {
  let actual = points;
  for (let vuelta = 0; vuelta < passes; vuelta += 1) {
    const suave = [actual[0]];
    for (let index = 1; index < actual.length - 1; index += 1) {
      suave.push({
        x: (actual[index - 1].x + 2 * actual[index].x + actual[index + 1].x) / 4,
        y: (actual[index - 1].y + 2 * actual[index].y + actual[index + 1].y) / 4
      });
    }
    suave.push(actual[actual.length - 1]);
    actual = suave;
  }
  return actual;
}

/** Rumbo dominante de una cadena, por componentes principales. */
function heading(points) {
  const [desde, hasta] = pcaLine(points);
  return unit(desde, hasta);
}

/**
 * Une las cadenas que son el mismo eje partido en dos.
 *
 * El reparto de picos es codicioso: dos cadenas pueden repartirse las
 * isohipsas de una misma vaguada y quedarse las dos por debajo de la longitud
 * mínima. Se unen las que se tocan y llevan el mismo rumbo, y del resultado se
 * conserva un solo vértice por isohipsa —el más marcado—, que es lo que evita
 * que el eje vuelva sobre sus pasos.
 */
export function mergeChains(chains, { gapCells, maxTurnDeg = TROUGH_MAX_TURN_DEG }) {
  const cosGiro = Math.cos((maxTurnDeg * Math.PI) / 180);
  const pendientes = [...chains];
  const unidas = [];
  while (pendientes.length) {
    let actual = pendientes.shift();
    let creció = true;
    while (creció) {
      creció = false;
      for (let index = 0; index < pendientes.length; index += 1) {
        const otra = pendientes[index];
        const cerca = actual.some(
          (punto) => otra.some((vecino) => distance(punto, vecino) <= gapCells)
        );
        if (!cerca) continue;
        if (Math.abs(cosine(heading(actual), heading(otra))) < cosGiro) continue;
        pendientes.splice(index, 1);
        actual = actual.concat(otra);
        creció = true;
        break;
      }
    }
    // Un vértice por isohipsa: si dos cadenas aportaban el mismo nivel, se
    // queda el pico más marcado.
    const porNivel = new Map();
    for (const punto of actual) {
      const previo = porNivel.get(punto.nivel);
      if (!previo || (punto.strength ?? 0) > (previo.strength ?? 0)) {
        porNivel.set(punto.nivel, punto);
      }
    }
    unidas.push([...porNivel.values()].sort((izquierda, derecha) => izquierda.nivel - derecha.nivel));
  }
  return unidas;
}

function axisLength(axis) {
  let total = 0;
  for (let index = 0; index < axis.length - 1; index += 1) {
    total += distance(axis[index], axis[index + 1]);
  }
  return total;
}

/**
 * Detecta los ejes de vaguada de un campo de geopotencial.
 *
 * Devuelve los ejes en coordenadas de la rejilla original y, aparte, los
 * centros de depresión cerrada: son fenómenos distintos y en un mapa se
 * dibujan distinto, aunque los dos nazcan de la misma curvatura ciclónica.
 */
export function troughAxes(field, {
  width,
  height,
  cellKm = 2.5,
  block = TROUGH_BLOCK,
  sigmaKm = TROUGH_SIGMA_KM,
  minLengthKm = TROUGH_MIN_LENGTH_KM,
  minCurvature = TROUGH_MIN_CURVATURE,
  minGradient = TROUGH_MIN_GRADIENT,
  peakSpacingKm = TROUGH_PEAK_SPACING_KM,
  percentile = TROUGH_PERCENTILE,
  mergeKm = TROUGH_MERGE_KM,
  maxTurnDeg = TROUGH_MAX_TURN_DEG,
  maxDriftDeg = TROUGH_MAX_DRIFT_DEG,
  minAmplitudeKm = TROUGH_MIN_AMPLITUDE_KM,
  amplitudeSpanKm = TROUGH_AMPLITUDE_SPAN_KM,
  minSpanKm = TROUGH_MIN_SPAN_KM,
  edgeMinLengthKm = TROUGH_EDGE_MIN_LENGTH_KM,
  edgeMinPoints = TROUGH_EDGE_MIN_POINTS,
  levelStep = 2
} = {}) {
  if (!field) return { axes: [], lows: [] };
  const grueso = coarsen(field, width, height, block);
  if (grueso.width < 8 || grueso.height < 8) return { axes: [], lows: [] };
  const kmPorCelda = cellKm * block;

  // 1. Escala sinóptica.
  const suave = gaussianBlur(
    grueso.field, grueso.width, grueso.height, sigmaKm / kmPorCelda
  );

  // 2. Curvatura de las isohipsas por la intensidad del flujo.
  const fuerza = curvatureVorticity(
    suave, grueso.width, grueso.height, minGradient * kmPorCelda
  );
  // El umbral se adapta al caso: en un mapa plano no hay nada que marcar, y en
  // uno con una vaguada profunda no tiene sentido señalar cada ondulación.
  const umbral = Math.max(
    strengthThreshold(fuerza, percentile),
    minCurvature * kmPorCelda * gradientScale(suave, grueso.width, grueso.height)
  );

  // 6a. Las depresiones cerradas se localizan antes de encadenar, para poder
  // apartar de los ejes los picos que caen dentro de ellas.
  const radioCierre = Math.max(2, Math.round(150 / kmPorCelda));
  const lows = closedLows(suave, grueso.width, grueso.height, radioCierre, levelStep);

  // 3. Picos de curvatura sobre cada isohipsa.
  const niveles = stepLevels(suave, levelStep);
  const peaksByLevel = [];
  for (const nivel of niveles) {
    const lineas = contourLines(suave, {
      width: grueso.width,
      height: grueso.height,
      levels: [nivel],
      sigma: 0,
      minRingArea: 0,
      tolerance: 0,
      labelMinLength: Infinity
    });
    const picos = [];
    for (const linea of lineas) {
      // `contourLines` devuelve el trazo montado; los vértices se recuperan de
      // él, que es la misma geometría ya limpia de anillos sueltos.
      for (const tramo of linea.path.split('M').slice(1)) {
        const points = tramo.split('L').map((par) => par.split(',').map(Number));
        if (points.length < 5) continue;
        picos.push(...curvaturePeaks(
          points, fuerza, grueso.width, grueso.height,
          umbral, peakSpacingKm / kmPorCelda
        ));
      }
    }
    peaksByLevel.push(picos);
  }

  // 4. Encadenado.
  const maxGap = Math.max(2, Math.round(200 / kmPorCelda));
  const gradientAt = (x, y) => {
    const columna = Math.round(x);
    const fila = Math.round(y);
    if (columna < 0 || fila < 0 || columna >= grueso.width || fila >= grueso.height) return null;
    const d = derivatives(suave, grueso.width, grueso.height, fila * grueso.width + columna);
    // `derivatives` devuelve el eje y hacia el norte; aquí se compara contra
    // pasos medidos en la rejilla, que crece hacia el sur.
    return d ? unit({ x: 0, y: 0 }, { x: d.zx, y: -d.zy }) : null;
  };
  const cadenas = chainAxes(peaksByLevel, maxGap, { gradientAt, maxTurnDeg, maxDriftDeg });
  const crudos = mergeChains(cadenas, {
    gapCells: Math.max(2, Math.round(mergeKm / kmPorCelda)),
    maxTurnDeg
  });

  // 5 y 6b. Poda por longitud y separación de lo que cae en una depresión.
  const minLength = minLengthKm / kmPorCelda;
  const dentroDeCierre = (punto) => lows.some(
    (centro) => distance(centro, punto) <= radioCierre
  );
  const spanCells = Math.max(2, Math.round(amplitudeSpanKm / kmPorCelda));
  const minSpanCells = Math.max(2, Math.round(minSpanKm / kmPorCelda));
  const minAmplitude = minAmplitudeKm / kmPorCelda;
  const edgeMinLength = edgeMinLengthKm / kmPorCelda;
  const vorticidad = geostrophicVorticity(suave, grueso.width, grueso.height);
  const supervivientes = [];
  for (const eje of crudos) {
    const abierto = eje.filter((punto) => !dentroDeCierre(punto));
    if (abierto.length < 2) continue;

    // La onda tiene que existir, no solo curvarse: sin esto entraban los
    // hombros de las dorsales, que curvan del lado ciclónico sin descolgar
    // nada hacia el sur.
    const medidas = abierto
      .map((punto) => waveAmplitude(
        suave, grueso.width, grueso.height, punto, spanCells, minSpanCells
      ))
      .filter((valor) => valor !== null);
    if (!medidas.length) continue;
    // El umbral se ajusta a lo que se ha podido medir: con 250 km de ventana
    // se le pide la mitad de amplitud que con 500. Medir menos exige menos,
    // no da barra libre.
    const razones = medidas
      .map(({ amplitude, span }) => amplitude / (minAmplitude * (span / spanCells)))
      .sort((izquierda, derecha) => izquierda - derecha);
    if (razones[Math.floor(razones.length / 2)] < 1) continue;

    const recortada = medidas.some(({ span }) => span < spanCells);
    if (!recortada) {
      if (axisLength(abierto) < minLength) continue;
    } else {
      // Medido contra media ventana, el eje tiene que ganarse el sitio por
      // otro lado: presencia en varias isohipsas, giro ciclónico de verdad,
      // rumbo coherente y un recorrido visible que no sea un retal.
      if (abierto.length < edgeMinPoints) continue;
      if (axisLength(abierto) < edgeMinLength) continue;
      const ciclonica = abierto.filter((punto) => {
        const valor = vorticidad[
          Math.round(punto.y) * grueso.width + Math.round(punto.x)
        ];
        return Number.isFinite(valor) && valor > 0;
      }).length;
      if (ciclonica < abierto.length / 2) continue;
      // Rumbo coherente: los vértices tienen que caer cerca de su propia
      // recta de ajuste, no describir una ese.
      const [desde, hasta] = pcaLine(abierto);
      const eje2 = unit(desde, hasta);
      const desvio = Math.max(...abierto.map((punto) => Math.abs(
        (punto.x - desde.x) * eje2.y - (punto.y - desde.y) * eje2.x
      )));
      if (desvio > maxGap) continue;
    }
    supervivientes.push(abierto);
  }

  // 5b. Una misma vaguada puede dar dos cadenas casi paralelas cuando sus
  // isohipsas se reparten los picos. Se queda la larga y se descarta la que va
  // pegada a ella, que es la rama corta del mismo eje y no otra vaguada.
  supervivientes.sort((izquierda, derecha) => axisLength(derecha) - axisLength(izquierda));
  const radioFusion = Math.max(2, Math.round(mergeKm / kmPorCelda));
  const elegidos = [];
  for (const eje of supervivientes) {
    const solapa = elegidos.some((previo) => {
      const cerca = eje.filter(
        (punto) => previo.some((otro) => distance(punto, otro) <= radioFusion)
      ).length;
      return cerca >= eje.length * 0.6;
    });
    if (!solapa) elegidos.push(eje);
  }

  // 7. Geometría final. Con dos o tres vértices fiables, la recta que mejor
  // los representa; con cuatro o más, la poligonal suavizada. En los dos casos
  // el eje empieza y acaba donde hay dato.
  const axes = elegidos.map((eje) => {
    const enRejilla = eje.map((punto) => ({
      x: punto.x * block + block / 2,
      y: punto.y * block + block / 2
    }));
    return enRejilla.length <= 3 ? pcaLine(enRejilla) : smoothAxis(enRejilla, 2);
  });

  return {
    axes,
    lows: lows.map((centro) => ({
      x: centro.x * block + block / 2,
      y: centro.y * block + block / 2,
      value: centro.value
    }))
  };
}

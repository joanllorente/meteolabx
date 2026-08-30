/**
 * Isolíneas presentables a partir de un campo de rejilla.
 *
 * Trazar el campo crudo sigue el color demasiado de cerca: aparecen anillos de
 * dos o tres celdas y dentaduras que ensucian el mapa sin decir nada. La cadena
 * es la de un mapa impreso:
 *
 *     campo → filtro gaussiano → marching squares → unir en polilíneas
 *           → tirar los anillos diminutos → simplificar → etiquetas
 *
 * Todo va en unidades de celda. Una celda de AROME 0,025° mide unos 2,8 km en
 * latitud y 2,0 en longitud a 45° N, así que ronda los 5,5 km².
 */

/** σ del suavizado, en celdas: unos 4 km. */
export const CONTOUR_SIGMA = 1.75;
/** Área mínima de un anillo cerrado, en celdas: unos 110 km². */
export const CONTOUR_MIN_RING_CELLS = 20;
/** Tolerancia de la simplificación, en celdas. */
export const CONTOUR_TOLERANCE = 0.8;
/** Recorrido mínimo para que una línea merezca etiqueta, en celdas. */
export const CONTOUR_LABEL_MIN_LENGTH = 70;

/**
 * Gaussiano separable que respeta los huecos.
 *
 * Fuera del dominio del modelo no hay dato, así que la convolución se
 * renormaliza con el peso de las muestras válidas: sin eso, el borde del
 * dominio arrastraría el campo hacia el vacío e inventaría isolíneas
 * pegadas al contorno. Una celda sin dato lo sigue estando después.
 */
export function gaussianBlur(field, width, height, sigma) {
  if (!(sigma > 0)) return field;
  // 2,5σ recoge el 99 % del peso: el tramo que queda fuera no mueve el campo
  // y cada tap de más son ochocientas mil multiplicaciones por pasada.
  const radius = Math.max(1, Math.ceil(sigma * 2.5));
  const kernel = new Float64Array(radius * 2 + 1);
  for (let offset = -radius; offset <= radius; offset += 1) {
    kernel[offset + radius] = Math.exp(-(offset * offset) / (2 * sigma * sigma));
  }

  const pass = (source, horizontal) => {
    const output = new Float32Array(source.length);
    for (let row = 0; row < height; row += 1) {
      for (let column = 0; column < width; column += 1) {
        const index = row * width + column;
        if (!Number.isFinite(source[index])) {
          output[index] = NaN;
          continue;
        }
        let total = 0;
        let weights = 0;
        for (let offset = -radius; offset <= radius; offset += 1) {
          const x = horizontal ? column + offset : column;
          const y = horizontal ? row : row + offset;
          if (x < 0 || y < 0 || x >= width || y >= height) continue;
          const value = source[y * width + x];
          if (!Number.isFinite(value)) continue;
          const weight = kernel[offset + radius];
          total += value * weight;
          weights += weight;
        }
        output[index] = weights > 0 ? total / weights : NaN;
      }
    }
    return output;
  };

  return pass(pass(field, true), false);
}

const MARCHING_CASES = {
  1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]],
  5: [[3, 0], [1, 2]], 6: [[0, 2]], 7: [[3, 2]], 8: [[2, 3]],
  9: [[0, 2]], 10: [[0, 1], [2, 3]], 11: [[1, 2]], 12: [[3, 1]],
  13: [[0, 1]], 14: [[3, 0]]
};

function interpolate(a, b, threshold) {
  return Math.max(0, Math.min(1, (threshold - a) / (b - a || 1e-6)));
}

/** Segmentos sueltos de un nivel, en orden de barrido. */
function marchingSegments(field, width, height, level) {
  const segments = [];
  for (let row = 0; row + 1 < height; row += 1) {
    for (let column = 0; column + 1 < width; column += 1) {
      const topLeft = field[row * width + column];
      const topRight = field[row * width + column + 1];
      const bottomLeft = field[(row + 1) * width + column];
      const bottomRight = field[(row + 1) * width + column + 1];
      if (!Number.isFinite(topLeft) || !Number.isFinite(topRight)
        || !Number.isFinite(bottomLeft) || !Number.isFinite(bottomRight)) continue;
      const code = (topLeft >= level ? 1 : 0)
        | (topRight >= level ? 2 : 0)
        | (bottomRight >= level ? 4 : 0)
        | (bottomLeft >= level ? 8 : 0);
      const cases = MARCHING_CASES[code];
      if (!cases) continue;
      const corners = [
        [column + interpolate(topLeft, topRight, level), row],
        [column + 1, row + interpolate(topRight, bottomRight, level)],
        [column + interpolate(bottomLeft, bottomRight, level), row + 1],
        [column, row + interpolate(topLeft, bottomLeft, level)]
      ];
      for (const [first, second] of cases) {
        segments.push([corners[first], corners[second]]);
      }
    }
  }
  return segments;
}

/**
 * Clave numérica de un extremo, para casar los tramos que se tocan.
 *
 * Los dos vértices de una arista compartida salen de la misma expresión sobre
 * los mismos dos valores, así que son idénticos bit a bit y cuantizarlos no
 * pierde uniones. Con claves de texto —dos `toFixed` por segmento, cerca de
 * ciento cuarenta mil por frame— el enlazado costaba más que el propio
 * trazado.
 */
function pointKey(point) {
  return Math.round(point[0] * 4096) * 8388608 + Math.round(point[1] * 4096);
}

/**
 * Encadena los segmentos sueltos en polilíneas y anillos.
 *
 * Marching squares devuelve trocitos independientes y sin una orientación
 * común: el mismo contorno aparece recorrido en un sentido en unas celdas y en
 * el contrario en otras. Por eso cada tramo se prueba por sus dos extremos y el
 * vecino se invierte si hace falta; encadenando solo cola con cabeza, una
 * isoterma de seiscientas celdas se quedaba en tres mil trozos de una.
 */
export function linkSegments(segments) {
  const open = new Map();
  const lines = new Set();

  const keysOf = (line) => [pointKey(line[0]), pointKey(line[line.length - 1])];
  const register = (line) => {
    const [head, tail] = keysOf(line);
    lines.add(line);
    if (head === tail) {
      line.closed = true;
      return;
    }
    open.set(head, line);
    open.set(tail, line);
  };
  const unregister = (line) => {
    const [head, tail] = keysOf(line);
    if (open.get(head) === line) open.delete(head);
    if (open.get(tail) === line) open.delete(tail);
    lines.delete(line);
  };
  const endingAt = (line, key) => (
    pointKey(line[line.length - 1]) === key ? line : [...line].reverse()
  );
  const startingAt = (line, key) => (
    pointKey(line[0]) === key ? line : [...line].reverse()
  );

  for (const [start, end] of segments) {
    if (start[0] === end[0] && start[1] === end[1]) continue;
    let line = [start, end];

    const headKey = pointKey(line[0]);
    const before = open.get(headKey);
    if (before) {
      unregister(before);
      const left = endingAt(before, headKey);
      left.pop();
      line = left.concat(line);
    }

    const tailKey = pointKey(line[line.length - 1]);
    const after = open.get(tailKey);
    if (after) {
      unregister(after);
      const right = startingAt(after, tailKey);
      right.shift();
      line = line.concat(right);
    }

    register(line);
  }

  return [...lines].map((points) => ({ points, closed: Boolean(points.closed) }));
}

/** Área con signo de un anillo, por la fórmula del cordón de zapato. */
export function ringArea(points) {
  let total = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[index + 1];
    total += x1 * y2 - x2 * y1;
  }
  return Math.abs(total) / 2;
}

export function polylineLength(points) {
  let total = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    total += Math.hypot(
      points[index + 1][0] - points[index][0],
      points[index + 1][1] - points[index][1]
    );
  }
  return total;
}

/**
 * Distancia de un punto al segmento, no a la recta que lo contiene.
 *
 * La diferencia solo importa cuando el pie de la perpendicular cae fuera del
 * segmento, y hay un caso en el que cae siempre: un anillo cerrado empieza y
 * acaba en el mismo punto, así que su segmento base mide cero. Con la fórmula
 * de la recta infinita el numerador se anula y todos los vértices quedaban a
 * distancia cero del «segmento», ninguno superaba la tolerancia y el anillo
 * entero se reducía a sus dos extremos coincidentes: un trazo de longitud
 * cero. Es lo que borraba del mapa la isobara que rodea a cada centro —la de
 * 1024 alrededor de un anticiclón de 1026, o las de 996 a 1008 alrededor de
 * una borrasca de 992—, que es justo la línea que dice de qué centro se trata.
 */
function distanceToSegment(x, y, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const largo = dx * dx + dy * dy;
  // Segmento degenerado: la distancia es al propio punto.
  const t = largo > 0
    ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / largo))
    : 0;
  return Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
}

/** Douglas-Peucker iterativo: la recursión desborda con miles de vértices. */
export function simplify(points, tolerance) {
  if (points.length < 3 || !(tolerance > 0)) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];
  while (stack.length) {
    const [first, last] = stack.pop();
    if (last <= first + 1) continue;
    const [x1, y1] = points[first];
    const [x2, y2] = points[last];
    let worst = 0;
    let worstIndex = -1;
    for (let index = first + 1; index < last; index += 1) {
      const [x, y] = points[index];
      const distance = distanceToSegment(x, y, x1, y1, x2, y2);
      if (distance > worst) {
        worst = distance;
        worstIndex = index;
      }
    }
    if (worst > tolerance && worstIndex > 0) {
      keep[worstIndex] = 1;
      stack.push([first, worstIndex], [worstIndex, last]);
    }
  }
  const result = [];
  for (let index = 0; index < points.length; index += 1) {
    if (keep[index]) result.push(points[index]);
  }
  return result;
}

/** Puntos repartidos por el recorrido de una línea, para las etiquetas. */
export function anchorsAlong(points, spacing) {
  const anchors = [];
  let walked = 0;
  let next = spacing / 2;
  for (let index = 0; index < points.length - 1; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[index + 1];
    const length = Math.hypot(x2 - x1, y2 - y1);
    while (walked + length >= next && length > 0) {
      const fraction = (next - walked) / length;
      anchors.push([x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction]);
      next += spacing;
    }
    walked += length;
  }
  return anchors;
}

/**
 * Traza la polilínea como curva, no como cadena de rectas.
 *
 * Marching squares deja los vértices sobre las aristas de las celdas, así que
 * una isolínea es una escalera de segmentos rectos. Con celdas de 2,5 km no se
 * nota; con las de 25 km de una rejilla global, la isobara sale con esquinas.
 *
 * Catmull-Rom pasada a Bézier, igual que los ejes de vaguada: la curva pasa por
 * todos los vértices —no se separa del dato en ninguno— y llega a cada uno con
 * la pendiente que marcan el anterior y el siguiente. En un anillo cerrado los
 * vecinos se buscan dando la vuelta, para que no quede un pico donde se cierra.
 */
function toPath(points, closed = false) {
  const punto = (index) => {
    if (closed) {
      // El último vértice repite el primero: el ciclo son los demás.
      const vueltas = points.length - 1;
      return points[((index % vueltas) + vueltas) % vueltas];
    }
    return points[Math.max(0, Math.min(points.length - 1, index))];
  };
  let path = `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`;
  if (points.length < 3) {
    for (let index = 1; index < points.length; index += 1) {
      path += `L${points[index][0].toFixed(2)},${points[index][1].toFixed(2)}`;
    }
    return path;
  }
  for (let index = 0; index < points.length - 1; index += 1) {
    const [x0, y0] = punto(index - 1);
    const [x1, y1] = punto(index);
    const [x2, y2] = punto(index + 1);
    const [x3, y3] = punto(index + 2);
    const c1x = x1 + (x2 - x0) / 6;
    const c1y = y1 + (y2 - y0) / 6;
    const c2x = x2 - (x3 - x1) / 6;
    const c2y = y2 - (y3 - y1) / 6;
    path += `C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)}`
      + ` ${x2.toFixed(2)},${y2.toFixed(2)}`;
  }
  return path;
}

/** Niveles múltiplos del paso que el campo cruza de verdad. */
export function stepLevels(field, step) {
  if (!field || !(step > 0)) return [];
  let low = Infinity;
  let high = -Infinity;
  for (let index = 0; index < field.length; index += 1) {
    const value = field[index];
    if (!Number.isFinite(value)) continue;
    if (value < low) low = value;
    if (value > high) high = value;
  }
  if (!Number.isFinite(low)) return [];
  const levels = [];
  for (let level = Math.ceil(low / step) * step; level <= high; level += step) {
    // El paso puede ser fraccionario: se redondea para que 0,1+0,2 no deje
    // niveles como 5,000000000000001 en las etiquetas.
    levels.push(Number(level.toFixed(6)));
  }
  return levels;
}

/**
 * La cadena completa: un trazo por nivel y sus candidatos a etiqueta.
 *
 * Devuelve el `path` ya montado y, aparte, los puntos donde cabe rótulo, que
 * solo salen de las líneas largas: una etiqueta sobre un anillo de tres celdas
 * ocupa más que el propio anillo. Los candidatos van juntos a propósito; quien
 * decide cuántos caben es la colocación, que sí conoce el zoom.
 */
export function contourLines(field, {
  width,
  height,
  levels,
  sigma = CONTOUR_SIGMA,
  minRingArea = CONTOUR_MIN_RING_CELLS,
  tolerance = CONTOUR_TOLERANCE,
  labelMinLength = CONTOUR_LABEL_MIN_LENGTH,
  labelSpacing = 60
} = {}) {
  if (!field || !levels?.length) return [];
  const smooth = gaussianBlur(field, width, height, sigma);
  const contours = [];
  for (const level of levels) {
    const lines = linkSegments(marchingSegments(smooth, width, height, level));
    const paths = [];
    const anchors = [];
    for (const line of lines) {
      // Los anillos diminutos son ruido de celda, no una estructura: fuera.
      if (line.closed && ringArea(line.points) < minRingArea) continue;
      const points = simplify(line.points, tolerance);
      if (points.length < 2) continue;
      const length = polylineLength(points);
      if (!line.closed && length < 2) continue;
      paths.push(toPath(points, line.closed));
      if (length >= labelMinLength) {
        for (const anchor of anchorsAlong(points, labelSpacing)) anchors.push(anchor);
      }
    }
    if (paths.length) contours.push({ level, path: paths.join(''), anchors });
  }
  return contours;
}

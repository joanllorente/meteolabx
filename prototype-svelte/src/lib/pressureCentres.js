/**
 * Bajas y anticiclones a partir de la presión al nivel del mar.
 *
 * El campo se suaviza solo para buscar los centros; las isobaras se trazan
 * aparte con el campo original, porque suavizarlas movería la línea y el mapa
 * dejaría de coincidir con el dato.
 *
 * El criterio no es «ser mínimo local»: en un campo de 2,5 km hay cientos, y
 * casi todos son ruido o un valle entre montañas. Un centro tiene que ganarle
 * al entorno por un margen de presión y estar suficientemente lejos de otro
 * del mismo signo.
 */

import { gaussianBlur } from './contours.js';

/** Lado del bloque de engrosado, en celdas de AROME. */
export const CENTRE_BLOCK = 4;
/** σ del suavizado, en km: solo para la detección. */
export const CENTRE_SIGMA_KM = 40;
/** Radio en el que un centro tiene que ser el extremo, en km. */
export const CENTRE_RADIUS_KM = 200;
/** Prominencia mínima para dibujar un centro, en hPa. */
export const CENTRE_PROMINENCE_HPA = 2;
/** Cierre mínimo para que un centro sea principal, en hPa. */
export const CENTRE_MAIN_DEPTH_HPA = 4;
/** Radio equivalente mínimo de un centro principal, en km. */
export const CENTRE_MAIN_RADIUS_KM = 150;
/**
 * Separación mínima entre dos centros del mismo tipo, en km.
 *
 * Va al tope del rango razonable porque la distancia se calcula con una celda
 * media de 2,5 km, y a 52° N una celda de longitud mide dos. Dos centros de la
 * misma borrasca separados 205 km reales salían como 280 calculados y se
 * dibujaban por duplicado.
 */
export const CENTRE_SEPARATION_KM = 300;

function coarsen(field, width, height, block) {
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
      output[row * cols + column] = count > block * block * 0.6 ? total / count : NaN;
    }
  }
  return { field: output, width: cols, height: rows };
}

/** Montón binario mínimo, lo justo para la inundación. */
function makeHeap() {
  const items = [];
  const swap = (a, b) => { const t = items[a]; items[a] = items[b]; items[b] = t; };
  return {
    get size() { return items.length; },
    push(value, index) {
      items.push([value, index]);
      let hijo = items.length - 1;
      while (hijo > 0) {
        const padre = (hijo - 1) >> 1;
        if (items[padre][0] <= items[hijo][0]) break;
        swap(padre, hijo);
        hijo = padre;
      }
    },
    pop() {
      const raiz = items[0];
      const ultimo = items.pop();
      if (items.length) {
        items[0] = ultimo;
        let padre = 0;
        for (;;) {
          const izquierda = padre * 2 + 1;
          const derecha = izquierda + 1;
          let menor = padre;
          if (izquierda < items.length && items[izquierda][0] < items[menor][0]) menor = izquierda;
          if (derecha < items.length && items[derecha][0] < items[menor][0]) menor = derecha;
          if (menor === padre) break;
          swap(padre, menor);
          padre = menor;
        }
      }
      return raiz;
    }
  };
}

/**
 * Cierre de un centro: hasta qué isobara sigue siendo suyo el terreno.
 *
 * Es la definición de manual —Δp entre el centro y el collado por el que se
 * derrama o se une a otro sistema más intenso— resuelta por inundación: se
 * expande siempre por la celda más baja disponible y se para cuando el agua
 * alcanza terreno más hondo que el propio centro, es decir cuando ha llegado a
 * la cuenca de otro más profundo, o cuando se escapa por el borde del mapa.
 *
 * El anillo fijo que había antes no medía esto: comparaba contra el sector que
 * más favorecía al candidato, así que un mínimo pegado a una vaguada salía tan
 * cerrado como una borrasca redonda.
 *
 * Trabaja siempre en clave de mínimo: para un anticiclón se le da la vuelta al
 * campo con `sign` y el mismo recorrido sirve.
 */
export function closureDepth(field, width, height, start, sign, maxCells = 30000) {
  // Siempre en clave de mínimo: `sign` llega con el criterio del recorrido
  // exterior —-1 para bajas, +1 para anticiclones— y aquí se le da la vuelta,
  // porque lo que se inunda es una cuenca y una baja ya lo es. Sin el cambio
  // de signo, una borrasca entraba como máximo y se derramaba en la primera
  // celda con profundidad cero.
  const nivelDe = (index) => -sign * field[index];
  const inicio = start.y * width + start.x;
  const base = nivelDe(inicio);
  const vistos = new Uint8Array(field.length);
  const monton = makeHeap();
  vistos[inicio] = 1;
  monton.push(base, inicio);
  let celdas = 0;
  let nivel = base;

  while (monton.size) {
    const [valor, index] = monton.pop();
    nivel = valor;
    celdas += 1;
    if (celdas > maxCells) break;
    const x = index % width;
    const y = (index - x) / width;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx;
      const ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) {
        // Se escapa del dominio: lo que haya más allá no se puede juzgar.
        return { depth: nivel - base, cells: celdas, open: true };
      }
      const vecino = ny * width + nx;
      const valorVecino = nivelDe(vecino);
      if (!Number.isFinite(valorVecino)) {
        return { depth: nivel - base, cells: celdas, open: true };
      }
      if (valorVecino < base) {
        // Terreno más hondo que el centro: aquí se une con otro más intenso.
        return { depth: nivel - base, cells: celdas, open: false };
      }
      if (vistos[vecino]) continue;
      vistos[vecino] = 1;
      monton.push(valorVecino, vecino);
    }
  }
  return { depth: nivel - base, cells: celdas, open: true };
}

/**
 * Centros de presión de un campo, ordenados de más a menos marcados.
 *
 * `sign` vale -1 para bajas y +1 para anticiclones, de modo que el mismo
 * recorrido sirve para los dos. Cada centro sale marcado como principal o
 * relativo según lo que cierre y lo que ocupe: A y B para los primeros, a y b
 * para los segundos, que es la convención de los mapas de AEMET.
 */
export function pressureCentres(field, {
  width,
  height,
  cellKm = 2.5,
  block = CENTRE_BLOCK,
  sigmaKm = CENTRE_SIGMA_KM,
  radiusKm = CENTRE_RADIUS_KM,
  prominenceHpa = CENTRE_PROMINENCE_HPA,
  mainDepthHpa = CENTRE_MAIN_DEPTH_HPA,
  mainRadiusKm = CENTRE_MAIN_RADIUS_KM,
  separationKm = CENTRE_SEPARATION_KM
} = {}) {
  if (!field) return [];
  const grueso = coarsen(field, width, height, block);
  const kmPorCelda = cellKm * block;
  const suave = gaussianBlur(grueso.field, grueso.width, grueso.height, sigmaKm / kmPorCelda);
  const radio = Math.max(2, Math.round(radiusKm / kmPorCelda));
  const separacion = separationKm / kmPorCelda;

  const candidatos = [];
  for (const [tipo, sign] of [['low', -1], ['high', 1]]) {
    for (let row = 0; row < grueso.height; row += 1) {
      for (let column = 0; column < grueso.width; column += 1) {
        const value = suave[row * grueso.width + column];
        if (!Number.isFinite(value)) continue;

        // Extremo dentro del radio: nadie le gana en su vecindario.
        let esExtremo = true;
        for (let dy = -radio; dy <= radio && esExtremo; dy += 1) {
          for (let dx = -radio; dx <= radio; dx += 1) {
            if (dx === 0 && dy === 0) continue;
            if (dx * dx + dy * dy > radio * radio) continue;
            const vecino = suave[(row + dy) * grueso.width + (column + dx)];
            if (!Number.isFinite(vecino)) continue;
            if (sign * vecino > sign * value) {
              esExtremo = false;
              break;
            }
          }
        }
        if (!esExtremo) continue;

        candidatos.push({ type: tipo, sign, x: column, y: row, value });
      }
    }
  }

  // Se agrupa antes de medir. Una meseta anticiclónica tiene varios máximos a
  // décimas unos de otros, y midiéndolos por separado se anulan entre sí: cada
  // uno encuentra a su vecino como terreno más alto y sale con dos décimas de
  // cierre. Agrupados, el representante mide contra lo que hay de verdad
  // alrededor de la meseta.
  candidatos.sort((izquierda, derecha) => (
    izquierda.sign * derecha.value - izquierda.sign * izquierda.value
  ));
  const elegidos = [];
  for (const candidato of candidatos) {
    const repetido = elegidos.some(
      (previo) => previo.type === candidato.type
        && Math.hypot(previo.x - candidato.x, previo.y - candidato.y) < separacion
    );
    if (!repetido) elegidos.push(candidato);
  }

  const centros = [];
  for (const centro of elegidos) {
    const cierre = closureDepth(
      suave, grueso.width, grueso.height, { x: centro.x, y: centro.y }, centro.sign
    );
    if (cierre.depth < prominenceHpa) continue;
    // Radio equivalente de lo que tiene cerrado: una borrasca de manual abarca
    // cientos de kilómetros y un mínimo encajado en una vaguada, cuatro celdas.
    const radiusKm = Math.sqrt((cierre.cells * kmPorCelda * kmPorCelda) / Math.PI);
    centros.push({
      type: centro.type,
      // Principal el que cierra de verdad y ocupa lo que debe; relativo el
      // resto, en minúscula, como en los mapas de AEMET.
      main: cierre.depth >= mainDepthHpa && radiusKm >= mainRadiusKm,
      x: centro.x * block + block / 2,
      y: centro.y * block + block / 2,
      // El valor se lee del campo suavizado a propósito: el crudo tiene ruido
      // de celda y la etiqueta bailaría un hectopascal de una hora a otra.
      value: centro.value,
      prominence: cierre.depth,
      radiusKm,
      open: cierre.open
    });
  }
  return centros.sort((izquierda, derecha) => derecha.prominence - izquierda.prominence);
}

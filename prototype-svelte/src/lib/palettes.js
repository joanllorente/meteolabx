/**
 * Paletas del visor y muestreo compartido.
 *
 * El ráster y la leyenda tienen que sacar los colores del mismo sitio y con la
 * misma interpolación: si cada uno se los calcula por su cuenta, una escala por
 * clases acaba enseñando en la barra un color que el mapa no usa.
 */

export const LUT_SIZE = 256;

export const defaultPalette = [
  '#3b4cc0', '#3288bd', '#66c2a5', '#abdda4', '#e6f598',
  '#fee08b', '#fdae61', '#f46d43', '#d73027', '#762a83'
];

export const precipitationPalette = [
  '#28465f', '#2f6f8e', '#369aa1', '#58bd91', '#9bd275',
  '#d7dc69', '#f2c55a', '#ed914c', '#df6262', '#b44f88'
];

function channels(hex) {
  return [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
}

/** Color de la rampa en una posición de 0 a LUT_SIZE-1. */
export function paletteStop(palette, position) {
  const escala = position / (LUT_SIZE - 1) * (palette.length - 1);
  const lower = Math.floor(escala);
  const upper = Math.min(lower + 1, palette.length - 1);
  const fraction = escala - lower;
  const a = channels(palette[lower]);
  const b = channels(palette[upper]);
  return [
    Math.round(a[0] + (b[0] - a[0]) * fraction),
    Math.round(a[1] + (b[1] - a[1]) * fraction),
    Math.round(a[2] + (b[2] - a[2]) * fraction)
  ];
}

/** Posición en la rampa de la clase `index` de `count`. */
export function bandPosition(index, count) {
  return Math.round(index / Math.max(1, count - 1) * (LUT_SIZE - 1));
}

/** Un color CSS por clase, para la leyenda. */
export function bandHexColors(palette, count) {
  return Array.from({ length: count }, (_, index) => {
    const [red, green, blue] = paletteStop(palette, bandPosition(index, count));
    return `rgb(${red} ${green} ${blue})`;
  });
}

/**
 * Clase a la que cae un valor: el número de umbrales que ya ha superado.
 *
 * Los umbrales son el borde inferior de cada clase, así que un valor igual a un
 * corte entra en la clase de arriba: 2 mm es la clase 2-5, no la 1-2.
 */
export function bandOfValue(value, breaks) {
  for (let index = 0; index < breaks.length; index += 1) {
    if (value < breaks[index]) return index;
  }
  return breaks.length;
}

/**
 * Posición en la rampa, de 0 a 1, de un valor en una escala con nodos.
 *
 * Los nodos son pares `[valor, fracción]` ordenados por valor: entre dos nodos
 * el reparto sigue siendo lineal, así que la rampa no pierde continuidad, pero
 * cada tramo se lleva la parte de paleta que se le asigna. Sirve para dilatar
 * la franja donde vive casi todo el campo sin recortar las colas: en la
 * temperatura a 2 m, los quince grados de una ola de frío alpina no tienen por
 * qué gastar el mismo trozo de rampa que los quince que separan una mañana de
 * marzo de una tarde de julio.
 *
 * Fuera del primer y del último nodo se recorta, como hace la escala lineal.
 */
export function anchorFraction(value, anchors) {
  if (value <= anchors[0][0]) return 0;
  const end = anchors.length - 1;
  if (value >= anchors[end][0]) return 1;
  for (let index = 1; index <= end; index += 1) {
    const [upperValue, upperStop] = anchors[index];
    if (value > upperValue) continue;
    const [lowerValue, lowerStop] = anchors[index - 1];
    const span = upperValue - lowerValue || 1;
    return lowerStop + (upperStop - lowerStop) * ((value - lowerValue) / span);
  }
  return 1;
}

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

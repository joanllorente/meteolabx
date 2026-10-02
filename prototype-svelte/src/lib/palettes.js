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

/**
 * θe con cola tropical: la paleta de siempre y, detrás, cuatro colores más.
 *
 * Con los nodos del producto, de −10 a 60 °C se recorren exactamente los
 * diez colores de `defaultPalette`, igual que antes, y los frentes de latitudes
 * medias no cambian. Por encima de 60 °C, donde el trópico y el núcleo de los
 * huracanes antes se recortaban al último color, sigue una rampa del magenta
 * al blanco.
 */
export const thetaEPalette = [...defaultPalette, '#a8409a', '#d46fb4', '#f2b3da', '#fff4fb'];

/**
 * Viento: la de siempre para el viento fuerte y un arranque claro para el flojo.
 *
 * Con `defaultPalette`, la calma caía en azul oscuro y el trazo negro de las
 * streamlines desaparecía justo donde el flujo es más enrevesado. Los tres
 * primeros colores se aclaran para que la tinta oscura siempre contraste;
 * del verde en adelante sigue igual, que es donde ya se leía bien.
 */
export const windPalette = [
  '#dfe8f1', '#a9c8e4', '#6fa8d6', '#66c2a5', '#abdda4', '#e6f598',
  '#fee08b', '#fdae61', '#f46d43', '#d73027', '#762a83'
];

export const divergingPalette = ['#2166ac', '#67a9cf', '#d1e5f0', '#f7f7f7', '#fddbc7', '#ef8a62', '#b2182b'];

export const precipitationPalette = [
  '#28465f', '#2f6f8e', '#369aa1', '#58bd91', '#9bd275',
  '#d7dc69', '#f2c55a', '#ed914c', '#df6262', '#b44f88'
];

/**
 * Tramo más allá del lila, hacia el blanco como en la theta-e, para la lluvia
 * horaria y el acumulado de AROME, que se quedaban cortos en 60 mm/h y 400 mm.
 *
 * No se alarga la paleta: se añade DESPUÉS de la escala de siempre. Alargarla
 * repartía la rampa entera de otra forma y los mismos milímetros cambiaban de
 * color —10 mm/h pasaban de ocre a rojo y 40 de rojo a lila—. Hasta el antiguo
 * techo (`paletteSplit` del producto) todo se pinta igual que antes; solo lo
 * que lo supera usa este tramo.
 */
export const precipitationExtension = ['#9a5bb5', '#c597dc', '#e8cdf3', '#fbf5ff'];
/** Del lila con que acaba la escala de siempre hasta el blanco. */
export const precipitationExtensionRamp = [precipitationPalette.at(-1), ...precipitationExtension];
/** Para la leyenda continua: la de siempre seguida del tramo nuevo. */
export const precipitationExtendedPalette = [...precipitationPalette, ...precipitationExtension];

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

/**
 * Colores por clase de una escala de precipitación ampliada, como [r, g, b].
 *
 * Las clases hasta `split` son exactamente las de siempre —las mismas muestras
 * de la paleta original que daba la escala corta—; las que lo superan se
 * reparten por el tramo nuevo, sin repetir el lila con que acaba la original.
 */
export function splitBandRgb(breaks, split) {
  const base = breaks.filter((value) => value <= split).length + 1;
  const extra = breaks.length + 1 - base;
  return [
    ...Array.from({ length: base }, (_, index) => paletteStop(precipitationPalette, bandPosition(index, base))),
    ...Array.from({ length: extra }, (_, index) => paletteStop(
      precipitationExtensionRamp,
      Math.round((index + 1) / extra * (LUT_SIZE - 1))
    ))
  ];
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

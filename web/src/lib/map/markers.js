/**
 * Paleta y agrupado de las cajas del mapa.
 *
 * Es la misma lógica que usa hoy `components/temperature_clusters_frontend`
 * en la aplicación de Streamlit: mismos cortes de color, mismo tamaño de
 * celda por nivel de zoom. Si aquí se cambiara un número, los dos mapas
 * dejarían de parecerse.
 */

const TEMPERATURE_STOPS = [
  [-20, [98, 22, 146]], [-10, [52, 122, 235]],
  [0, [88, 176, 245]], [5, [130, 215, 235]],
  [10, [110, 205, 125]], [15, [200, 225, 80]],
  [20, [250, 210, 50]], [25, [248, 158, 38]],
  [30, [238, 92, 28]], [35, [205, 32, 22]],
  [40, [150, 8, 32]], [46, [96, 2, 58]]
];

const PRECIPITATION_STOPS = [
  [0, [224, 238, 247]], [0.2, [183, 224, 240]],
  [1, [124, 196, 232]], [5, [70, 160, 219]],
  [10, [46, 121, 197]], [20, [58, 92, 178]],
  [40, [96, 66, 160]], [80, [140, 44, 132]],
  [150, [176, 24, 92]]
];

function interpolate(stops, value) {
  const clamped = Math.max(stops[0][0], Math.min(stops[stops.length - 1][0], value));
  let left = stops[0];
  let right = stops[stops.length - 1];
  for (let index = 1; index < stops.length; index += 1) {
    if (clamped <= stops[index][0]) {
      left = stops[index - 1];
      right = stops[index];
      break;
    }
  }
  const span = right[0] - left[0];
  const fraction = span === 0 ? 0 : (clamped - left[0]) / span;
  return left[1].map((channel, index) =>
    Math.round(channel + fraction * (right[1][index] - channel))
  );
}

export const colorForTemperature = (value) => interpolate(TEMPERATURE_STOPS, value);
export const colorForPrecipitation = (value) => interpolate(PRECIPITATION_STOPS, value);

/** Negro o blanco según lo claro que sea el fondo, para que el número se lea. */
export function textColor(rgb) {
  const luminance = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
  return luminance > 0.62 ? '#191b21' : '#ffffff';
}

/**
 * Lado de la celda de agrupado, en píxeles.
 *
 * Cuanto más cerca, celdas más pequeñas: al acercarse se quiere ver cada
 * estación, y de lejos un número por comarca en vez de mil superpuestos.
 */
export function cellSizeForZoom(zoom) {
  const level = Number.isFinite(zoom) ? zoom : 0;
  if (level < 5) return 112;
  if (level < 8) return 92;
  if (level < 11) return 68;
  if (level < 14) return 48;
  if (level < 16) return 28;
  return 14;
}

/**
 * Agrupa puntos ya proyectados a píxeles en una rejilla regular.
 *
 * Cada grupo conserva sus puntos: la caja enseña el valor representativo y,
 * si hay más de uno, cuántos van dentro.
 */
export function clusterByGrid(projected, cellSize) {
  const cells = new Map();
  for (const point of projected) {
    const key = `${Math.floor(point.x / cellSize)}:${Math.floor(point.y / cellSize)}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(point);
    else cells.set(key, [point]);
  }
  return Array.from(cells.values());
}

/** Media aritmética de un campo, ignorando lo que no sea número. */
export function meanOf(cluster, field) {
  const values = cluster.map((item) => item[field]).filter(Number.isFinite);
  if (!values.length) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

/** Media de rumbos: se promedian los vectores, no los grados. */
export function meanDirection(cluster) {
  let x = 0;
  let y = 0;
  let count = 0;
  for (const item of cluster) {
    if (!Number.isFinite(item.direction)) continue;
    const radians = (item.direction * Math.PI) / 180;
    x += Math.cos(radians);
    y += Math.sin(radians);
    count += 1;
  }
  if (!count) return null;
  const degrees = (Math.atan2(y, x) * 180) / Math.PI;
  return (degrees + 360) % 360;
}

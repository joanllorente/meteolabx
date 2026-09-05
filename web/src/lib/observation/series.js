/**
 * Preparación de series temporales para las gráficas.
 *
 * Lo comparten observación —serie del día— y tendencias —ventana sinóptica de
 * varios días—. La diferencia entre las dos es el eje: en un día bastan las
 * horas; en una semana hay que decir también el día, o los rótulos se repiten
 * siete veces y no se entiende nada.
 */
import { isNumber, locale, stationTime } from '$lib/format.js';

// Puntos máximos por gráfica. Una estación que publica cada minuto da 1.400
// puntos al día: dibujarlos todos no añade información y multiplica el peso
// del HTML, que aquí se sirve renderizado.
export const MAX_CHART_POINTS = 96;
const MIN_CHART_POINTS = 3;
const AXIS_TICKS = 7;

/**
 * Índices donde TODAS las magnitudes pedidas tienen dato.
 * Sin esto, el hueco de un sensor rompería la escala de la gráfica entera.
 */
function alignedIndices(epochs, arrays) {
  const indices = [];
  for (let index = 0; index < epochs.length; index += 1) {
    if (!isNumber(epochs[index])) continue;
    if (arrays.every((values) => isNumber(values?.[index]))) indices.push(index);
  }
  return indices;
}

/** Reduce a `MAX_CHART_POINTS` conservando siempre el último punto: es «ahora». */
function thinned(indices) {
  if (indices.length <= MAX_CHART_POINTS) return indices;
  const stride = Math.ceil(indices.length / MAX_CHART_POINTS);
  const kept = indices.filter((_, position) => position % stride === 0);
  const last = indices[indices.length - 1];
  if (kept[kept.length - 1] !== last) kept.push(last);
  return kept;
}

/** Día y hora local de la estación, para ejes que abarcan varias jornadas. */
function stationDayTime(epoch, { language, timeZone }) {
  if (!isNumber(epoch) || epoch <= 0) return '';
  try {
    return new Intl.DateTimeFormat(locale(language), {
      day: '2-digit',
      month: '2-digit',
      timeZone: timeZone || 'UTC'
    }).format(new Date(epoch * 1000));
  } catch {
    return stationTime(epoch, { language, timeZone });
  }
}

/**
 * Etiquetas del eje X.
 *
 * El componente pinta una de cada dos, así que las etiquetas van en índices
 * pares y el resto queda en blanco: con noventa puntos y todas puestas, el
 * eje sería una mancha.
 */
function axisLabels(indices, epochs, { language, timeZone, span }) {
  const labels = new Array(indices.length).fill('');
  const step = Math.max(1, Math.floor((indices.length - 1) / (AXIS_TICKS - 1)));
  const format = span === 'days' ? stationDayTime : stationTime;
  for (let position = 0; position < indices.length; position += step) {
    const even = position % 2 === 0 ? position : position - 1;
    if (even < 0) continue;
    labels[even] = format(epochs[indices[even]], { language, timeZone });
  }
  return labels;
}

/**
 * Gráfica lista para `TrendChart`/`WindChart`, o `null` si no hay serie.
 *
 * `fields` son nombres de campo de la serie; se devuelven alineados entre sí
 * y recortados al mismo eje.
 */
export function chart(series, fields, { language, timeZone, span = 'day' } = {}) {
  const epochs = series?.epochs || [];
  const arrays = fields.map((field) => series?.[field] || []);
  const indices = thinned(alignedIndices(epochs, arrays));
  if (indices.length < MIN_CHART_POINTS) return null;
  return {
    labels: axisLabels(indices, epochs, { language, timeZone, span }),
    // Los instantes de cada punto. Dos gráficas de la misma estación no
    // conservan los mismos índices —cada una descarta sus propios huecos—,
    // así que el cursor compartido se sincroniza por tiempo, no por posición.
    epochs: indices.map((index) => epochs[index]),
    data: arrays.map((values) => indices.map((index) => values[index])),
    nowIndex: indices.length - 1
  };
}

// ---------------------------------------------------------------------------
// Rejilla del día
// ---------------------------------------------------------------------------

/**
 * Ranuras de 5 minutos: 288 cubren el día entero.
 *
 * Con ranuras de cuarto de hora, una lectura de las 16:42 se dibujaba en las
 * 16:30 y la gráfica parecía ir atrasada. Cinco minutos es más fino que lo que
 * publica cualquier red, así que el punto cae donde toca.
 */
const DAY_SLOTS = 288;
const SLOT_MINUTES = (24 * 60) / DAY_SLOTS;
const DAY_LABEL_EVERY = 36; // una etiqueta cada tres horas

/** Día local de la estación, como `2026-09-04`. */
function localDayKey(epoch, timeZone) {
  if (!isNumber(epoch) || epoch <= 0) return '';
  try {
    return new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      timeZone: timeZone || 'UTC'
    }).format(new Date(epoch * 1000));
  } catch {
    return '';
  }
}

/** Minutos transcurridos desde la medianoche LOCAL de la estación. */
function minutesIntoLocalDay(epoch, timeZone) {
  if (!isNumber(epoch) || epoch <= 0) return null;
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: timeZone || 'UTC'
    }).formatToParts(new Date(epoch * 1000));
    const hour = Number(parts.find((part) => part.type === 'hour')?.value);
    const minute = Number(parts.find((part) => part.type === 'minute')?.value);
    if (!isNumber(hour) || !isNumber(minute)) return null;
    // Medianoche sale como 24 en algunos entornos; se normaliza a 0.
    return ((hour % 24) * 60) + minute;
  } catch {
    return null;
  }
}

/**
 * Gráfica del día sobre un eje FIJO de 24 horas.
 *
 * La serie del proveedor solo llega hasta ahora, así que dibujarla sobre su
 * propia extensión hace que el gráfico crezca a lo largo del día y que a las
 * 08:00 parezca que la jornada entera cabe en dos horas. Aquí el eje va
 * siempre de 00:00 a 24:00 y la línea se va rellenando: lo que falta queda
 * vacío, que es la verdad.
 */
export function dayChart(series, fields, { language, timeZone } = {}) {
  const epochs = series?.epochs || [];
  const arrays = fields.map((field) => series?.[field] || []);
  const slots = arrays.map(() => new Array(DAY_SLOTS).fill(null));
  const slotEpochs = new Array(DAY_SLOTS).fill(null);
  let filled = 0;

  // Qué día es «hoy» para esta estación. Hay redes —Weather Underground, sin
  // ir más lejos— que devuelven las últimas veinticuatro horas en vez del día
  // natural: sin este filtro, las lecturas de ayer por la tarde se pintaban al
  // final del eje y la gráfica salía partida en dos.
  const today = localDayKey(
    [...epochs].reverse().find((epoch) => isNumber(epoch) && epoch > 0),
    timeZone
  );

  for (let index = 0; index < epochs.length; index += 1) {
    if (today && localDayKey(epochs[index], timeZone) !== today) continue;
    const minutes = minutesIntoLocalDay(epochs[index], timeZone);
    if (minutes === null) continue;
    // A la ranura más cercana, no a la anterior: con `floor`, una lectura
    // siempre se dibujaba hasta cinco minutos antes de su hora.
    const slot = Math.max(0, Math.min(DAY_SLOTS - 1, Math.round(minutes / SLOT_MINUTES)));
    let wrote = false;
    arrays.forEach((values, position) => {
      const value = values?.[index];
      if (!isNumber(value)) return;
      // La última lectura de la ranura manda: dentro de quince minutos puede
      // haber varias y la más reciente es la que cuenta.
      slots[position][slot] = value;
      wrote = true;
    });
    if (wrote) {
      slotEpochs[slot] = epochs[index];
      filled += 1;
    }
  }

  if (filled < MIN_CHART_POINTS) return null;

  const labels = new Array(DAY_SLOTS).fill('');
  for (let slot = 0; slot < DAY_SLOTS; slot += DAY_LABEL_EVERY) {
    const hour = Math.floor((slot * SLOT_MINUTES) / 60);
    labels[slot] = `${String(hour).padStart(2, '0')}:00`;
  }

  // La línea de «ahora» se planta en la última ranura con dato.
  let nowIndex = null;
  for (let slot = DAY_SLOTS - 1; slot >= 0; slot -= 1) {
    if (slots.some((values) => isNumber(values[slot]))) {
      nowIndex = slot;
      break;
    }
  }

  return { labels, epochs: slotEpochs, data: slots, nowIndex };
}

// ---------------------------------------------------------------------------
// Irradiancia
// ---------------------------------------------------------------------------

/** Ranuras del eje solar. Con 96 la curva teórica sale continua. */
const SOLAR_SLOTS = 96;

/**
 * Gráfica de irradiancia, del orto al ocaso.
 *
 * Dos diferencias con el resto de gráficas del día, y las dos vienen de lo
 * mismo: la irradiancia solo existe mientras hay sol.
 *
 *   · El eje no va de 00:00 a 24:00 sino de amanecer a atardecer. Las horas
 *     de noche son una franja plana a cero que solo aplasta la curva.
 *   · La teórica se dibuja entera. Es una curva astronómica —el cielo limpio
 *     de hoy en esta latitud—, existe aunque la estación no haya publicado
 *     todavía la tarde, y es la referencia contra la que se lee lo medido.
 *
 * Devuelve `null` si la estación no da irradiancia o si no se conocen las
 * horas de sol: sin eje no hay gráfica.
 */
export function solarChart(series, { language, timeZone } = {}) {
  const from = Number(series?.sunrise_epoch);
  const to = Number(series?.sunset_epoch);
  if (!isNumber(from) || !isNumber(to) || to <= from) return null;

  const measured = series?.solar_radiations || [];
  if (!measured.some(isNumber)) return null;

  const slotOf = (epoch) =>
    Math.round(((epoch - from) / (to - from)) * (SOLAR_SLOTS - 1));

  const inWindow = (epoch) => isNumber(epoch) && epoch >= from && epoch <= to;
  const fill = (epochs, values) => {
    const slots = new Array(SOLAR_SLOTS).fill(null);
    for (let index = 0; index < epochs.length; index += 1) {
      const epoch = Number(epochs[index]);
      if (!inWindow(epoch) || !isNumber(values?.[index])) continue;
      slots[Math.max(0, Math.min(SOLAR_SLOTS - 1, slotOf(epoch)))] = values[index];
    }
    return slots;
  };

  const epochs = new Array(SOLAR_SLOTS)
    .fill(0)
    .map((_, slot) => from + ((to - from) * slot) / (SOLAR_SLOTS - 1));

  const labels = new Array(SOLAR_SLOTS).fill('');
  // Una etiqueta cada ocho ranuras: con trece horas de sol salen unas siete.
  for (let slot = 0; slot < SOLAR_SLOTS; slot += 8) {
    labels[slot] = stationTime(epochs[slot], { language, timeZone });
  }

  return {
    labels,
    epochs,
    data: [
      fill(series?.epochs || [], measured),
      fill(series?.solar_day_epochs || [], series?.solar_day_theoretical || [])
    ]
  };
}

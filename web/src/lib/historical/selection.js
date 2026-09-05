/**
 * Qué se pide al histórico y cómo se lee de la URL.
 *
 * Vive aparte porque lo comparten las dos rutas del histórico: la de las
 * estaciones con ficha indexable y la de las que se identifican por red e
 * identificador —entre ellas las propias, que consultan desde el navegador
 * con su credencial—. La selección se resuelve igual en ambas; lo único que
 * cambia es quién hace la llamada al proveedor.
 */
const FIRST_YEAR = 1990;
// El mismo tope que la app actual: en mensual, doce bloques mes×año. Pedir
// más son doce peticiones al proveedor y una espera que nadie quiere.
export const MAX_MONTHLY_BLOCKS = 12;

/** Cuántas descargas son: de eso depende cuánto se puede tardar. */
export function countBlocks(mode, selection) {
  return mode === 'annual'
    ? selection.years.length
    : selection.years.length * selection.months.length;
}

/**
 * Qué se va a pedir, en una línea: rango, bloques y días.
 *
 * Es la misma cuenta que enseña la app actual antes de consultar, y sirve
 * para lo mismo: doce bloques mes×año no son una consulta cualquiera y
 * conviene verlo antes de pulsar.
 */
export function describeSelection(mode, selection, blocks) {
  const today = new Date();
  const stamps = [];
  let days = 0;

  const add = (start, end) => {
    const last = end > today ? today : end;
    if (start > last) return;
    stamps.push(start.getTime(), last.getTime());
    days += Math.round((last - start) / 86400000) + 1;
  };

  for (const year of selection.years) {
    if (mode === 'annual') {
      add(new Date(Date.UTC(year, 0, 1)), new Date(Date.UTC(year, 11, 31)));
      continue;
    }
    for (const month of selection.months) {
      add(new Date(Date.UTC(year, month - 1, 1)), new Date(Date.UTC(year, month, 0)));
    }
  }

  if (!stamps.length) return { range: '', blocks, days: 0 };
  const format = (value) => new Date(value).toISOString().slice(0, 10).split('-').reverse().join('/');
  return {
    range: `${format(Math.min(...stamps))} → ${format(Math.max(...stamps))}`,
    blocks,
    days
  };
}

/**
 * Meses y años pedidos.
 *
 * Se admiten repetidos (`?meses=7&meses=8`) y listas (`?meses=7,8`): lo
 * primero es lo que manda un formulario con casillas, lo segundo lo que se
 * escribe a mano en un enlace.
 */
export function resolveSelection(params, mode, language, requested = false) {
  const now = new Date();
  const currentYear = now.getUTCFullYear();
  // El mes en curso casi nunca está completo en los catálogos oficiales; el
  // anterior sí, y es el que deja el formulario listo para consultar.
  const previous = new Date(Date.UTC(currentYear, now.getUTCMonth() - 1, 1));

  const numbers = (name) =>
    params
      .getAll(name)
      .flatMap((value) => String(value).split(','))
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value));

  const months = [...new Set(numbers('meses').filter((m) => m >= 1 && m <= 12))].sort(
    (a, b) => a - b
  );
  const years = [
    ...new Set(numbers('anios').filter((y) => y >= FIRST_YEAR && y <= currentYear))
  ].sort((a, b) => b - a);

  const yearOptions = [];
  for (let value = currentYear; value >= FIRST_YEAR; value -= 1) yearOptions.push(value);

  const formatter = new Intl.DateTimeFormat(language, { month: 'long', timeZone: 'UTC' });
  const monthOptions = Array.from({ length: 12 }, (_, index) => {
    const label = formatter.format(new Date(Date.UTC(2000, index, 1)));
    return { value: index + 1, label: label.charAt(0).toUpperCase() + label.slice(1) };
  });

  // Al abrir la pestaña se propone el último periodo completo, que es el que
  // siempre tiene datos. Pero si ya se ha pulsado el botón, una selección
  // vacía es una selección vacía: se avisa en vez de consultar otra cosa.
  const suggest = !requested;
  return {
    months: months.length || !suggest ? months : [previous.getUTCMonth() + 1],
    years:
      years.length || !suggest
        ? years
        : [mode === 'annual' ? currentYear - 1 : previous.getUTCFullYear()],
    monthOptions,
    yearOptions
  };
}

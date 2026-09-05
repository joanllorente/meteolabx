/**
 * Idioma con el que responder cuando la URL no lo dice.
 *
 * Solo pasa en la raíz del sitio: el resto de páginas lo llevan en la ruta.
 * El navegador no manda un idioma sino una lista con prioridades —
 * `ru-RU,ru;q=0.9,en-US;q=0.8` —, así que se negocia con la lista entera: un
 * visitante ruso con inglés de segunda recibe inglés, no el idioma por
 * defecto del sitio.
 */
/**
 * Último recurso.
 *
 * Quien no pide ninguno de los seis en toda su lista es, por definición, de
 * fuera de su ámbito; el inglés es lo que más probablemente entienda.
 */
export const FALLBACK_LANGUAGE = 'en';

/** `[{ code, quality }]` ordenado de más a menos preferido. */
export function parseAcceptLanguage(header) {
  return String(header || '')
    .split(',')
    .map((chunk) => {
      const [tag, ...params] = chunk.trim().split(';');
      const quality = params
        .map((param) => param.trim())
        .filter((param) => param.startsWith('q='))
        .map((param) => Number(param.slice(2)))
        .find(Number.isFinite);
      return { tag: tag.trim().toLowerCase(), quality: quality === undefined ? 1 : quality };
    })
    .filter((entry) => entry.tag && entry.quality > 0)
    .sort((a, b) => b.quality - a.quality);
}

/**
 * Primer idioma del sitio que aparezca en la lista del navegador.
 *
 * Se comparan también las variantes regionales: `pt-BR` cuenta como `pt`.
 */
export function negotiateLanguage(header, supported) {
  const codes = new Set(supported);
  for (const { tag } of parseAcceptLanguage(header)) {
    if (codes.has(tag)) return tag;
    const base = tag.split('-')[0];
    if (codes.has(base)) return base;
  }
  return codes.has(FALLBACK_LANGUAGE) ? FALLBACK_LANGUAGE : supported[0];
}

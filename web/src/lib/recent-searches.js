/**
 * Últimas búsquedas de la caja de «Conecta una estación», en el navegador.
 *
 * Se guarda lo que se escribió y el nombre del sitio al que resolvió; nunca
 * la ubicación del botón de geolocalización, que es la posición de alguien.
 * Como en `favourites.svelte.js`, todo acceso a `localStorage` va envuelto:
 * sin almacenamiento la caja funciona igual, solo que sin memoria.
 */

const KEY = 'mlx-recent-searches';
export const RECENT_LIMIT = 8;

function normalize(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

function sanitize(list) {
  if (!Array.isArray(list)) return [];
  return list
    .filter((item) => typeof item?.query === 'string' && item.query.trim())
    .map((item) => ({
      query: item.query.trim().slice(0, 120),
      label: typeof item.label === 'string' ? item.label : ''
    }))
    .slice(0, RECENT_LIMIT);
}

export function loadRecentSearches(storage = globalThis.localStorage) {
  try {
    return sanitize(JSON.parse(storage?.getItem(KEY) || '[]'));
  } catch {
    return [];
  }
}

function save(list, storage) {
  try {
    storage?.setItem(KEY, JSON.stringify(list));
  } catch {
    /* sin almacenamiento no se recuerda nada, y tampoco pasa nada */
  }
}

/** Pone la búsqueda la primera, sin duplicados, y devuelve la lista nueva. */
export function rememberSearch({ query, label = '' }, storage = globalThis.localStorage) {
  const clean = String(query || '').trim().slice(0, 120);
  if (!clean) return loadRecentSearches(storage);
  const key = normalize(clean);
  const rest = loadRecentSearches(storage).filter((item) => normalize(item.query) !== key);
  const updated = [{ query: clean, label: String(label || '') }, ...rest].slice(0, RECENT_LIMIT);
  save(updated, storage);
  return updated;
}

export function forgetSearch(query, storage = globalThis.localStorage) {
  const key = normalize(query);
  const updated = loadRecentSearches(storage).filter((item) => normalize(item.query) !== key);
  save(updated, storage);
  return updated;
}

/**
 * Las que encajan con lo que hay escrito, como el desplegable de Google: con
 * la caja vacía, todas; si no, las que contienen el texto en la búsqueda o en
 * el nombre del sitio, sin distinguir mayúsculas ni acentos.
 */
export function matchRecentSearches(list, text) {
  const needle = normalize(text);
  if (!needle) return list;
  return list.filter(
    (item) => normalize(item.query).includes(needle) || normalize(item.label).includes(needle)
  );
}

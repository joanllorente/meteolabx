import { isLanguage } from '$lib/seo/i18n.js';

/**
 * Solo los seis idiomas que ya publican fichas. Cualquier otro prefijo cae
 * fuera de la ruta y lo recoge el proxy hacia la app antigua, que es donde
 * siguen viviendo el resto de URLs.
 */
export function match(param) {
  return isLanguage(param);
}

/**
 * A dónde está navegando la aplicación ahora mismo.
 *
 * Conectar con una estación es un enlace normal, pero detrás hay una consulta
 * al proveedor que tarda: hasta que responde, la página anterior sigue en
 * pantalla y parece que el clic no ha hecho nada. Esto permite señalar el
 * destino —la fila, el botón— mientras dura la espera.
 */
import { navigating } from '$app/state';

/** ¿La navegación en curso va a `href`? */
export function navigatingTo(href) {
  const target = navigating?.to?.url;
  if (!target || !href) return false;
  try {
    return new URL(href, target.origin).pathname === target.pathname;
  } catch {
    return false;
  }
}

/** ¿Hay alguna navegación en curso? */
export function isNavigating() {
  return Boolean(navigating?.to);
}

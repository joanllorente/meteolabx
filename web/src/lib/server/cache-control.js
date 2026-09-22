/**
 * Caché de las páginas que consultan al proveedor en vivo.
 *
 * Una ficha buena se comparte en Cloudflare y se guarda en el navegador: los
 * datos valen para todos durante un rato. Una ficha SIN datos no: el fallo es
 * de ese instante, y guardarlo lo alarga. Recoaro Mille (MeteoHub) acumuló
 * nueve «provider_unreachable» en un día sin que MeteoHub cayera nueve veces:
 * la página del error se servía desde el borde cinco minutos, el navegador la
 * guardaba hasta una hora, y cada carga de esa copia anotaba otro error en el
 * panel —el aviso se registra al pintar, no al fallar—.
 *
 * `private, no-store` además la saca de la caché compartida: `hooks.server.js`
 * solo comparte lo que pide `public`.
 */
export const NO_STORE = 'private, no-store';

/** `whenOk` si la consulta en vivo salió bien; si no, que nadie la guarde. */
export function liveCacheControl(ok, whenOk) {
  return ok ? whenOk : NO_STORE;
}

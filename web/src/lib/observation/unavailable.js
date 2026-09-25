/**
 * Por qué una ficha aparece sin datos.
 *
 * Decirlo todo con «la estación no está publicando datos ahora mismo» es
 * cómodo y a veces falso: una red rechazada por credenciales publica
 * perfectamente, quien no llega es este servidor. La confusión cuesta caro
 * —una tarde entera buscando en el frontend un 401 del backend—, así que
 * cada familia de fallo dice lo suyo.
 *
 * Solo distingue lo que el visitante puede interpretar: falta de acceso,
 * proveedor lento, proveedor limitado, proveedor incomunicado y, por defecto,
 * sin datos.
 */
export function unavailableKey(unavailable) {
  // Sin diagnóstico —el proveedor respondió, pero sin lectura reciente—
  // la estación callada sigue siendo la explicación correcta.
  if (!unavailable) return 'data_unavailable';

  const code = String(unavailable.code || '');
  const status = Number(unavailable.status) || 0;

  if (code === 'provider_unauthorized' || status === 401 || status === 403) {
    return 'provider_unauthorized';
  }
  if (code === 'provider_timeout' || status === 504) return 'provider_timeout';
  // El límite de consultas del proveedor no dice nada de la estación: AEMET
  // rechaza la petición y Marbella sigue publicando cada diez minutos. Sin
  // esta rama caía en «no está publicando datos», que es justo la confusión
  // que este módulo existe para evitar.
  if (code === 'provider_ratelimit' || status === 429) return 'provider_ratelimit';
  // El proveedor contestó, y lo que contestó es que esa estación no tiene
  // lectura reciente. Llega con un 502 —el backend lo usa para «no pude
  // componer la respuesta»— pero la red está perfectamente: Monte Carpegna
  // llevaba dos días muda y la ficha culpaba a MeteoHub. Este caso tiene que
  // ganarle al status, o el 502 se lo lleva a «no se ha podido contactar».
  if (code === 'provider_no_current_data') return 'data_unavailable';
  if (code === 'unreachable' || code === 'provider_network_error' || status === 502) {
    return 'provider_unreachable';
  }
  return 'data_unavailable';
}

/**
 * Traduce el fallo de una petición al backend en un diagnóstico para la ficha.
 *
 * La distinción que importa aquí es entre «no llegué» y «me cansé de esperar»:
 * cuando el reloj de ``request()`` aborta la petición, el error no es un
 * ApiError y acababa cayendo en ``unreachable`` —«no se ha podido contactar
 * con el proveedor de esta red»—, cuando lo cierto es que la red contesta y es
 * este servidor el que ha colgado. Pasó con cuatro estaciones de MeteoGalicia,
 * que tarda entre cuatro y siete segundos en responder.
 */
export function describeRequestFailure(cause, { ApiError } = {}) {
  if (ApiError && cause instanceof ApiError) {
    return { status: cause.status, code: cause.body?.error_code || 'provider_error' };
  }
  const name = String(cause?.name || '');
  if (name === 'AbortError' || name === 'TimeoutError') {
    return { status: 504, code: 'provider_timeout' };
  }
  return { status: 0, code: 'unreachable' };
}

/**
 * Un buscador pidió la ficha y no había lectura guardada que enseñarle.
 *
 * Los rastreadores no consultan al proveedor en vivo (ver `hooks.server.js`),
 * así que para ellos la falta de datos no es un fallo de nadie: la ficha se
 * sirve sin aviso, con su ubicación y sus sensores, en vez de con un «vuelve a
 * intentarlo» que Google leía en miles de páginas como error.
 */
export const SNAPSHOT_MISSING = 'snapshot_missing';

export function isSnapshotMissing(unavailable) {
  return unavailable?.code === SNAPSHOT_MISSING;
}

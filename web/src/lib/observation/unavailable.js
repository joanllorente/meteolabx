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
 * proveedor lento, proveedor incomunicado y, por defecto, sin datos.
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
  if (code === 'unreachable' || code === 'provider_network_error' || status === 502) {
    return 'provider_unreachable';
  }
  return 'data_unavailable';
}

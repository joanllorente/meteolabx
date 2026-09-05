/**
 * Estadísticas internas de uso.
 *
 * Cuentan qué estaciones se consultan y qué falla al consultarlas: es lo que
 * alimenta el panel interno y lo único que hay para saber si una red lleva
 * días caída. No llevan identificadores, ni sesión, ni IP —eso lo dice la
 * ventana de privacidad— y nunca deben estorbar: cada envío va por su cuenta
 * y su fallo se ignora.
 *
 * Lo enviaba la aplicación anterior; al retirarla, las conexiones, los errores
 * y las entradas a cada pestaña dejaron de contarse y el panel se quedaba
 * enseñando solo las aperturas de ficha.
 */
function send(path, body) {
  if (typeof fetch !== 'function') return;
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    // Sobrevive a la navegación: si la visita se registra justo al saltar de
    // página, el navegador no la cancela a medias.
    keepalive: true
  }).catch(() => {});
}

/** Alguien ha abierto la ficha de una estación. */
export function recordVisit({ provider, stationId, name = '', source = 'app' }) {
  if (!provider || !stationId) return;
  send('/v1/stats/visit', { provider, station_id: stationId, name, source });
}

/**
 * La consulta a esa estación falló.
 *
 * `kind` es la categoría que ya distingue la interfaz —tiempo agotado,
 * credenciales rechazadas, red incomunicada— y no el mensaje completo: el
 * registro cuenta clases de fallo, no textos.
 */
export function recordConnectionError({ provider, stationId, name = '', kind, status = null }) {
  if (!provider || !stationId || !kind) return;
  send('/v1/stats/error', {
    provider,
    station_id: stationId,
    name,
    error_kind: kind,
    ...(Number.isInteger(status) && status >= 100 && status <= 599 ? { status_code: status } : {})
  });
}

/** Entrada a una pestaña. Las del mapa llevan su capa: `map.temperature`. */
export function recordSection(section) {
  if (!section) return;
  send('/v1/stats/section', { section });
}

/** Apertura de una ficha indexable, con el idioma en el que se leyó. */
export function recordSeoView({ provider, stationId, name = '', language = '' }) {
  if (!provider || !stationId) return;
  send('/v1/stats/seo-view', { provider, station_id: stationId, name, language });
}

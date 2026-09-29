/**
 * Estadísticas internas del visor de predicción.
 *
 * Mismas reglas que las de la web (`web/src/lib/stats.js`): sin
 * identificadores ni sesión, cada envío por su cuenta y su fallo ignorado. El
 * visor se sirve en el mismo dominio, así que respeta también el interruptor
 * `?stats=off` que se guarda en `localStorage`.
 */
const OPT_OUT = 'mlx-stats-off';

function statsExcluded() {
  try {
    return localStorage.getItem(OPT_OUT) === '1';
  } catch {
    return false;
  }
}

// Una vez por modelo y carga de página, al abrir el visor y al elegirlo en la
// barra: saltar de AROME a ECMWF y volver cuenta una entrada en cada uno.
const seenModels = new Set();

/** Cuenta la entrada en un modelo del visor. */
export function recordForecastModel(model) {
  if (!model || typeof fetch !== 'function' || seenModels.has(model)) return;
  seenModels.add(model);
  if (statsExcluded()) return;
  fetch('/v1/stats/forecast-model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
    keepalive: true
  }).catch(() => {});
}

// Una vez por mapa y carga de página: quien vuelve tres veces a la CAPE
// mientras compara con la cizalladura ha visto un mapa, no tres.
const seen = new Set();

/**
 * Cuenta la apertura de un mapa que el visitante ha elegido.
 *
 * `label` y `category` van en castellano, sin traducir: el panel interno es
 * uno solo y agrupa por ellos.
 */
export function recordForecastMap({ model, product, label = '', category = '' } = {}) {
  if (!model || !product || typeof fetch !== 'function') return;
  const key = `${model}|${product}`;
  if (seen.has(key)) return;
  seen.add(key);
  if (statsExcluded()) return;
  fetch('/v1/stats/forecast-map', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, product, label, category }),
    keepalive: true
  }).catch(() => {});
}

/**
 * Contrato de acceso al futuro backend AROME.
 *
 * La vista usa datos de demostración mientras estos endpoints no estén
 * publicados. Mantener el cliente separado permite conectar el motor Python
 * sin cambiar los componentes visuales.
 */
const configuredBase = import.meta.env.VITE_METEOLABX_API_URL || '';
const localBase = typeof window !== 'undefined' && ['127.0.0.1', 'localhost'].includes(window.location.hostname)
  ? (window.location.port === '5173' ? '' : `${window.location.protocol}//${window.location.hostname}:8000`)
  : '';
const API_BASE = (configuredBase || localBase).replace(/\/$/, '');
// Se sube con cada cambio de formato de la rejilla. Los frames se sirven con
// `immutable` y un año de caché, así que sin tocar esto un navegador que ya
// tenga la hora guardada seguiría enseñando la versión anterior —sin la capa
// de geopotencial, en este caso— y ni recargando ni reiniciando la cambiaría.
const FORECAST_DATA_REVISION = 'forecast-fields-v17';
const FRAME_CACHE_MAX_BYTES = 192 * 1024 * 1024;
const frameCache = new Map();
const geometryCache = new Map();
// Peticiones en vuelo: evita que la precarga y la selección del usuario pidan
// el mismo frame dos veces.
const pendingFrames = new Map();
let frameCacheBytes = 0;

function frameCacheKey({ product, validTime, run, verticalKind, level } = {}) {
  return [FORECAST_DATA_REVISION, run || '', product || '', validTime || '', verticalKind || '', level ?? ''].join('|');
}

export function getCachedAromeFrame(options = {}) {
  const key = frameCacheKey(options);
  const entry = frameCache.get(key);
  if (!entry) return null;
  // Actualiza el orden LRU para conservar las horas usadas más recientemente.
  frameCache.delete(key);
  frameCache.set(key, entry);
  return entry.frame;
}

function rememberAromeFrame(options, frame) {
  const key = frameCacheKey(options);
  const previous = frameCache.get(key);
  if (previous) frameCacheBytes -= previous.bytes;
  const bytes = [frame.values, frame.u, frame.v, frame.overlay]
    .filter(Boolean)
    .reduce((total, matrix) => total + matrix.byteLength, 0);
  frameCache.delete(key);
  frameCache.set(key, { frame, bytes });
  frameCacheBytes += bytes;
  while (frameCacheBytes > FRAME_CACHE_MAX_BYTES && frameCache.size > 1) {
    const oldestKey = frameCache.keys().next().value;
    frameCacheBytes -= frameCache.get(oldestKey).bytes;
    frameCache.delete(oldestKey);
  }
  return frame;
}

// Las fronteras son idénticas para todos los mapas. Desde el formato 3 no
// viajan dentro del frame: se piden una vez y se reutilizan.
let boundariesRequest = null;

/**
 * Convierte el `detail` de la API en algo legible.
 *
 * FastAPI lo devuelve como texto en sus errores propios, pero en los de
 * validación es una lista de objetos: interpolarla directamente dejaba un
 * «[object Object]» en pantalla en vez de decir qué campo estaba mal.
 */
function describeApiDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  const partes = (Array.isArray(detail) ? detail : [detail]).map((item) => {
    if (typeof item === 'string') return item;
    const campo = Array.isArray(item?.loc) ? item.loc.filter((p) => p !== 'query').join('.') : '';
    const mensaje = item?.msg || JSON.stringify(item);
    return campo ? `${campo}: ${mensaje}` : mensaje;
  });
  return partes.join(' · ');
}

function fetchDomainBoundaries() {
  if (!boundariesRequest) {
    boundariesRequest = getJson('/v1/forecast/arome/boundaries')
      .then((payload) => payload.boundaries || [])
      .catch((error) => {
        // Sin contornos el mapa sigue siendo legible; se reintenta al siguiente.
        boundariesRequest = null;
        throw error;
      });
  }
  return boundariesRequest;
}

function shareFrameGeometry(header) {
  if (!header.boundaries?.length) return header;
  const key = [header.calculation_scope || '', header.width, header.height, ...(header.bounds || [])].join('|');
  const shared = geometryCache.get(key);
  if (shared) header.boundaries = shared;
  else geometryCache.set(key, header.boundaries);
  return header;
}

/**
 * Reconstruye las matrices del cuerpo.
 *
 * v1 son Float32 crudos. De v2 en adelante son códigos uint16 con los bytes
 * altos y bajos en bloques separados, y puede omitir el escalar cuando es el
 * módulo de (u, v); v3 solo cambia la cabecera, que ya no lleva fronteras. El
 * almacén conserva frames de las tres versiones mientras rotan las pasadas.
 */
function decodeFrameBody(buffer, header, bodyStart) {
  const cellCount = header.width * header.height;
  if (!(header.version >= 2)) {
    const matrixBytes = cellCount * Float32Array.BYTES_PER_ELEMENT;
    let offset = bodyStart;
    const readMatrix = () => {
      const matrix = new Float32Array(buffer.slice(offset, offset + matrixBytes));
      offset += matrixBytes;
      return matrix;
    };
    const values = readMatrix();
    const u = header.has_vectors ? readMatrix() : null;
    const v = header.has_vectors ? readMatrix() : null;
    const overlay = header.has_overlay ? readMatrix() : null;
    return { ...header, values, u, v, overlay };
  }

  const bytes = new Uint8Array(buffer);
  const matrices = {};
  let offset = bodyStart;
  for (const { name, offset: base, step } of header.arrays || []) {
    const matrix = new Float32Array(cellCount);
    const high = offset;
    const low = offset + cellCount;
    for (let index = 0; index < cellCount; index += 1) {
      const code = (bytes[high + index] << 8) | bytes[low + index];
      // El código 0 está reservado para las celdas sin dato.
      matrix[index] = code === 0 ? NaN : base + (code - 1) * step;
    }
    matrices[name] = matrix;
    offset += cellCount * 2;
  }

  const u = matrices.u || null;
  const v = matrices.v || null;
  let values = matrices.value || null;
  if (!values && header.value_source === 'hypot' && u && v) {
    values = new Float32Array(cellCount);
    for (let index = 0; index < cellCount; index += 1) {
      // sqrt en vez de hypot: nueve veces más rápido sobre la malla completa y
      // con resultado idéntico en este rango de magnitudes.
      const eastward = u[index];
      const northward = v[index];
      values[index] = Math.sqrt(eastward * eastward + northward * northward);
    }
  }
  return { ...header, values, u, v, overlay: matrices.overlay || null };
}

async function getJson(path, { signal } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal
  });
  if (!response.ok) {
    throw new Error(`Forecast API ${response.status}`);
  }
  return response.json();
}

export function fetchAromeCatalog({ signal } = {}) {
  return getJson('/v1/forecast/arome/catalog', { signal });
}

export function fetchAromeFrame({ product, validTime, run, verticalKind, level, signal } = {}) {
  const options = { product, validTime, run, verticalKind, level };
  const cached = getCachedAromeFrame(options);
  if (cached) return Promise.resolve(cached);
  const cacheKey = frameCacheKey(options);
  // Solo se comparten las descargas de precarga, que nunca se abortan; colgar
  // una selección del usuario de una petición cancelable la dejaría sin frame.
  const inFlight = pendingFrames.get(cacheKey);
  if (inFlight) return inFlight;
  const params = new URLSearchParams({
    product,
    valid_time: validTime,
    revision: FORECAST_DATA_REVISION
  });
  if (run) params.set('run', run);
  if (verticalKind) params.set('vertical_kind', verticalKind);
  if (level != null) params.set('level', String(level));
  const request = fetch(`${API_BASE}/v1/forecast/arome/frames.grid?${params}`, {
    headers: { Accept: 'application/vnd.meteolabx.arome-grid' },
    signal
  }).then(async (response) => {
    if (!response.ok) {
      let detail = `Forecast API ${response.status}`;
      try {
        const payload = await response.json();
        detail = describeApiDetail(payload.detail) || detail;
      } catch {
        // La respuesta puede no ser JSON si el proxy todavía no está listo.
      }
      throw new Error(detail);
    }
    const buffer = await response.arrayBuffer();
    const view = new DataView(buffer);
    const headerLength = view.getUint32(0, true);
    const header = shareFrameGeometry(JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength))));
    if (!header.boundaries?.length) {
      // Formato 3 en adelante: los contornos llegan por su propio endpoint.
      header.boundaries = await fetchDomainBoundaries().catch(() => []);
    }
    return rememberAromeFrame(options, decodeFrameBody(buffer, header, 4 + headerLength));
  });
  if (!signal) {
    pendingFrames.set(cacheKey, request);
    // Se atienden ambos desenlaces para que un fallo de precarga no quede como
    // rechazo sin gestionar.
    const forget = () => pendingFrames.delete(cacheKey);
    request.then(forget, forget);
  }
  return request;
}

/**
 * Descarga horas contiguas en segundo plano para que el deslizador no espere.
 *
 * Se ignoran los errores a propósito: una hora que aún no está publicada no
 * debe ensuciar la vista, y al seleccionarla se pedirá de nuevo mostrando el
 * estado que corresponda.
 */
export function prefetchAromeFrames(optionsList = []) {
  for (const options of optionsList) {
    if (!options?.product || !options?.validTime) continue;
    if (getCachedAromeFrame(options) || pendingFrames.has(frameCacheKey(options))) continue;
    fetchAromeFrame(options).catch(() => {});
  }
}

export function aromeTileUrl({ product, validTime, z = '{z}', x = '{x}', y = '{y}' }) {
  const params = new URLSearchParams({ product, valid_time: validTime });
  return `${API_BASE}/v1/forecast/arome/tiles/${z}/${x}/${y}.png?${params}`;
}

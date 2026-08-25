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
const FORECAST_DATA_REVISION = 'forecast-fields-v14';
const FRAME_CACHE_MAX_BYTES = 192 * 1024 * 1024;
const frameCache = new Map();
const geometryCache = new Map();
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

function shareFrameGeometry(header) {
  if (!header.boundaries?.length) return header;
  const key = [header.calculation_scope || '', header.width, header.height, ...(header.bounds || [])].join('|');
  const shared = geometryCache.get(key);
  if (shared) header.boundaries = shared;
  else geometryCache.set(key, header.boundaries);
  return header;
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
  const params = new URLSearchParams({
    product,
    valid_time: validTime,
    revision: FORECAST_DATA_REVISION
  });
  if (run) params.set('run', run);
  if (verticalKind) params.set('vertical_kind', verticalKind);
  if (level != null) params.set('level', String(level));
  return fetch(`${API_BASE}/v1/forecast/arome/frames.grid?${params}`, {
    headers: { Accept: 'application/vnd.meteolabx.arome-grid' },
    signal
  }).then(async (response) => {
    if (!response.ok) {
      let detail = `Forecast API ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // La respuesta puede no ser JSON si el proxy todavía no está listo.
      }
      throw new Error(detail);
    }
    const buffer = await response.arrayBuffer();
    const view = new DataView(buffer);
    const headerLength = view.getUint32(0, true);
    const header = shareFrameGeometry(JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength))));
    const cellCount = header.width * header.height;
    const matrixBytes = cellCount * Float32Array.BYTES_PER_ELEMENT;
    let offset = 4 + headerLength;
    const readMatrix = () => {
      const matrix = new Float32Array(buffer.slice(offset, offset + matrixBytes));
      offset += matrixBytes;
      return matrix;
    };
    const values = readMatrix();
    const u = header.has_vectors ? readMatrix() : null;
    const v = header.has_vectors ? readMatrix() : null;
    const overlay = header.has_overlay ? readMatrix() : null;
    return rememberAromeFrame(options, { ...header, values, u, v, overlay });
  });
}

export function aromeTileUrl({ product, validTime, z = '{z}', x = '{x}', y = '{y}' }) {
  const params = new URLSearchParams({ product, valid_time: validTime });
  return `${API_BASE}/v1/forecast/arome/tiles/${z}/${x}/${y}.png?${params}`;
}

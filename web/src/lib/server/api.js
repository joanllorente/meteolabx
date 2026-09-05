/**
 * Cliente del backend FastAPI para el renderizado en servidor.
 *
 * El front vive en su propio servicio, así que la API se alcanza por su URL
 * pública o por la red interna de Railway; `METEOLABX_API_URL` decide cuál.
 * Nada de esto llega al navegador: las llamadas se hacen durante el `load`.
 */
import { env } from '$env/dynamic/private';

const DEFAULT_API_URL = 'http://127.0.0.1:8000';

export function apiBaseUrl() {
  return (env.METEOLABX_API_URL || DEFAULT_API_URL).replace(/\/+$/, '');
}

/** Segundos que esperamos al backend antes de rendirnos y pintar sin datos. */
const DEFAULT_TIMEOUT_MS = Number(env.METEOLABX_API_TIMEOUT_MS || 8000);

export class ApiError extends Error {
  constructor(status, body) {
    super(`API ${status}: ${body?.detail ?? body?.error_code ?? 'error'}`);
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = 'GET', body, fetch: fetchImpl = fetch, timeoutMs } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`${apiBaseUrl()}${path}`, {
      method,
      signal: controller.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, payload);
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

/** Ficha completa de una estación por red e identificador. */
export function fetchStation(provider, stationId, options = {}) {
  return request(
    `/v1/stations/${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}`,
    options
  );
}

/** Ficha de catálogo a partir del slug de la URL indexable. */
export function fetchStationByUrlSlug(slug, options = {}) {
  return request(`/v1/stations/by-url-slug/${encodeURIComponent(slug)}`, options);
}

/**
 * Identificador que espera el backend de observaciones.
 *
 * IEM agrega decenas de redes y sus identificadores solo son únicos dentro de
 * cada una, así que sus endpoints exigen `RED|ID`. El catálogo, en cambio,
 * devuelve las dos partes por separado: mandarle el `station_id` a secas
 * hacía que la estación resolviera su ficha pero no sus datos.
 */
function providerStationId(station) {
  const id = String(station.station_id || '');
  if (String(station.provider || '').toUpperCase() !== 'IEM') return id;
  if (!station.network || id.includes('|')) return id;
  return `${station.network}|${id}`;
}

/**
 * Observación completa en una sola petición: valores actuales, derivadas
 * termodinámicas, extremos del día y serie. Es el mismo endpoint que alimenta
 * la pestaña de observación de la app.
 */
export function fetchProcessedObservation(station, options = {}) {
  return request('/v1/observations/current/processed', {
    ...options,
    method: 'POST',
    body: {
      provider: station.provider,
      station_id: providerStationId(station),
      sun_tz_name: station.tz || '',
      station_elevation: station.elevation ?? null,
      // Weather Underground y WeatherLink se consultan con la credencial de
      // quien mira: el resto de redes usan las del servidor y las ignoran.
      api_key: station.apiKey || '',
      api_secret: station.apiSecret || ''
    }
  });
}

/**
 * Serie sinóptica de los últimos días: la pestaña de tendencias.
 *
 * El backend la sirve a ~1 punto/hora y ya trae las derivadas de presión, θe
 * y razón de mezcla calculadas, que es justo lo que pintan las gráficas.
 */
export function fetchRecentSeries(station, { daysBack = 7 } = {}, options = {}) {
  return request('/v1/observations/series/recent', {
    ...options,
    method: 'POST',
    body: {
      provider: station.provider,
      station_id: providerStationId(station),
      days_back: daysBack,
      station_elevation: station.elevation ?? null
    }
  });
}

/** Serie del día, para ver las mismas tendencias con detalle intradía. */
export function fetchTodaySeries(station, options = {}) {
  return request('/v1/observations/series/today', {
    ...options,
    method: 'POST',
    body: {
      provider: station.provider,
      station_id: providerStationId(station),
      station_elevation: station.elevation ?? null
    }
  });
}

// Redes que publican histórico. Fuera de esta lista la pestaña no tiene nada
// que enseñar y se dice, en vez de pedir un dataset que el backend rechazará.
export const HISTORICAL_PROVIDERS = new Set([
  'WU', 'AEMET', 'METEOCAT', 'METEOFRANCE', 'METEOGALICIA', 'FROST',
  'WEATHERLINK', 'IEM', 'GEOSPHERE', 'SMHI', 'ECCC'
]);

/**
 * Histórico ya resumido: tarjetas, climograma y tabla.
 *
 * El cálculo lo hace `domain/climograms.py` en el servidor —el mismo que usa
 * la app actual—, así que las dos interfaces enseñan los mismos números.
 */
export function fetchClimoSummary(
  station,
  { language, summaryMode, periods = [], selectedMonths = [], selectedYears = [], blocks = 1, units },
  options = {}
) {
  return request('/v1/climo/summary', {
    ...options,
    method: 'POST',
    // Cada bloque mes×año es una descarga entera al proveedor: seis agostos
    // de Meteocat en frío son tres cuartos de minuto. Un plazo fijo cortaba
    // justo las consultas que más tardan —las de comparar varios años—, y el
    // corte se veía como «no hay datos».
    timeoutMs: Math.min(180000, 20000 + 15000 * Math.max(1, blocks)),
    body: {
      provider: station.provider,
      station_id: providerStationId(station),
      summary_mode: summaryMode,
      // O los bloques ya construidos, o la selección en bruto: el backend
      // construye los periodos con la misma regla que la app actual.
      periods,
      selected_months: selectedMonths,
      selected_years: selectedYears,
      language,
      unit_preferences: units
    }
  });
}

/**
 * Puntos del mapa: la última lectura de cada estación con datos.
 *
 * Las tres capas salen del mismo refresco horario del ranking, así que pedir
 * una u otra cuesta lo mismo: es memoria del backend, no una ronda a los
 * proveedores.
 */
const MAP_LAYERS = {
  temperature: '/v1/stations/current-temperatures',
  wind: '/v1/stations/current-winds',
  precipitation: '/v1/stations/precipitations-24h'
};

/**
 * Catálogo de estaciones alrededor de un punto, para el modo «Estaciones».
 *
 * No depende del refresco del ranking: sale del catálogo SQLite, así que el
 * mapa de conexión funciona aunque no haya observaciones cargadas.
 */
/**
 * Catálogo de un país en formato compacto para el mapa.
 *
 * Devuelve arrays paralelos con lo justo para pintar: España baja de 3,4 MB
 * a 670 KB frente a la ficha completa por estación.
 */
export function fetchMapCatalog(filters, options = {}) {
  const params = new URLSearchParams({
    countries: filters.countries.join(','),
    limit: String(filters.limit ?? 60000)
  });
  if (filters.sensors?.length) params.set('sensors', filters.sensors.join(','));
  for (const [key, value] of [
    ['has_historical', filters.onlyHistorical],
    ['hide_historical_only', filters.hideArchived],
    ['hide_manual', filters.hideManual],
    ['hide_amateur', filters.hideAmateur]
  ]) {
    if (value) params.set(key, 'true');
  }
  return request(`/v1/stations/map-catalog?${params}`, options);
}

/**
 * País a partir de una zona horaria IANA.
 *
 * Es la aproximación que usa la aplicación actual cuando no hay posición del
 * usuario: `Europe/Madrid` → ES. Cobertura mundial y sin pedir permisos.
 */
export function fetchCountryByTimezone(timezone, options = {}) {
  return request(`/v1/stations/country-by-tz?tz=${encodeURIComponent(timezone)}`, options);
}

/** Países con estaciones en el catálogo, con su recuento. */
export function fetchStationCountries(options = {}) {
  return request('/v1/stations/countries', options);
}

export function fetchStationsNear(
  { lat, lon, radiusKm = 400, limit = 3000, hideHistoricalOnly = true, hideAmateur = false },
  options = {}
) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
    radius_km: String(radiusKm),
    limit: String(limit),
    hide_historical_only: String(hideHistoricalOnly)
  });
  // Netatmo y Windy son casi todo lo que hay cerca en ciudad: se enseñan por
  // defecto y quien no las quiera las apaga.
  if (hideAmateur) params.set('hide_amateur', 'true');
  return request(`/v1/stations/near?${params}`, options);
}

export function fetchMapPoints(layer, options = {}) {
  return request(MAP_LAYERS[layer] || MAP_LAYERS.temperature, options);
}

/**
 * Ranking diario. Devuelve las cuatro métricas de golpe, así que cambiar de
 * pestaña dentro de la página no cuesta otra petición al proveedor.
 */
export function fetchRanking(
  { country = '', day = '', limit = 10, order = '', exclude = '' } = {},
  options = {}
) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (country) params.set('country', country);
  if (day) params.set('day', day);
  // `metrica:asc|desc`; solo altera la métrica listada, el resto conserva su
  // orden natural (máxima descendente, mínima ascendente).
  if (order) params.set('order', order);
  // ISO2 a excluir. La Antártida copa siempre las mínimas globales.
  if (exclude) params.set('exclude', exclude);
  return request(`/v1/ranking?${params}`, options);
}

/** Países con datos de ranking hoy, para el selector. */
export function fetchRankingCountries(options = {}) {
  return request('/v1/ranking/countries', options);
}

/** Geocodificación textual: «Girona» → coordenadas. Alimenta la búsqueda. */
export function geocode(query, { language = 'es' } = {}, options = {}) {
  const params = new URLSearchParams({ q: query, lang: `${language},en` });
  return request(`/v1/stations/geocode?${params}`, options);
}

/** Estaciones indexables cerca de un punto arbitrario (búsqueda por lugar). */
export function fetchIndexableNear({ lat, lon, limit = 12 }, options = {}) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
    limit: String(limit)
  });
  return request(`/v1/stations/indexable-near?${params}`, options);
}

/**
 * Estaciones de una cuenta de WeatherLink.
 *
 * Se piden desde el navegador con las credenciales de quien mira: son suyas y
 * no se guardan en el servidor.
 */
export function fetchWeatherLinkStations({ apiKey, apiSecret }, options = {}) {
  return request('/v1/stations/weatherlink', {
    ...options,
    method: 'POST',
    body: { api_key: apiKey, api_secret: apiSecret }
  });
}

/** Página del catálogo indexable; la usa el generador de sitemaps. */
export function fetchIndexableCatalog({ offset = 0, limit = 10000 } = {}, options = {}) {
  return request(`/v1/stations/indexable?offset=${offset}&limit=${limit}`, options);
}

/**
 * Vecinas indexables de una ficha: el bloque de «estaciones cercanas».
 *
 * Son enlaces internos entre páginas que ya están posicionadas, así que se
 * piden con el mismo criterio que usaba el generador estático: las seis más
 * próximas del catálogo publicable.
 */
export function fetchNearbyIndexableStations(station, { limit = 6 } = {}, options = {}) {
  const params = new URLSearchParams({
    lat: String(station.lat),
    lon: String(station.lon),
    limit: String(limit),
    exclude_provider: station.provider,
    exclude_station_id: station.station_id
  });
  return request(`/v1/stations/indexable-near?${params}`, options);
}

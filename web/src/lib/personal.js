/**
 * Consulta desde el navegador de las redes con credencial propia.
 *
 * Weather Underground y WeatherLink no tienen catálogo público: cada quien
 * pide SU estación con SU clave. Como la clave vive en este navegador, la
 * petición sale de aquí y no del servidor —que igualmente es quien habla con
 * el proveedor, pero solo durante esa llamada.
 */
/**
 * Cada cuánto vuelve a preguntarse por el dato, según la red.
 *
 * Son los mismos intervalos que usa la app actual, y salen de cómo publica
 * cada proveedor: Weather Underground sirve la observación actual con cada
 * envío de la consola —diez o quince segundos—, mientras que Met Office o SMHI
 * publican una vez por hora y preguntar antes solo gasta cuota.
 */
export const REFRESH_SECONDS = {
  // Weather Underground publica con cada envío de la consola, cada diez o
  // quince segundos. Preguntando cada 30 se veía un dato de hasta medio minuto
  // —y hasta uno entero sumando el caché del servidor—, que con el contador de
  // antigüedad a la vista canta mucho. La cuota que se gasta es la del dueño de
  // la estación, que es quien mira.
  WU: 15,
  WEATHERLINK: 60,
  FROST: 300,
  METEOFRANCE: 300,
  POEM: 300,
  WINDY: 300,
  AEMET: 600,
  METEOCAT: 600,
  EUSKALMET: 600,
  METEOGALICIA: 600,
  NWS: 600,
  GEOSPHERE: 600,
  ECCC: 600,
  NETATMO: 600,
  IEM: 600,
  METOFFICE: 3600,
  IPMA: 3600,
  SMHI: 3600,
  CLIMANTARTIDE: 3600
};

const DEFAULT_REFRESH_SECONDS = 600;

export function refreshSecondsFor(provider) {
  return REFRESH_SECONDS[String(provider || '').toUpperCase()] || DEFAULT_REFRESH_SECONDS;
}

export async function fetchPersonalObservation({
  provider,
  stationId,
  apiKey,
  apiSecret = '',
  elevation = null,
  calibration = null
}) {
  const response = await fetch('/v1/observations/current/processed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider,
      station_id: stationId,
      api_key: apiKey,
      api_secret: apiSecret,
      // Sin altitud no salen la presión al nivel del mar, la densidad del aire
      // ni la base de nube: estas redes no siempre la publican, así que se
      // pide al conectar.
      station_elevation: Number.isFinite(elevation) ? elevation : null,
      // Los offsets de los sensores, cuando los hay. El backend los suma
      // antes de derivar, así que el rocío y la presión al nivel del mar
      // salen ya del valor corregido.
      ...(calibration ? { calibration } : {})
    })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail?.error_code || body?.error_code || `http_${response.status}`);
  }
  return response.json();
}

/** Estaciones de una cuenta de WeatherLink, para elegir cuál conectar. */
export async function fetchMyWeatherLinkStations({ apiKey, apiSecret }) {
  const response = await fetch('/v1/stations/weatherlink', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail?.error_code || body?.error_code || `http_${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload?.stations) ? payload.stations : [];
}

/**
 * Serie del día de una estación propia.
 *
 * Mismo endpoint que usan las redes públicas desde el servidor; lo único que
 * cambia es de dónde sale la credencial, que aquí viaja con la petición.
 */
export async function fetchPersonalTodaySeries({
  provider,
  stationId,
  apiKey,
  apiSecret = '',
  elevation = null,
  calibration = null
}) {
  return personalPost('/v1/observations/series/today', {
    provider,
    station_id: stationId,
    api_key: apiKey,
    api_secret: apiSecret,
    station_elevation: Number.isFinite(elevation) ? elevation : null,
    ...(calibration ? { calibration } : {})
  });
}

/** Ventana de varios días de una estación propia, para la vista sinóptica. */
export async function fetchPersonalRecentSeries({
  provider,
  stationId,
  apiKey,
  apiSecret = '',
  elevation = null,
  calibration = null,
  daysBack = 7
}) {
  return personalPost('/v1/observations/series/recent', {
    provider,
    station_id: stationId,
    api_key: apiKey,
    api_secret: apiSecret,
    days_back: daysBack,
    station_elevation: Number.isFinite(elevation) ? elevation : null,
    ...(calibration ? { calibration } : {})
  });
}

/**
 * Histórico resumido de una estación propia.
 *
 * Cada bloque mes×año es una descarga entera al proveedor, así que el plazo
 * crece con la selección igual que en el servidor: un tiempo fijo cortaba
 * justo las consultas de varios años y el corte se leía como «sin datos».
 */
export async function fetchPersonalClimoSummary({
  provider,
  stationId,
  apiKey,
  apiSecret = '',
  language,
  summaryMode,
  selectedMonths = [],
  selectedYears = [],
  blocks = 1,
  units = { temperature: '°C', precipitation: 'mm', wind: 'km/h' }
}) {
  return personalPost(
    '/v1/climo/summary',
    {
      provider,
      station_id: stationId,
      api_key: apiKey,
      api_secret: apiSecret,
      summary_mode: summaryMode,
      periods: [],
      selected_months: selectedMonths,
      selected_years: selectedYears,
      language,
      unit_preferences: units
    },
    { timeoutMs: Math.min(180000, 20000 + 15000 * Math.max(1, blocks)) }
  );
}

/** POST con credenciales: mismo trato del error en las tres llamadas. */
async function personalPost(path, body, { timeoutMs = 0 } = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload?.detail?.error_code || payload?.error_code || `http_${response.status}`
    );
  }
  return response.json();
}

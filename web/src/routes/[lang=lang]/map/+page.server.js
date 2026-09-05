import {
  fetchCountryByTimezone,
  fetchMapCatalog,
  fetchMapPoints,
  fetchStationCountries
} from '$lib/server/api.js';

const LAYERS = {
  estaciones: 'stations',
  temperatura: 'temperature',
  viento: 'wind',
  precipitacion: 'precipitation'
};

// Último recurso, cuando no hay país en la URL ni zona horaria conocida.
const LANGUAGE_COUNTRY = { es: 'ES', fr: 'FR', it: 'IT', pt: 'PT', ca: 'ES', en: 'ES' };

// La zona horaria del navegador, que deja el cliente en una cookie. Es la
// misma aproximación que usa la app actual cuando no tiene la posición del
// usuario: cubre el mundo entero y no pide permisos.
const TIMEZONE_COOKIE = 'mlx_tz';

const SENSORS = [
  'thermometer', 'hygrometer', 'barometer', 'anemometer',
  'wind_vane', 'rain_gauge', 'pyranometer', 'uv'
];

// Tope de seguridad: Estados Unidos tiene 194.000 estaciones y cargarlas
// todas serían decenas de megas antes de ver un punto.
const MAX_STATIONS = 60_000;

/**
 * Mapa.
 *
 * «Estaciones» enseña el catálogo completo de los países elegidos, sin
 * agrupar: son decenas de miles de puntos y los dibuja la GPU. Todos los
 * filtros viajan en la URL, así que una vista concreta —«las estaciones
 * francesas con barómetro y sin particulares»— se puede enlazar.
 */
export async function load({ params, url, fetch, cookies, setHeaders }) {
  const layer = LAYERS[url.searchParams.get('capa') || ''] || 'stations';
  const countriesPromise = fetchStationCountries({ fetch }).catch(() => ({}));
  const filters = await readFilters(url.searchParams, params.lang, cookies, fetch);

  let catalog = null;
  let points = [];
  let updatedAt = null;

  if (layer === 'stations') {
    catalog = await fetchMapCatalog(
      { ...filters, limit: MAX_STATIONS },
      { fetch }
    ).catch(() => null);
  } else {
    const payload = await fetchMapPoints(layer, { fetch }).catch(() => null);
    points = payload?.points ?? [];
    updatedAt = payload?.updated_at ?? null;
  }

  // Sin país en la URL la respuesta depende de la cookie de zona horaria, y
  // una caché compartida serviría el país de otro.
  setHeaders({
    'cache-control': url.searchParams.has('pais')
      ? 'public, max-age=300, stale-while-revalidate=900'
      : 'private, max-age=60'
  });

  return {
    lang: params.lang,
    layer,
    filters,
    sensorKeys: SENSORS,
    countries: await countriesPromise,
    catalog,
    points,
    updatedAt,
    centre: readCentre(url.searchParams, catalog)
  };
}

async function readFilters(params, language, cookies, fetch) {
  const countries = params
    .getAll('pais')
    .flatMap((value) => value.split(','))
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);

  // El formulario manda un `sensores` por casilla marcada; escrito a mano
  // suele venir uno solo separado por comas. Se aceptan las dos formas.
  const sensors = params
    .getAll('sensores')
    .flatMap((value) => value.split(','))
    .map((value) => value.trim().toLowerCase())
    .filter((value) => SENSORS.includes(value));

  return {
    countries: countries.length ? countries : [await defaultCountry(language, cookies, fetch)],
    sensors,
    onlyHistorical: params.get('solo-historico') === 'si',
    hideArchived: params.get('ocultar-archivadas') === 'si',
    hideManual: params.get('ocultar-manuales') === 'si',
    hideAmateur: params.get('ocultar-particulares') === 'si'
  };
}

/**
 * País de partida cuando la URL no dice ninguno.
 *
 * Mismo orden que la aplicación actual: primero la zona horaria del
 * navegador —que la deja el cliente en una cookie— y, si no la hay todavía,
 * el idioma de la página.
 */
async function defaultCountry(language, cookies, fetch) {
  const timezone = cookies?.get(TIMEZONE_COOKIE);
  if (timezone) {
    const payload = await fetchCountryByTimezone(timezone, { fetch }).catch(() => null);
    if (payload?.country) return String(payload.country).toUpperCase();
  }
  return LANGUAGE_COUNTRY[language] || 'ES';
}

/**
 * Centro del mapa. Sin coordenadas en la URL se encuadra lo cargado, para que
 * al cambiar de país el mapa vaya allí en vez de quedarse donde estaba.
 */
function readCentre(params, catalog) {
  const lat = Number(params.get('lat'));
  const lon = Number(params.get('lon'));
  const zoom = Number(params.get('zoom'));
  const usable =
    params.has('lat') && params.has('lon') &&
    Number.isFinite(lat) && Number.isFinite(lon) &&
    Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
  if (usable) return { lat, lon, zoom: Number.isFinite(zoom) ? zoom : 9 };

  const size = catalog?.lat?.length ?? 0;
  if (!size) return { lat: 41.39, lon: 2.15, zoom: 5 };
  const mean = (values) => values.reduce((total, value) => total + value, 0) / values.length;
  return { lat: mean(catalog.lat), lon: mean(catalog.lon), zoom: 5 };
}

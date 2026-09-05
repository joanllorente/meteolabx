import { fetchRanking, fetchRankingCountries } from '$lib/server/api.js';

const METRICS = ['tmax', 'tmin', 'gust', 'rain'];
const LIMIT = 10;

// Orden natural de cada métrica: la máxima más alta primero, la mínima más
// baja primero. El resto siempre de mayor a menor.
const NATURAL_DESC = { tmax: true, tmin: false, gust: true, rain: true };

const LANGUAGE_COUNTRY = { es: 'ES', fr: 'FR', it: 'IT', pt: 'PT', ca: 'ES', en: 'ES' };
const TIMEZONE_COOKIE = 'mlx_tz';

/**
 * Ranking del día.
 *
 * Ámbito, país, métrica, día y orden viajan en la URL: cada lista concreta
 * —«la racha máxima de ayer en Francia, de menor a mayor»— se puede enlazar.
 * El backend devuelve las cuatro métricas en la misma respuesta, así que
 * cambiar de tarjeta no cuesta otra consulta a los proveedores.
 */
export async function load({ params, url, fetch, cookies, setHeaders }) {
  const { lang } = params;
  const metric = METRICS.includes(url.searchParams.get('metrica'))
    ? url.searchParams.get('metrica')
    : 'tmax';

  const global = url.searchParams.get('ambito') === 'global';
  const requested = (url.searchParams.get('pais') || '').trim().toUpperCase();
  const country = global ? '' : requested || (await defaultCountry(lang, cookies, fetch));

  const day = (url.searchParams.get('dia') || '').trim();

  // Sin la Antártida el ranking mundial de mínimas deja de ser una lista de
  // bases polares. Solo tiene sentido en global: filtrando por país ya está
  // fuera salvo que se pida.
  const withoutAntarctica = global && url.searchParams.get('sin-antartida') === 'si';

  // Invertir solo tiene sentido en las temperaturas: en racha y lluvia el
  // «menor primero» es una lista de estaciones sin nada que contar.
  const reversible = metric === 'tmax' || metric === 'tmin';
  const reversed = reversible && url.searchParams.get('orden') === 'inverso';
  const descending = reversed ? !NATURAL_DESC[metric] : NATURAL_DESC[metric];
  const order = reversed ? `${metric}:${descending ? 'desc' : 'asc'}` : '';

  const [ranking, countries] = await Promise.all([
    fetchRanking(
      { country, day, limit: LIMIT, order, exclude: withoutAntarctica ? 'AQ' : '' },
      { fetch }
    ).catch(() => null),
    fetchRankingCountries({ fetch })
      .then((payload) => payload.countries || [])
      .catch(() => [])
  ]);

  setHeaders({
    'cache-control': url.searchParams.has('pais') || global
      ? 'public, max-age=300, stale-while-revalidate=1800'
      : 'private, max-age=120'
  });

  return {
    lang,
    metric,
    metrics: METRICS,
    global,
    country,
    withoutAntarctica,
    reversible,
    reversed,
    descending,
    limit: LIMIT,
    countries,
    ranking: ranking ?? { providers: [], units: {}, metrics: {}, days: [], day: '' }
  };
}

/** Mismo orden que el mapa: zona horaria del navegador y luego el idioma. */
async function defaultCountry(language, cookies, fetch) {
  const timezone = cookies?.get(TIMEZONE_COOKIE);
  if (timezone) {
    const { fetchCountryByTimezone } = await import('$lib/server/api.js');
    const payload = await fetchCountryByTimezone(timezone, { fetch }).catch(() => null);
    if (payload?.country) return String(payload.country).toUpperCase();
  }
  return LANGUAGE_COUNTRY[language] || 'ES';
}

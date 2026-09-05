import { error, redirect } from '@sveltejs/kit';

import {
  ApiError,
  fetchRecentSeries,
  fetchStationByUrlSlug,
  fetchTodaySeries
} from '$lib/server/api.js';
import { observationPath, primaryLanguage, stationLanguages, stationMeta } from '$lib/seo/station.js';

const DAYS_BACK = 7;

/**
 * Tendencias de una estación.
 *
 * El rango se elige por URL (`?rango=hoy`) en vez de con estado en el cliente:
 * así la vista es compartible, se renderiza entera en el servidor y el
 * selector funciona sin JavaScript.
 */
export async function load({ params, url, fetch, setHeaders }) {
  const { lang, slug } = params;
  // «Hoy» por defecto: es lo que se mira al abrir la pestaña. La ventana
  // sinóptica de varios días se pide expresamente.
  const range = url.searchParams.get('rango') === 'sinoptica' ? 'synoptic' : 'today';

  let station;
  try {
    station = await fetchStationByUrlSlug(slug, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) error(404, 'station_not_found');
    throw cause;
  }

  if (station.url_slug !== slug) {
    redirect(301, `/${lang}/trends/${station.url_slug}${url.search}`);
  }
  const languages = stationLanguages(station);
  if (!languages.includes(lang)) {
    redirect(301, `/${primaryLanguage(station)}/trends/${station.url_slug}${url.search}`);
  }

  // Si el proveedor no sirve el rango pedido, la página se pinta igual con el
  // aviso: una tendencia ausente no es un error de la aplicación.
  const series = await (range === 'today'
    ? fetchTodaySeries(station, { fetch })
    : fetchRecentSeries(station, { daysBack: DAYS_BACK }, { fetch })
  ).catch(() => null);

  setHeaders({ 'cache-control': 'public, max-age=300, stale-while-revalidate=900' });

  return {
    lang,
    slug: station.url_slug,
    station,
    meta: stationMeta(station, lang, station.url_slug),
    series,
    range,
    daysBack: DAYS_BACK,
    observationPath: observationPath(lang, station.url_slug)
  };
}

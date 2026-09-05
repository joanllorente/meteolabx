import { error, redirect } from '@sveltejs/kit';

import {
  ApiError,
  fetchRecentSeries,
  fetchStation,
  fetchTodaySeries
} from '$lib/server/api.js';

// Redes con credencial personal; se resuelven en el navegador.
const PERSONAL_PROVIDERS = ['WU', 'WEATHERLINK'];

const DAYS_BACK = 7;

/**
 * Tendencias de una estación identificada por red e identificador.
 *
 * La hermana de `trends/[slug]` para las estaciones sin ficha indexable. Las
 * propias —Weather Underground, WeatherLink— se piden desde el navegador,
 * porque la credencial vive allí; el resto se resuelven aquí, igual que las
 * que tienen slug.
 */
export async function load({ params, url, fetch, setHeaders }) {
  const { lang } = params;
  const provider = decodeURIComponent(params.provider).toUpperCase();
  const stationId = decodeURIComponent(params.stationId);
  const range = url.searchParams.get('rango') === 'sinoptica' ? 'synoptic' : 'today';

  if (PERSONAL_PROVIDERS.includes(provider)) {
    setHeaders({ 'cache-control': 'private, no-store' });
    return {
      lang,
      provider,
      stationId,
      station: null,
      series: null,
      range,
      daysBack: DAYS_BACK,
      personal: true
    };
  }

  let station;
  try {
    station = await fetchStation(provider, stationId, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) error(404, 'station_not_found');
    throw cause;
  }

  // Una sola URL por vista: si la estación tiene ficha indexable, sus
  // tendencias viven en la del slug.
  const slugPayload = await fetch(
    `/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`
  )
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  if (slugPayload?.url_slug) {
    redirect(301, `/${lang}/trends/${slugPayload.url_slug}${url.search}`);
  }

  const series = await (range === 'today'
    ? fetchTodaySeries(station, { fetch })
    : fetchRecentSeries(station, { daysBack: DAYS_BACK }, { fetch })
  ).catch(() => null);

  setHeaders({ 'cache-control': 'public, max-age=300, stale-while-revalidate=900' });

  return {
    lang,
    provider,
    stationId,
    station,
    series,
    range,
    daysBack: DAYS_BACK,
    personal: false
  };
}

import { error, redirect } from '@sveltejs/kit';

import { ApiError, fetchProcessedObservation, fetchStation } from '$lib/server/api.js';
import { observationPath } from '$lib/seo/station.js';

// Redes con credencial personal; se resuelven en el navegador.
const PERSONAL_PROVIDERS = ['WU', 'WEATHERLINK'];

/**
 * Panel de una estación identificada por red e identificador.
 *
 * Existe porque no todas las estaciones tienen URL indexable: el slug
 * `nombre-id` solo es único a nivel mundial entre las redes oficiales, y en
 * IEM, Netatmo o Windy hay miles de nombres repetidos. Sus datos sí se
 * sirven igual que los de cualquier otra, así que se consultan por aquí.
 *
 * Cuando la estación SÍ tiene slug, esta ruta redirige a él: una sola URL
 * canónica por ficha.
 */
export async function load({ params, fetch, setHeaders }) {
  const { lang } = params;
  const provider = decodeURIComponent(params.provider).toUpperCase();
  const stationId = decodeURIComponent(params.stationId);

  // Weather Underground y WeatherLink se consultan con la credencial de quien
  // mira, y esa credencial vive en su navegador: aquí no hay con qué pedir el
  // dato. La página se pinta vacía y el cliente la rellena. No se pierde nada:
  // son fichas privadas, sin nada que indexar.
  if (PERSONAL_PROVIDERS.includes(provider)) {
    setHeaders({ 'cache-control': 'private, no-store' });
    return { lang, provider, stationId, station: null, observation: null, personal: true };
  }

  let station;
  try {
    station = await fetchStation(provider, stationId, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) error(404, 'station_not_found');
    throw cause;
  }

  const slugPayload = await fetch(
    `/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`
  )
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);

  if (slugPayload?.url_slug) {
    redirect(301, observationPath(lang, slugPayload.url_slug));
  }

  const observation = await fetchProcessedObservation(station, { fetch }).catch((cause) => ({
    unavailable: {
      status: cause instanceof ApiError ? cause.status : 0,
      code: cause instanceof ApiError ? cause.body?.error_code || 'provider_error' : 'unreachable'
    }
  }));

  setHeaders({ 'cache-control': 'public, max-age=60, stale-while-revalidate=300' });

  return { lang, provider, stationId, station, observation, personal: false };
}

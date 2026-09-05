import { error, redirect } from '@sveltejs/kit';

import { ApiError, fetchProcessedObservation, fetchStationByUrlSlug } from '$lib/server/api.js';
import {
  observationPath,
  primaryLanguage,
  stationLanguages,
  stationMeta
} from '$lib/seo/station.js';

/**
 * Ficha de estación renderizada en servidor.
 *
 * Google tiene que recibir el panel con los valores dentro, no un esqueleto:
 * de ahí que la observación se pida aquí y no al montar el componente. Si el
 * proveedor falla, la página se sirve igual con la ficha del catálogo —los
 * metadatos y el contenido indexable no dependen de que la estación esté
 * publicando ahora mismo.
 */
export async function load({ params, fetch, setHeaders }) {
  const { lang, slug } = params;

  let station;
  try {
    station = await fetchStationByUrlSlug(slug, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) {
      error(404, 'station_not_found');
    }
    throw cause;
  }

  // El slug canónico manda: una mayúscula o un alias antiguo redirigen en vez
  // de servir la misma ficha en dos URLs distintas.
  if (station.url_slug !== slug) {
    redirect(301, observationPath(lang, station.url_slug));
  }

  // Cada ficha existe solo en los idiomas de su país; pedir /it/ de una
  // estación noruega crearía una página que nunca estuvo indexada.
  const languages = stationLanguages(station);
  if (!languages.includes(lang)) {
    redirect(301, observationPath(primaryLanguage(station), station.url_slug));
  }

  const meta = stationMeta(station, lang, station.url_slug);

  // Que el proveedor falle no puede tumbar la página: la ficha se sirve
  // igual y el panel lo dice.
  const observation = await fetchProcessedObservation(station, { fetch }).catch((cause) => ({
    unavailable: describeFailure(cause)
  }));

  // Un minuto en CDN y cinco sirviendo el anterior mientras se revalida: las
  // estaciones publican cada 10-30 minutos, así que no hay nada que ganar
  // pegándole al proveedor en cada visita.
  setHeaders({ 'cache-control': 'public, max-age=60, stale-while-revalidate=300' });

  return { lang, slug: station.url_slug, station, meta, observation };
}

function describeFailure(cause) {
  if (cause instanceof ApiError) {
    return { status: cause.status, code: cause.body?.error_code || 'provider_error' };
  }
  return { status: 0, code: 'unreachable' };
}

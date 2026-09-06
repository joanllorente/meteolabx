import { error, redirect } from '@sveltejs/kit';
import { languageDecision } from '$lib/server/language.js';
import { LANGUAGE_CODES } from '$lib/seo/i18n.js';

import { ApiError, fetchProcessedObservation, fetchStationByUrlSlug } from '$lib/server/api.js';
import { contentEtag, observationVersion } from '$lib/server/etag.js';
import { describeRequestFailure } from '$lib/observation/unavailable.js';
import {
  observationPath,
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
export async function load({ params, request, cookies, fetch, setHeaders }) {
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



  const meta = stationMeta(station, lang, station.url_slug);

  let replacementPath = '';
  if (station.is_historical_only && station.replacement_station_id) {
    const replacement = await fetch(
      `/v1/stations/url-slug?${new URLSearchParams({
        provider: station.provider,
        station_id: station.replacement_station_id
      })}`
    )
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null);
    if (replacement?.url_slug) {
      replacementPath = observationPath(lang, replacement.url_slug);
    }
  }

  // Que el proveedor falle no puede tumbar la página: la ficha se sirve
  // igual y el panel lo dice.
  const observation = station.is_historical_only
    ? { unavailable: { status: 410, code: 'historical_station' } }
    : await fetchProcessedObservation(station, { fetch }).catch((cause) => ({
        unavailable: describeFailure(cause)
      }));

  const decision = languageDecision({ request, cookies }, LANGUAGE_CODES, lang);

  // Una hora en CDN y cinco minutos sirviendo el anterior mientras se
  // revalida: las estaciones publican cada 10-60 minutos, así que no hay nada
  // que ganar pegándole al proveedor en cada visita. El ETag cierra el resto:
  // quien ya tenga la ficha con esta misma observación recibe un 304.
  const version = observationVersion(observation);
  setHeaders({
    'cache-control': 'public, max-age=3600, stale-while-revalidate=300',
    ...(version
      ? { etag: contentEtag('observation', lang, station.url_slug, decision.language, version, replacementPath) }
      : {})
  });

  return { languageDecision: decision, lang, slug: station.url_slug, station, meta, observation, replacementPath };
}

function describeFailure(cause) {
  return describeRequestFailure(cause, { ApiError });
}

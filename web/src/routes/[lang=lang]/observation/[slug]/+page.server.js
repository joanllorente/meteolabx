import { error, redirect } from '@sveltejs/kit';
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
export async function load({ params, fetch, setHeaders }) {
  const { lang, slug } = params;

  let station;
  try {
    station = await fetchStationByUrlSlug(slug, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) {
      error(404, 'station_not_found');
    }
    console.error('[observation metadata]', cause?.cause || cause);
    error(503, 'station_catalog_unavailable');
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

  // Una hora en el navegador y cinco minutos sirviendo el anterior mientras se
  // revalida: las estaciones publican cada 10-60 minutos, así que no hay nada
  // que ganar pegándole al proveedor en cada visita. El ETag cierra el resto:
  // quien ya tenga la ficha con esta misma observación recibe un 304.
  //
  // El CDN va aparte y con menos cuerda: `hooks.server.js` le añade un
  // `s-maxage` más corto a lo compartible. Una copia del borde la ven todos
  // los visitantes, así que caducarla antes cuesta poco y evita que una ficha
  // se quede una hora enseñando una observación vieja.
  const version = observationVersion(observation);
  setHeaders({
    'cache-control': 'public, max-age=3600, stale-while-revalidate=300',
    ...(version
      ? { etag: contentEtag('observation', lang, station.url_slug, version, replacementPath) }
      : {})
  });

  // La decisión de idioma NO viaja en el HTML: esta página se comparte entre
  // visitantes en el CDN, y un dato de quien pidió primero acabaría contado
  // como el de todos los demás. El navegador sabe sus propios idiomas y los
  // manda aparte al registrar la visita.
  return { lang, slug: station.url_slug, station, meta, observation, replacementPath };
}

function describeFailure(cause) {
  return describeRequestFailure(cause, { ApiError });
}

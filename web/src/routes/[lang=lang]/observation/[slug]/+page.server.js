import { error, redirect } from '@sveltejs/kit';
import { isManualDaily } from '$lib/observation/manual-daily.js';
import { NO_STORE, liveCacheControl } from '$lib/server/cache-control.js';
import { ApiError, fetchLatestDailyPrecip, fetchObservationSnapshot,
  fetchProcessedObservation, fetchStationByUrlSlug } from '$lib/server/api.js';
import { contentEtag, observationVersion } from '$lib/server/etag.js';
import { SNAPSHOT_MISSING, describeRequestFailure } from '$lib/observation/unavailable.js';
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
export async function load({ params, fetch, setHeaders, locals }) {
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

  // El sitemap solo publica los idiomas asignados al país, y la ruta debe
  // aplicar la misma regla. Así una estación polaca escrita bajo `/ca/` no
  // responde con una variante indexable accidental.
  if (!stationLanguages(station).includes(lang)) {
    redirect(301, observationPath(primaryLanguage(station), station.url_slug));
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
  // Pluviómetro manual: no hay lectura actual que pedir, pedirla daba «sin
  // datos» y un error en el panel. Se enseña la última lluvia diaria.
  const crawler = Boolean(locals?.crawler);
  const manual = isManualDaily(station) && !station.is_historical_only;
  const dailyPrecip = manual
    ? await fetchLatestDailyPrecip(station, { fetch }).catch(() => null)
    : null;
  const observation = station.is_historical_only
    ? { unavailable: { status: 410, code: 'historical_station' } }
    : manual
      ? { unavailable: { status: 200, code: 'manual_daily_station' } }
      : crawler
        // Un buscador no consulta al proveedor (ver `hooks.server.js`): recibe
        // la última lectura guardada. Durante un tiempo recibió un 429 y Google
        // vio en cada ficha un «vuelve a intentarlo» sin un solo valor.
        ? await fetchObservationSnapshot(station, { fetch }).catch(() => ({
            unavailable: { status: 404, code: SNAPSHOT_MISSING }
          }))
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
  // Una ficha sin datos no se guarda: el fallo es de este instante, y en caché
  // se repetiría a cada visitante (ver `cache-control.js`). La histórica sí:
  // no publicar observaciones es su estado permanente, no un tropiezo.
  // La manual se guarda si llegó su lluvia; la versión es el día publicado,
  // para que el ETag cambie cuando MeteoSwiss publique el siguiente.
  const consultaBuena = manual
    ? dailyPrecip !== null
    : !observation.unavailable || station.is_historical_only;
  const version = manual
    ? dailyPrecip && `manual-${dailyPrecip.day}`
    : observationVersion(observation);
  setHeaders({
    // La versión del buscador lleva la lectura guardada, no la actual: que
    // no la guarde nadie. `hooks.server.js` ya la saca del CDN; esto lo dice
    // también la propia página.
    'cache-control': crawler
      ? NO_STORE
      : liveCacheControl(consultaBuena, 'public, max-age=3600, stale-while-revalidate=300'),
    ...(version
      ? { etag: contentEtag('observation', lang, station.url_slug, version, replacementPath) }
      : {})
  });

  // La decisión de idioma NO viaja en el HTML: esta página se comparte entre
  // visitantes en el CDN, y un dato de quien pidió primero acabaría contado
  // como el de todos los demás. El navegador sabe sus propios idiomas y los
  // manda aparte al registrar la visita.
  return { lang, slug: station.url_slug, station, meta, observation, dailyPrecip, replacementPath };
}

function describeFailure(cause) {
  return describeRequestFailure(cause, { ApiError });
}

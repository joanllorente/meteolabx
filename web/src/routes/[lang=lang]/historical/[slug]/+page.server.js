import { error, redirect } from '@sveltejs/kit';

import {
  ApiError,
  HISTORICAL_PROVIDERS,
  fetchClimoSummary,
  fetchStationByUrlSlug
} from '$lib/server/api.js';
import {
  MAX_MONTHLY_BLOCKS,
  countBlocks,
  describeSelection,
  resolveSelection
} from '$lib/historical/selection.js';
import { primaryLanguage, stationLanguages, stationMeta } from '$lib/seo/station.js';


/**
 * Histórico de una estación.
 *
 * La consulta NO se lanza al entrar: el histórico son peticiones caras al
 * proveedor —una por bloque mes×año— y abrir la pestaña no es pedirlas. Solo
 * se consulta cuando la selección viaja en la URL, que es lo que hace el
 * botón «Consultar histórico». Así la pestaña abre instantánea, una consulta
 * concreta se puede enlazar y compartir, y recargar no repite la llamada.
 */
export async function load({ params, url, fetch, setHeaders }) {
  const { lang, slug } = params;

  let station;
  try {
    station = await fetchStationByUrlSlug(slug, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) error(404, 'station_not_found');
    throw cause;
  }

  if (station.url_slug !== slug) {
    redirect(301, `/${lang}/historical/${station.url_slug}${url.search}`);
  }
  const languages = stationLanguages(station);
  if (!languages.includes(lang)) {
    redirect(301, `/${primaryLanguage(station)}/historical/${station.url_slug}${url.search}`);
  }

  const meta = stationMeta(station, lang, station.url_slug);
  const supported = HISTORICAL_PROVIDERS.has(String(station.provider).toUpperCase());

  const mode = url.searchParams.get('modo') === 'anual' ? 'annual' : 'monthly';
  // Sin selección explícita no se consulta nada: la pestaña se abre con el
  // formulario preparado y espera al botón.
  const requested = url.searchParams.has('consulta');
  const selection = resolveSelection(url.searchParams, mode, lang, requested);
  const blocks = countBlocks(mode, selection);

  let summary = null;
  let warning = '';
  let failure = '';
  if (requested && supported) {
    if (!selection.years.length || (mode === 'monthly' && !selection.months.length)) {
      warning = mode === 'monthly' ? 'select_month_and_year' : 'select_year';
    } else if (mode === 'monthly' && blocks > MAX_MONTHLY_BLOCKS) {
      warning = 'max_monthly_blocks';
    } else {
      summary = await fetchClimoSummary(
        station,
        {
          language: lang,
          summaryMode: mode,
          // Los bloques de fechas los construye el backend a partir de esto,
          // con la misma regla que la app actual.
          selectedMonths: mode === 'monthly' ? selection.months : [],
          selectedYears: selection.years,
          // Cuántas descargas son: de eso depende cuánto se puede tardar.
          blocks,
          // El backend formatea los valores con estas unidades. De momento son
          // las del sistema métrico; cuando el frontend tenga su selector,
          // saldrán de ahí.
          units: { temperature: '°C', precipitation: 'mm', wind: 'km/h' }
        },
        { fetch }
      ).catch((cause) => {
        // Un proveedor caído, un tiempo agotado o unas credenciales que faltan
        // NO son «no hay datos para este periodo»: el periodo puede estar
        // lleno y ser la consulta la que no llegó. Confundirlos deja a quien
        // mira cambiando de mes para siempre.
        // Un plazo agotado se cuenta como tal: la consulta era demasiado
        // grande, no el periodo demasiado vacío.
        failure =
          cause?.name === 'AbortError' || cause?.name === 'TimeoutError'
            ? 'timeout'
            : cause instanceof ApiError
              ? `${cause.status || ''} ${cause.message || ''}`.trim()
              : 'error';
        return null;
      });
      if (!failure && summary && !summary.has_data) warning = 'no_data_selected_period';
    }
  }

  // Un periodo cerrado no cambia; la pestaña sin consultar tampoco.
  setHeaders({ 'cache-control': 'public, max-age=1800, stale-while-revalidate=86400' });

  return {
    lang,
    slug: station.url_slug,
    station,
    meta,
    supported,
    mode,
    summary,
    requested,
    warning,
    failure,
    provider: station.provider,
    period: describeSelection(mode, selection, blocks),
    maxBlocks: MAX_MONTHLY_BLOCKS,
    selection
  };
}

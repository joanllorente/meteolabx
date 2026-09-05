import { error, redirect } from '@sveltejs/kit';

import {
  ApiError,
  HISTORICAL_PROVIDERS,
  fetchClimoSummary,
  fetchStation
} from '$lib/server/api.js';
import {
  MAX_MONTHLY_BLOCKS,
  countBlocks,
  describeSelection,
  resolveSelection
} from '$lib/historical/selection.js';

// Redes con credencial personal; se resuelven en el navegador.
const PERSONAL_PROVIDERS = ['WU', 'WEATHERLINK'];

/**
 * Histórico de una estación identificada por red e identificador.
 *
 * La hermana de `historical/[slug]`. La selección se lee de la URL igual en
 * los dos casos —es solo parseo—, pero la consulta al proveedor la hace el
 * servidor únicamente cuando puede: las estaciones propias se piden desde el
 * navegador, que es donde está la credencial.
 */
export async function load({ params, url, fetch, setHeaders }) {
  const { lang } = params;
  const provider = decodeURIComponent(params.provider).toUpperCase();
  const stationId = decodeURIComponent(params.stationId);

  const mode = url.searchParams.get('modo') === 'anual' ? 'annual' : 'monthly';
  // Sin selección explícita no se consulta nada: la pestaña se abre con el
  // formulario preparado y espera al botón.
  const requested = url.searchParams.has('consulta');
  const selection = resolveSelection(url.searchParams, mode, lang, requested);
  const blocks = countBlocks(mode, selection);
  const supported = HISTORICAL_PROVIDERS.has(provider);

  const common = {
    lang,
    provider,
    stationId,
    supported,
    mode,
    requested,
    selection,
    blocks,
    period: describeSelection(mode, selection, blocks),
    maxBlocks: MAX_MONTHLY_BLOCKS,
    warning: requested ? validate(mode, selection, blocks) : ''
  };

  if (PERSONAL_PROVIDERS.includes(provider)) {
    setHeaders({ 'cache-control': 'private, no-store' });
    return { ...common, station: null, summary: null, failure: '', personal: true };
  }

  let station;
  try {
    station = await fetchStation(provider, stationId, { fetch });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) error(404, 'station_not_found');
    throw cause;
  }

  // Una sola URL por vista: con ficha indexable, el histórico vive en el slug.
  const slugPayload = await fetch(
    `/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`
  )
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  if (slugPayload?.url_slug) {
    redirect(301, `/${lang}/historical/${slugPayload.url_slug}${url.search}`);
  }

  let summary = null;
  let failure = '';
  let warning = common.warning;
  if (requested && supported && !warning) {
    summary = await fetchClimoSummary(
      station,
      {
        language: lang,
        summaryMode: mode,
        selectedMonths: mode === 'monthly' ? selection.months : [],
        selectedYears: selection.years,
        blocks,
        units: { temperature: '°C', precipitation: 'mm', wind: 'km/h' }
      },
      { fetch }
    ).catch((cause) => {
      // Un proveedor caído o un plazo agotado NO son «no hay datos para este
      // periodo»: el periodo puede estar lleno y ser la consulta la que no
      // llegó.
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

  setHeaders({ 'cache-control': 'public, max-age=1800, stale-while-revalidate=86400' });

  return { ...common, station, summary, failure, warning, personal: false };
}

/** Lo que se puede decir de la selección sin llamar a nadie. */
function validate(mode, selection, blocks) {
  if (!selection.years.length || (mode === 'monthly' && !selection.months.length)) {
    return mode === 'monthly' ? 'select_month_and_year' : 'select_year';
  }
  if (mode === 'monthly' && blocks > MAX_MONTHLY_BLOCKS) return 'max_monthly_blocks';
  return '';
}

import { redirect } from '@sveltejs/kit';

import { LANGUAGES } from '$lib/seo/i18n.js';
import { negotiateLanguage } from '$lib/server/language.js';

/**
 * Los enlaces de la aplicación anterior.
 *
 * Streamlit vivía en `/app`, y con él los enlaces que se compartían así:
 * `?e=AEMET~barcelona-drassanes&sid=0201X&tab=historico`. Al retirarlo esas
 * direcciones quedarían muertas, y son las que la gente tiene guardadas en
 * marcadores y en mensajes de hace meses.
 *
 * Aquí se traducen a su equivalente de ahora: si el enlace traía estación, se
 * abre su ficha —por slug si lo tiene, y si no por red e identificador— y en
 * la pestaña que pedía; si no traía nada, la portada en el idioma del
 * navegador. Todo con 301, para que un buscador que aún los conserve aprenda
 * la dirección nueva.
 */
const TABS = {
  observacion: 'observation',
  observation: 'observation',
  tendencias: 'trends',
  trends: 'trends',
  historico: 'historical',
  historical: 'historical',
  climogramas: 'historical',
  climograms: 'historical'
};

export async function GET({ url, request, fetch }) {
  const language = negotiateLanguage(request.headers.get('accept-language'), Object.keys(LANGUAGES));
  const destino = await stationPath(url, language, fetch);
  redirect(301, destino || `/${language}`);
}

async function stationPath(url, language, fetch) {
  // `e` es `PROVEEDOR~slug-del-nombre`; el identificador de red viaja en `sid`.
  const provider = String(url.searchParams.get('e') || '').split('~')[0].trim().toUpperCase();
  const stationId = String(url.searchParams.get('sid') || '').trim();
  if (!provider || !stationId) return '';

  const section = TABS[String(url.searchParams.get('tab') || '').toLowerCase()] || 'observation';

  // Con ficha indexable se va a su URL canónica; sin ella, la estación se
  // identifica por red e identificador, que es como la abre el mapa.
  const payload = await fetch(
    `/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`
  )
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);

  if (payload?.url_slug) return `/${language}/${section}/${payload.url_slug}`;
  return `/${language}/${section}/${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}`;
}

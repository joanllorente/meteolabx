/**
 * Un mapa de Predicción: `/es/forecast/ebwd`.
 *
 * El visor con ese mapa ya elegido y su guía en el HTML. Un mapa que no
 * existe —un enlace viejo, uno retirado— lleva al índice del mismo idioma.
 */
import { redirect } from '@sveltejs/kit';

import template from '../../../../../static/forecast/index.html?raw';
import {
  forecastHtmlResponse, forecastPath, forecastProduct, renderForecastPage
} from '$lib/server/forecast-page.js';

export function GET({ params, url }) {
  const product = forecastProduct(params.product);
  if (!product) redirect(301, forecastPath(params.lang) + url.search);
  return forecastHtmlResponse(renderForecastPage(template, params.lang, product));
}

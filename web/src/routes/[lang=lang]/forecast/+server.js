/**
 * Índice de Predicción: `/es/forecast`.
 *
 * El visor sin mapa elegido, con todos los mapas enlazados por categoría en
 * el HTML para que el buscador llegue a cada uno. Ver `forecast-page.js`.
 */
import template from '../../../../static/forecast/index.html?raw';
import { forecastHtmlResponse, renderForecastPage } from '$lib/server/forecast-page.js';

export function GET({ params }) {
  return forecastHtmlResponse(renderForecastPage(template, params.lang));
}

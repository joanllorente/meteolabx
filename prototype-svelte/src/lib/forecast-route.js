/**
 * La URL del visor: `/{idioma}/forecast/{mapa}`.
 *
 * Cada mapa tiene su propia dirección para que se pueda indexar y compartir:
 * `/es/forecast/shear-06` abre el visor con la cizalladura 0–6 km ya elegida.
 * El servidor web entrega en esa misma URL el HTML con la guía del mapa, y el
 * visor, al montarse, lee de aquí el idioma y el mapa con que empezar.
 *
 * La forma antigua —`/forecast?lang=en`— la redirige el servidor, pero se
 * sigue entendiendo por si el HTML llega servido desde otro sitio.
 */
export const FORECAST_LANGUAGES = ['es', 'ca', 'en', 'de', 'fr', 'it', 'pt'];
export const FORECAST_SEGMENT = 'forecast';

export function parseForecastLocation(location) {
  const segments = String(location.pathname || '').replace(/^\/+|\/+$/g, '').split('/');
  const query = new URLSearchParams(location.search || '');
  if (FORECAST_LANGUAGES.includes(segments[0]) && segments[1] === FORECAST_SEGMENT) {
    return { language: segments[0], product: decodeURIComponent(segments[2] || '') };
  }
  const queryLanguage = query.get('lang') || '';
  return {
    language: FORECAST_LANGUAGES.includes(queryLanguage) ? queryLanguage : 'es',
    product: ''
  };
}

export function forecastPath(language, product = '') {
  const base = `/${language}/${FORECAST_SEGMENT}`;
  return product ? `${base}/${encodeURIComponent(product)}` : base;
}

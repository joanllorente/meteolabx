/**
 * La frontera entre el frontend nuevo y la app antigua.
 *
 * Un fallo aquí no se ve como un error: se ve como que el mapa, el ranking o
 * el histórico dejan de cargar porque este servicio se los quedó y contestó
 * 404. Por eso se comprueba en las dos direcciones.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isApiPath,
  isOwnedPath,
  parseGlobalSectionPath,
  parseLegacyStationPath,
  parseObservationPath
} from '../src/lib/seo/ownership.js';

test('las fichas estáticas antiguas se reconocen en los seis idiomas', () => {
  const cases = [
    ['/es/estaciones/aemet/barcelona-drassanes-0201x.html', 'es'],
    ['/ca/estacions/meteocat/barcelona-el-raval-x4.html', 'ca'],
    ['/en/weather-stations/nws/central-park-knyc.html', 'en'],
    ['/fr/stations-meteo/meteofrance/tour-eiffel-75107005.html', 'fr'],
    ['/it/stazioni-meteo/aemet/barcelona-drassanes-0201x.html', 'it'],
    ['/pt/estacoes-meteorologicas/aemet/barcelona-drassanes-0201x.html', 'pt']
  ];
  for (const [path, language] of cases) {
    const parsed = parseLegacyStationPath(path);
    assert.ok(parsed, path);
    assert.equal(parsed.language, language);
    assert.match(parsed.slug, /^[a-z0-9-]+$/);
  }
});

test('los índices, directorios y ciudades no son fichas, pero sí son nuestros', () => {
  // Se sirven como estáticos de este servicio desde que Streamlit se retiró:
  // no son fichas de estación, pero tampoco van a ningún proxy.
  for (const path of [
    '/es/estaciones.html',
    '/es/estaciones/aemet.html',
    '/es/tiempo/barcelona.html',
    '/en/weather/london.html'
  ]) {
    assert.equal(parseLegacyStationPath(path), null, path);
    assert.equal(isOwnedPath(path), true, path);
  }

  for (const path of [
    '/es/estaciones/aemet/algo.htm',
    '/xx/estaciones/aemet/algo.html',
    '/es/otracosa/aemet/algo.html'
  ]) {
    assert.equal(parseLegacyStationPath(path), null, path);
    assert.equal(isOwnedPath(path), false, path);
  }
});

test('las secciones ya migradas se reconocen y las demás no', () => {
  assert.deepEqual(parseObservationPath('/es/observation/algo-x8'), {
    language: 'es',
    section: 'observation',
    slug: 'algo-x8'
  });
  assert.deepEqual(parseObservationPath('/en/trends/algo-x8'), {
    language: 'en',
    section: 'trends',
    slug: 'algo-x8'
  });
  assert.equal(parseObservationPath('/es/observation'), null);
  assert.equal(parseObservationPath('/zz/observation/algo'), null);
  assert.equal(parseObservationPath('/es/observacion/algo'), null);
  assert.deepEqual(parseObservationPath('/es/historical/algo-x8'), {
    language: 'es',
    section: 'historical',
    slug: 'algo-x8'
  });
  // Redes sin ficha indexable: se consultan por red e identificador.
  assert.deepEqual(parseObservationPath('/es/observation/NETATMO/70:ee:50:22'), {
    language: 'es',
    section: 'observation',
    provider: 'NETATMO',
    stationId: '70:ee:50:22'
  });
  // Solo observación admite esa forma larga.
  assert.equal(parseObservationPath('/es/trends/NETATMO/70:ee'), null);
  // Ranking no cuelga de una estación: va por otra puerta.
  assert.equal(parseObservationPath('/es/ranking'), null);
  assert.deepEqual(parseGlobalSectionPath('/es/ranking'), { language: 'es', section: 'ranking' });
  assert.deepEqual(parseGlobalSectionPath('/es/map'), { language: 'es', section: 'map' });
  // 'mapa' en castellano no es una ruta: el segmento es estable en inglés.
  assert.equal(parseGlobalSectionPath('/es/mapa'), null);
});

test('el frontend se queda con lo suyo', () => {
  for (const path of [
    '/es/observation/barcelona-drassanes-0201x',
    '/es/trends/barcelona-drassanes-0201x',
    '/es/historical/barcelona-drassanes-0201x',
    '/es/observation/IEM/0-724-0-180',
    '/es/ranking',
    '/en/ranking',
    '/es/map',
    '/observation/barcelona-drassanes-0201x',
    '/robots.txt',
    '/sitemap.xml',
    '/sitemap-static.xml',
    '/sitemap-observation-3.xml',
    '/favicon-32x32.png',
    '/og-image.png',
    '/_app/immutable/entry/app.js',
    '/es/estaciones/aemet/barcelona-drassanes-0201x.html',
    // La portada es el panel vacío, y el visor de predicción viaja con el
    // frontend: ninguno de los dos pasa ya por Streamlit.
    '/',
    '/forecast',
    '/forecast/',
    '/forecast/assets/forecast.js'
  ]) {
    assert.equal(isOwnedPath(path), true, path);
  }
});

test('lo que no reconoce nadie sigue sin ser nuestro', () => {
  // Ya no hay app antigua detrás: estas rutas acaban en un 404 honesto en vez
  // de reenviarse a un servicio que no existe.
  for (const path of ['/media/algo.png', '/_stcore/stream', '/otra-cosa.php']) {
    assert.equal(isOwnedPath(path), false, path);
  }
});

test('la API va a su propio destino, no al servicio antiguo', () => {
  assert.equal(isApiPath('/v1/health'), true);
  assert.equal(isApiPath('/v1'), true);
  assert.equal(isApiPath('/v1/stations/by-url-slug/algo'), true);
  assert.equal(isApiPath('/v2/health'), false);
  assert.equal(isApiPath('/forecast'), false);
  // El sitemap de directorios es del servicio antiguo, no de la API.
  assert.equal(isApiPath('/directories-sitemap.xml'), false);
});

test('las URLs de la aplicación anterior las contesta este servicio', () => {
  // Con Streamlit retirado, `/app` ya no puede ir a ningún proxy: redirige.
  for (const path of ['/app', '/app/', '/app/_stcore/stream', '/app/static/js/index.js']) {
    assert.equal(isOwnedPath(path), true, path);
  }
});

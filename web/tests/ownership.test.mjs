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

test('la API va a su propio destino, no al servicio antiguo', () => {
  assert.equal(isApiPath('/v1/health'), true);
  assert.equal(isApiPath('/v1'), true);
  assert.equal(isApiPath('/v1/stations/by-url-slug/algo'), true);
  assert.equal(isApiPath('/v2/health'), false);
  assert.equal(isApiPath('/forecast'), false);
  // El sitemap de directorios es del servicio antiguo, no de la API.
  assert.equal(isApiPath('/directories-sitemap.xml'), false);
});

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { importLegacyStorage } from '../src/lib/legacy-storage.js';

/**
 * Cómo dejaba cada clave el puente de Streamlit.
 *
 * No guardaba el valor a secas: lo envolvía en un objeto con la propia clave
 * como campo, y lo de dentro venía ya serializado desde Python. Los fixtures
 * de estas pruebas usaban el valor pelado, y por eso daban por buena una
 * migración que en el navegador de verdad no traía nada.
 */
function comoStreamlit(clave, valor) {
  const texto = typeof valor === 'string' ? valor : JSON.stringify(valor);
  return JSON.stringify({ [clave]: texto });
}

/** Un `localStorage` de mentira, que es lo único que necesita el módulo. */
function almacen(inicial = {}) {
  const datos = { ...inicial };
  return {
    getItem: (clave) => (clave in datos ? datos[clave] : null),
    setItem: (clave, valor) => { datos[clave] = String(valor); },
    removeItem: (clave) => { delete datos[clave]; },
    _datos: datos
  };
}

const CATALOGO = {
  'AEMET|0201X': 'barcelona-drassanes-0201x',
  'METEOCAT|X8': 'barcelona-zona-universitaria-x8'
};

/** Responde como `/v1/stations/url-slug`. */
const fetchFalso = async (url) => {
  const params = new URL(url, 'http://x').searchParams;
  const slug = CATALOGO[`${params.get('provider')}|${params.get('station_id')}`];
  return { ok: true, json: async () => ({ url_slug: slug || '' }) };
};

beforeEach(() => {
  globalThis.localStorage = almacen();
});

test('los favoritos antiguos se traducen a la URL de su ficha', async () => {
  globalThis.localStorage = almacen({
    meteolabx_favorites: comoStreamlit('meteolabx_favorites', [
      { kind: 'PROVIDER', provider_id: 'AEMET', station_id: '0201X', station_name: 'Barcelona - Drassanes' },
      { kind: 'PROVIDER', provider_id: 'NETATMO', station_id: '70:ee:50:af:85:02', station_name: 'Rue Domat' }
    ])
  });

  const traido = await importLegacyStorage('es', fetchFalso);
  assert.equal(traido.favoritos, 2);

  const guardados = JSON.parse(localStorage.getItem('mlx-favourites'));
  // Con ficha indexable, su slug; sin ella, la ruta por red e identificador.
  assert.deepEqual(guardados[0], {
    slug: 'barcelona-drassanes-0201x', path: '', name: 'Barcelona - Drassanes', provider: 'AEMET'
  });
  assert.equal(guardados[1].slug, '');
  assert.match(guardados[1].path, /^\/es\/observation\/NETATMO\/70%3Aee/);
});

test('las credenciales y la autoconexión se traen tal cual', async () => {
  globalThis.localStorage = almacen({
    meteolabx_active_station: comoStreamlit('meteolabx_active_station', 'ilhosp26'),
    meteolabx_active_key: comoStreamlit('meteolabx_active_key', 'la-clave'),
    meteolabx_active_z: comoStreamlit('meteolabx_active_z', '39'),
    meteolabx_weatherlink_api_key: comoStreamlit('meteolabx_weatherlink_api_key', 'wl-clave'),
    meteolabx_weatherlink_api_secret: comoStreamlit('meteolabx_weatherlink_api_secret', 'wl-secreto'),
    meteolabx_auto_connect_target: comoStreamlit(
      'meteolabx_auto_connect_target', 'barcelona-drassanes-0201x'
    )
  });

  const traido = await importLegacyStorage('es', fetchFalso);
  assert.deepEqual(traido.credenciales.sort(), ['WEATHERLINK', 'WU']);

  const credenciales = JSON.parse(localStorage.getItem('mlx-credentials'));
  assert.deepEqual(credenciales.WU, { stationId: 'ILHOSP26', apiKey: 'la-clave', elevation: 39 });
  assert.equal(credenciales.WEATHERLINK.apiSecret, 'wl-secreto');
  assert.equal(JSON.parse(localStorage.getItem('mlx-autoconnect')), 'barcelona-drassanes-0201x');
});

test('nunca pisa lo que ya se eligió en la interfaz nueva', async () => {
  globalThis.localStorage = almacen({
    meteolabx_favorites: comoStreamlit('meteolabx_favorites', [
      { provider_id: 'AEMET', station_id: '0201X' }
    ]),
    'mlx-favourites': JSON.stringify([{ slug: 'otra-estacion', path: '', name: 'Otra', provider: 'AEMET' }])
  });

  const traido = await importLegacyStorage('es', fetchFalso);
  assert.equal(traido.favoritos, 0);
  assert.equal(JSON.parse(localStorage.getItem('mlx-favourites'))[0].slug, 'otra-estacion');
});

test('se hace una sola vez', async () => {
  globalThis.localStorage = almacen({
    meteolabx_favorites: comoStreamlit('meteolabx_favorites', [
      { provider_id: 'AEMET', station_id: '0201X' }
    ])
  });

  assert.equal((await importLegacyStorage('es', fetchFalso)).favoritos, 1);
  // Si alguien borra después sus favoritos, no vuelven de la interfaz vieja.
  localStorage.removeItem('mlx-favourites');
  assert.equal(await importLegacyStorage('es', fetchFalso), null);
  assert.equal(localStorage.getItem('mlx-favourites'), null);
});

test('sin nada guardado no inventa nada', async () => {
  const traido = await importLegacyStorage('es', fetchFalso);
  assert.deepEqual(traido, { favoritos: 0, credenciales: [], autoconexion: '' });
  assert.equal(localStorage.getItem('mlx-favourites'), null);
});

test('aguanta también el valor pelado, sin el envoltorio del puente', async () => {
  // Las versiones más viejas de la interfaz anterior escribían así, y no hay
  // forma de saber con cuál se guardó cada navegador.
  globalThis.localStorage = almacen({
    meteolabx_favorites: JSON.stringify([{ provider_id: 'AEMET', station_id: '0201X' }]),
    meteolabx_active_station: 'ilhosp26',
    meteolabx_active_key: 'la-clave'
  });

  const traido = await importLegacyStorage('es', fetchFalso);
  assert.equal(traido.favoritos, 1);
  assert.deepEqual(traido.credenciales, ['WU']);
});

test('lo que quedó marcado como olvidado no se trae', async () => {
  // La interfaz anterior no borraba las claves: les escribía un centinela.
  globalThis.localStorage = almacen({
    meteolabx_active_station: comoStreamlit('meteolabx_active_station', '__MLX_FORGOTTEN__'),
    meteolabx_active_key: comoStreamlit('meteolabx_active_key', '__MLX_FORGOTTEN__'),
    meteolabx_auto_connect_target: comoStreamlit(
      'meteolabx_auto_connect_target', '__MLX_FORGOTTEN__'
    )
  });

  const traido = await importLegacyStorage('es', fetchFalso);
  assert.deepEqual(traido, { favoritos: 0, credenciales: [], autoconexion: '' });
  assert.equal(localStorage.getItem('mlx-credentials'), null);
  assert.equal(localStorage.getItem('mlx-autoconnect'), null);
});

/**
 * Una ficha sin datos no se guarda en ninguna caché.
 *
 * Recoaro Mille (MeteoHub) sumó nueve «provider_unreachable» en un día. La
 * página del error salía con la misma cabecera que una buena: Cloudflare la
 * repartía cinco minutos, el navegador la guardaba una hora, y cada carga de
 * esa copia anotaba otro error en el panel. Un tropiezo del proveedor se
 * convertía en muchos.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { load } from '../src/routes/[lang=lang]/observation/[slug]/+page.server.js';

const STATION = {
  provider: 'METEOHUB_IT',
  station_id: 'dpcn-veneto|45.67972|11.22577|recoaro-mille-cmt',
  url_slug: 'recoaro-mille-cmt-prueba',
  name: 'Recoaro Mille CMT',
  country: 'IT',
  indexable: true,
  is_historical_only: false
};

function fetchConObservacion(respuesta) {
  return async (url) => {
    const ruta = new URL(String(url)).pathname;
    if (ruta.includes('/v1/stations/by-url-slug/')) {
      return new Response(JSON.stringify(STATION), { status: 200 });
    }
    if (ruta.endsWith('/v1/observations/current/processed')) return respuesta();
    return new Response('{}', { status: 404 });
  };
}

async function cabeceras(respuesta, slug) {
  const puestas = {};
  await load({
    params: { lang: 'es', slug },
    fetch: fetchConObservacion(respuesta),
    setHeaders: (valores) => Object.assign(puestas, valores)
  });
  return puestas;
}

test('si el proveedor falla, la ficha no se cachea', async () => {
  const puestas = await cabeceras(
    () => new Response(JSON.stringify({ error_code: 'provider_http_error' }), { status: 502 }),
    'recoaro-mille-cmt-prueba'
  );
  assert.equal(puestas['cache-control'], 'private, no-store');
});

test('con datos, la ficha se sigue compartiendo', async () => {
  const observacion = {
    observation: { epoch: 1790017800, Tc: 15.3 },
    derivatives: {},
    warnings: [],
    station: {},
    daily_extremes: {},
    series: {}
  };
  const puestas = await cabeceras(
    () => new Response(JSON.stringify(observacion), { status: 200 }),
    'recoaro-mille-cmt-prueba'
  );
  assert.match(puestas['cache-control'], /^public, max-age=3600/);
});

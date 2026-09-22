/**
 * La ficha de un pluviómetro manual, tal como la carga el servidor.
 *
 * Lenzerheide (MeteoSwiss) pedía una observación en vivo que no existe: la
 * ficha salía en blanco y el panel sumaba un error por visita. Ahora se pide
 * la última lluvia diaria y la observación en vivo ni se intenta.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { load } from '../src/routes/[lang=lang]/observation/[slug]/+page.server.js';

const LENZERHEIDE = {
  provider: 'METEOSWISS',
  station_id: 'LEH',
  url_slug: 'lenzerheide-leh-prueba',
  name: 'Lenzerheide',
  country: 'CH',
  indexable: true,
  is_historical_only: false,
  manual: true,
  realtime: false
};

const LLUVIA = {
  provider: 'METEOSWISS',
  station_id: 'LEH',
  day: '2026-09-19',
  window_start_utc: '2026-09-19T06:00:00+00:00',
  window_end_utc: '2026-09-20T06:00:00+00:00',
  precip_mm: 0
};

async function cargar(slug, lluvia) {
  const pedidas = [];
  const puestas = {};
  const data = await load({
    params: { lang: 'es', slug },
    fetch: async (url) => {
      const ruta = new URL(String(url)).pathname;
      pedidas.push(ruta);
      if (ruta.includes('/v1/stations/by-url-slug/')) {
        return new Response(JSON.stringify({ ...LENZERHEIDE, url_slug: slug }), { status: 200 });
      }
      if (ruta.endsWith('/v1/observations/daily/latest')) return lluvia();
      return new Response('{}', { status: 500 });
    },
    setHeaders: (valores) => Object.assign(puestas, valores)
  });
  return { data, pedidas, puestas };
}

test('una estación manual no pide la observación en vivo', async () => {
  const { data, pedidas, puestas } = await cargar(
    'lenzerheide-leh-a',
    () => new Response(JSON.stringify(LLUVIA), { status: 200 })
  );
  assert.ok(!pedidas.some((ruta) => ruta.endsWith('/v1/observations/current/processed')));
  assert.deepEqual(data.dailyPrecip, LLUVIA);
  assert.equal(data.observation.unavailable.code, 'manual_daily_station');
  // Con su lluvia es una ficha buena: se comparte, y su versión es el día
  // publicado, para que cambie cuando llegue el siguiente.
  assert.match(puestas['cache-control'], /^public/);
  assert.match(puestas.etag, /^W\//);
});

test('si no llega su lluvia, no se guarda', async () => {
  const { data, puestas } = await cargar(
    'lenzerheide-leh-b',
    () => new Response(JSON.stringify({ error_code: 'data_unavailable' }), { status: 404 })
  );
  assert.equal(data.dailyPrecip, null);
  assert.equal(puestas['cache-control'], 'private, no-store');
});

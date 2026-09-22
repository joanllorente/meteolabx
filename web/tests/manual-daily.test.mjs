import test from 'node:test';
import assert from 'node:assert/strict';

import { isManualDaily, latestDailyText } from '../src/lib/observation/manual-daily.js';

const LENZERHEIDE = {
  provider: 'METEOSWISS',
  station_id: 'LEH',
  day: '2026-09-19',
  window_start_utc: '2026-09-19T06:00:00+00:00',
  window_end_utc: '2026-09-20T06:00:00+00:00',
  precip_mm: 9.7
};

test('solo es manual lo que el catálogo declara sin lectura actual', () => {
  assert.equal(isManualDaily({ realtime: false }), true);
  // Las COOP de IEM son manuales y sí tienen lectura: su catálogo no declara
  // `realtime`, y deben seguir pidiendo la observación en vivo.
  assert.equal(isManualDaily({ manual: true }), false);
  assert.equal(isManualDaily({ realtime: true }), false);
  assert.equal(isManualDaily(null), false);
});

test('la última lluvia se dice con su ventana de 6 a 6 UTC', () => {
  const texto = latestDailyText('es', LENZERHEIDE);
  assert.match(texto, /9,7 mm/);
  assert.match(texto, /19/);
  assert.match(texto, /20/);
  assert.match(texto, /06:00/);
});

test('sin lluvia publicada se dice, no se inventa un cero', () => {
  assert.equal(latestDailyText('es', null), 'Esta estación aún no tiene ninguna lluvia diaria publicada.');
  assert.equal(
    latestDailyText('es', { ...LENZERHEIDE, precip_mm: null }),
    'Esta estación aún no tiene ninguna lluvia diaria publicada.'
  );
});

test('un cero publicado es un cero', () => {
  assert.match(latestDailyText('es', { ...LENZERHEIDE, precip_mm: 0 }), /0,0 mm/);
});

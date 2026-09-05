import assert from 'node:assert/strict';
import test from 'node:test';

import { heatAlert, heatRisk, wetBulbRisk } from '../src/lib/observation/heat-alert.js';

test('a 40 °C de índice de calor hay etiqueta de riesgo, aunque todavía no aviso', () => {
  // Lo que devuelve el backend a partir de 40: categoría «high», sin nivel de
  // alerta. Era el caso que dejaba la tarjeta muda.
  const derivatives = { heat_index_risk: 'high', heat_index_alert_level: '' };
  assert.equal(heatAlert(derivatives, 'es'), null);
  const risk = heatRisk(derivatives, 'es');
  assert.equal(risk.tone, 'warning');
  assert.match(risk.text, /Aviso de calor/);
});

test('a 45 salen las dos cosas, y a 50 en tono de peligro', () => {
  const intenso = { heat_index_risk: 'very_high', heat_index_alert_level: 'warning' };
  assert.equal(heatRisk(intenso, 'es').text, 'Calor intenso');
  assert.equal(heatAlert(intenso, 'es').tone, 'warning');

  const extremo = { heat_index_risk: 'extreme', heat_index_alert_level: 'danger' };
  assert.equal(heatRisk(extremo, 'es').text, 'Calor extremo');
  assert.equal(heatRisk(extremo, 'es').tone, 'danger');
  assert.equal(heatAlert(extremo, 'es').tone, 'danger');
});

test('cada riesgo se queda en su tarjeta, como en la aplicación actual', () => {
  // El caso de la captura: bulbo húmedo en el límite y calor extremo a la vez.
  // La tarjeta de temperatura decía «condiciones extremas» —que describe el
  // bulbo húmedo— en vez de «Calor extremo».
  const ambos = { heat_index_risk: 'extreme', wet_bulb_risk: 'extreme' };
  assert.equal(heatRisk(ambos, 'es').text, 'Calor extremo');
  assert.equal(wetBulbRisk(ambos, 'es').text, 'condiciones extremas');

  const soloBulbo = { wet_bulb_risk: 'critical' };
  assert.equal(heatRisk(soloBulbo, 'es'), null);
  assert.equal(wetBulbRisk(soloBulbo, 'es').text, 'Condiciones críticas');
});

test('sin riesgo no hay etiqueta', () => {
  assert.equal(heatRisk({}, 'es'), null);
  assert.equal(heatRisk({ heat_index_risk: '' }, 'es'), null);
});

for (const language of ['es', 'ca', 'en', 'fr', 'it', 'pt']) {
  test(`la etiqueta de riesgo está traducida al ${language}`, () => {
    for (const category of ['high', 'very_high', 'extreme']) {
      const risk = heatRisk({ heat_index_risk: category }, language);
      assert.ok(risk && risk.text.length > 3, `${language}/${category}`);
    }
  });
}

test('la tarjeta de temperatura lleva la etiqueta dentro del modelo', async () => {
  const { observationModel } = await import('../src/lib/observation/model.js');
  const model = observationModel(
    {
      observation: { epoch: 1788552000, Tc: 35.2, RH: 55, heat_index: 40 },
      derivatives: { heat_index_risk: 'high', heat_index_alert_level: '' },
      daily_extremes: {}
    },
    { provider: 'WU', station_id: 'ITEST', tz: 'Europe/Madrid' },
    'es'
  );

  assert.equal(model.temperature.alert, null, 'a 40 todavía no toca el aviso largo');
  assert.match(model.temperature.risk.text, /Aviso de calor/);
});

test('el aviso dice de qué magnitud habla', () => {
  // «El límite fisiológico teórico de 35 °C» sin decir 35 °C de qué, y encima
  // sobre las tarjetas, se leía como si hablara del termómetro.
  const ambos = heatAlert({ wet_bulb_alert_level: 'danger', heat_index_alert_level: 'danger' }, 'es');
  assert.equal(ambos.subject, 'Bulbo húmedo');

  const soloCalor = heatAlert({ heat_index_alert_level: 'danger' }, 'es');
  assert.equal(soloCalor.subject, 'Índice de calor');

  for (const language of ['es', 'ca', 'en', 'fr', 'it', 'pt']) {
    const aviso = heatAlert({ wet_bulb_alert_level: 'warning' }, language);
    assert.ok(aviso.subject && aviso.subject.length > 2, `${language}: sin magnitud`);
  }
});

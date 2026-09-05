import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import app from '../src/lib/i18n/app-i18n.generated.js';
import { heatAlert } from '../src/lib/observation/heat-alert.js';

const languages = ['es', 'ca', 'en', 'fr', 'it', 'pt'];

for (const language of languages) {
  test(`los avisos térmicos de ${language} son los mismos que en Streamlit`, () => {
    const localeUrl = new URL(`../../locales/${language}.json`, import.meta.url);
    const locale = JSON.parse(readFileSync(localeUrl, 'utf8'));
    const basic = locale.observation.cards.basic;

    assert.deepEqual(
      app.observation[language].temperature.heat_alert,
      basic.temperature.heat_alert
    );
    assert.deepEqual(
      app.observation[language].dew_point.wet_bulb_alert,
      basic.dew_point.wet_bulb_alert
    );
  });
}

test('sin riesgo no aparece ningún aviso', () => {
  assert.equal(heatAlert({}, 'es'), null);
});

test('el índice de calor usa literalmente el aviso de Streamlit', () => {
  assert.deepEqual(heatAlert({ heat_index_alert_level: 'warning' }, 'es'), {
    text: app.observation.es.temperature.heat_alert.warning,
    subject: 'Índice de calor',
    tone: 'warning'
  });
});

test('el peligro por índice de calor gana a un aviso menor de bulbo húmedo', () => {
  assert.deepEqual(heatAlert({
    heat_index_alert_level: 'danger',
    wet_bulb_alert_level: 'warning'
  }, 'es'), {
    text: app.observation.es.temperature.heat_alert.extreme,
    subject: 'Índice de calor',
    tone: 'danger'
  });
});

test('a igual gravedad gana el mensaje específico de bulbo húmedo', () => {
  assert.deepEqual(heatAlert({
    heat_index_alert_level: 'warning',
    wet_bulb_alert_level: 'warning'
  }, 'ca'), {
    text: app.observation.ca.dew_point.wet_bulb_alert.warning,
    subject: 'Bulb humit',
    tone: 'warning'
  });
});

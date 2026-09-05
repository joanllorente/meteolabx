import app from '../i18n/app-i18n.generated.js';
import { ui } from '../i18n/ui.js';

/**
 * Un único aviso térmico, con la misma prioridad y el mismo texto que usa
 * Streamlit. Si bulbo húmedo e índice de calor coinciden en gravedad gana el
 * primero, porque describe un riesgo fisiológico más específico.
 */
export function heatAlert(derivatives, language) {
  const heatLevel = String(derivatives?.heat_index_alert_level || '').toLowerCase();
  const wetLevel = String(derivatives?.wet_bulb_alert_level || '').toLowerCase();
  const validLevel = (level) => level === 'warning' || level === 'danger';
  if (!validLevel(heatLevel) && !validLevel(wetLevel)) return null;

  const useHeat = validLevel(heatLevel)
    && (!validLevel(wetLevel) || (heatLevel === 'danger' && wetLevel !== 'danger'));
  const level = useHeat ? heatLevel : wetLevel;
  const messages = app.observation?.[language] || app.observation?.es || {};
  const source = useHeat
    ? messages.temperature?.heat_alert
    : messages.dew_point?.wet_bulb_alert;
  const key = level === 'danger' ? 'extreme' : 'warning';
  const text = String(source?.[key] || '').trim();
  if (!text) return null;

  // De qué magnitud habla el aviso. El del bulbo húmedo dice «el límite
  // fisiológico teórico de 35 °C» sin decir 35 °C de qué, y suelto sobre las
  // tarjetas se leía como si hablara del termómetro —que en ese momento
  // marcaba 48—. El nombre delante lo ata a su medida.
  const subject = ui(language, useHeat ? 'heat_index' : 'wet_bulb');
  return { text, subject, tone: level };
}

/**
 * La etiqueta corta de riesgo, que no es lo mismo que el aviso.
 *
 * El aviso largo —la caja naranja— empieza en los 45 °C de índice de calor.
 * Pero el riesgo empieza en 40, y hasta ahora la tarjeta no lo decía: se veía
 * un índice de calor de 40 sin una palabra al lado, como si fuera una tarde
 * cualquiera.
 *
 * Cada riesgo va en SU tarjeta, como en la aplicación actual: el del índice de
 * calor bajo la temperatura y el del bulbo húmedo bajo el punto de rocío.
 * Mezclarlos hacía que la tarjeta de temperatura dijera «condiciones
 * extremas» —que es lo que describe el bulbo húmedo— en vez de «Calor
 * extremo».
 */
export function heatRisk(derivatives, language) {
  const messages = app.observation?.[language] || app.observation?.es || {};
  const category = String(derivatives?.heat_index_risk || '').toLowerCase();
  const WEIGHT = { high: 1, very_high: 2, extreme: 3 };
  return label(messages.temperature?.heat_risk?.[category], WEIGHT[category]);
}

/** Lo mismo para el bulbo húmedo, con sus tres categorías. */
export function wetBulbRisk(derivatives, language) {
  const messages = app.observation?.[language] || app.observation?.es || {};
  const category = String(derivatives?.wet_bulb_risk || '').toLowerCase();
  const WEIGHT = { potential: 1, critical: 2, extreme: 3 };
  return label(messages.dew_point?.wet_bulb_risk?.[category], WEIGHT[category]);
}

function label(text, weight) {
  const clean = String(text || '').trim();
  if (!clean || !weight) return null;
  return { text: clean, tone: weight >= 3 ? 'danger' : 'warning' };
}

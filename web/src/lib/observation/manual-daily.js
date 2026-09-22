/**
 * Estaciones sin lectura actual: los pluviómetros manuales.
 *
 * Un observador mide la lluvia una vez al día y la red la publica uno o dos
 * días después. Su ficha de Observación pedía una lectura en vivo que no
 * existe, se quedaba en blanco y el panel lo contaba como error. Lenzerheide
 * (MeteoSwiss) sumó dos el primer día que alguien entró a verla, con la lluvia
 * de ese día publicada en la pestaña de al lado.
 */
import { num } from '$lib/format.js';
import { ui } from '$lib/i18n/ui.js';

/** Solo cuando el catálogo lo declara: sin dato, se trata como automática. */
export function isManualDaily(station) {
  return station?.realtime === false;
}

function fecha(iso, language) {
  const instante = new Date(iso);
  if (Number.isNaN(instante.getTime())) return '';
  return new Intl.DateTimeFormat(language, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC'
  }).format(instante);
}

/**
 * Frase de la última lluvia publicada, o la de «aún no hay» si no llegó.
 *
 * La ventana va de 6 a 6 UTC y se dice entera: «el 19» a secas haría pensar en
 * la lluvia del día 19 en hora local, y es la del 19 a las 6 al 20 a las 6.
 */
export function latestDailyText(language, daily) {
  // `typeof`, no `Number()`: `Number(null)` es 0, y un dato ausente saldría
  // como «0,0 mm», que es afirmar que no llovió.
  if (typeof daily?.precip_mm !== 'number' || !Number.isFinite(daily.precip_mm)) {
    return ui(language, 'manual_daily_none');
  }
  return ui(language, 'manual_daily_latest', {
    amount: num(Number(daily.precip_mm), { language, decimals: 1 }),
    start: fecha(daily.window_start_utc, language),
    end: fecha(daily.window_end_utc, language)
  });
}

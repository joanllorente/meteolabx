/**
 * Formateo de magnitudes meteorológicas.
 *
 * Todo se formatea con la locale de la página y el huso de la estación, nunca
 * con los del navegador: el HTML lo genera el servidor y tiene que salir
 * idéntico al que produce el cliente al hidratar. Un `toLocaleString()` sin
 * argumentos rompería eso en cuanto alguien entre desde otro país.
 */
import app from './i18n/app-i18n.generated.js';
import { cardinals, ui } from './i18n/ui.js';

const LOCALES = {
  es: 'es-ES', ca: 'ca-ES', en: 'en-GB', fr: 'fr-FR', it: 'it-IT', pt: 'pt-PT'
};

export function locale(language) {
  return LOCALES[language] || 'en-GB';
}

export function isNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Número con decimales fijos, o una raya si no hay dato. */
export function num(value, { language = 'es', decimals = 1, dash = '—' } = {}) {
  if (!isNumber(value)) return dash;
  return new Intl.NumberFormat(locale(language), {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    // Sin separador de millares: «1,020.4 hPa» no es como se escribe una
    // presión, y en castellano el punto de millar se confunde con el decimal.
    useGrouping: false
  }).format(value);
}

/** Rumbo en letras: 155° → SSE. */
export function cardinal(degrees, language) {
  if (!isNumber(degrees)) return '';
  const table = cardinals(language);
  return table[Math.round((((degrees % 360) + 360) % 360) / 22.5) % 16];
}

/**
 * Tendencia barométrica de 3 h con los mismos umbrales que el pipeline
 * (`domain/observation_pipeline._pressure_trend_from_endpoints`), pero
 * traducida: el backend devuelve siempre la etiqueta en castellano.
 */
export function pressureTrend(dp3, language) {
  if (!isNumber(dp3)) return { label: '—', arrow: '•', direction: 'flat' };
  const magnitude = Math.abs(dp3);
  if (magnitude < 1) return { label: ui(language, 'trend_steady'), arrow: '→', direction: 'flat' };
  const rising = dp3 > 0;
  const strength = magnitude < 3 ? 'weak' : magnitude < 6 ? 'moderate' : 'strong';
  const arrows = {
    weak: rising ? '↗' : '↘',
    moderate: rising ? '↑' : '↓',
    strong: rising ? '⇑' : '⇓'
  };
  return {
    label: ui(language, `trend_${rising ? 'rise' : 'fall'}_${strength}`),
    arrow: arrows[strength],
    direction: rising ? 'up' : 'down'
  };
}

/** Intensidad de lluvia con los umbrales de `config.py`. */
export function rainIntensity(mmPerHour, language) {
  if (!isNumber(mmPerHour) || mmPerHour <= 0) return ui(language, 'rain_none');
  const scale = [
    [0.4, 'rain_trace'],
    [1.0, 'rain_very_light'],
    [2.5, 'rain_light'],
    [6.5, 'rain_slight'],
    [16.0, 'rain_moderate'],
    [40.0, 'rain_heavy'],
    [100.0, 'rain_very_heavy']
  ];
  for (const [limit, key] of scale) {
    if (mmPerHour < limit) return ui(language, key);
  }
  return ui(language, 'rain_torrential');
}

/**
 * Estado del cielo que acompaña al índice de claridad.
 *
 * Mismos umbrales que la app actual (`models/radiation.py`). Por debajo de 5°
 * de altura solar el índice deja de tener sentido —apenas hay radiación con la
 * que compararlo— y en su lugar se dice en qué tramo de crepúsculo está.
 */
export function skyClarity(clarity, solarAltitude, language) {
  const texts = app.observation?.[language] || app.observation?.es || {};
  const sky = texts.sky || {};
  const states = texts.clarity || {};

  if (isNumber(solarAltitude) && solarAltitude < 5) {
    if (solarAltitude <= -18) return { measurable: false, label: sky.night_closed || '' };
    if (solarAltitude <= -12) return { measurable: false, label: sky.twilight_astronomical || '' };
    if (solarAltitude <= -6) return { measurable: false, label: sky.twilight_nautical || '' };
    if (solarAltitude <= 0) return { measurable: false, label: sky.twilight_civil || '' };
    return { measurable: false, label: '' };
  }

  if (!isNumber(clarity)) return { measurable: false, label: '' };
  const key =
    clarity >= 0.8 ? 'clear'
      : clarity >= 0.6 ? 'mostly_clear'
        : clarity >= 0.4 ? 'partly_cloudy'
          : clarity >= 0.2 ? 'cloudy'
            : 'very_cloudy';
  return { measurable: true, label: states[key] || '' };
}

/** Categoría OMS del índice ultravioleta. */
export function uvCategory(uv, language) {
  if (!isNumber(uv)) return '';
  if (uv < 3) return ui(language, 'uv_low');
  if (uv < 6) return ui(language, 'uv_moderate');
  if (uv < 8) return ui(language, 'uv_high');
  if (uv < 11) return ui(language, 'uv_very_high');
  return ui(language, 'uv_extreme');
}

/** Hora local de la estación, no la de quien mira. */
export function stationTime(epochSeconds, { language = 'es', timeZone } = {}) {
  if (!isNumber(epochSeconds) || epochSeconds <= 0) return '';
  try {
    return new Intl.DateTimeFormat(locale(language), {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: timeZone || 'UTC'
    }).format(new Date(epochSeconds * 1000));
  } catch (error) {
    // Un huso desconocido en el catálogo no debe tumbar el renderizado.
    return new Intl.DateTimeFormat(locale(language), {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC'
    }).format(new Date(epochSeconds * 1000));
  }
}

/** Momento ISO completo, para `<time datetime>` y los datos estructurados. */
export function isoTimestamp(epochSeconds) {
  if (!isNumber(epochSeconds) || epochSeconds <= 0) return '';
  return new Date(epochSeconds * 1000).toISOString();
}

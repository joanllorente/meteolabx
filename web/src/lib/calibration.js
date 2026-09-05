/**
 * Los siete offsets con los que se corrige una estación de Weather Underground.
 *
 * Aquí vive solo lo que no depende del navegador: cuánto se admite de cada
 * sensor y cómo se normaliza lo escrito. El estado guardado —qué tiene cada
 * estación— está en `calibration.svelte.js`, que necesita `localStorage`.
 */
/**
 * Rango de cada offset, copiado de `domain/wu_calibration.py`.
 *
 * El backend rechaza lo que se salga, así que los límites tienen que ser los
 * mismos: un formulario que deja escribir 30 °C de corrección solo sirve para
 * que la petición falle después.
 */
export const CALIBRATION_SPECS = {
  barometer: { min: -20, max: 20, unit: 'hPa', decimals: 0 },
  wind_vane: { min: -180, max: 180, unit: '°', decimals: 0 },
  thermometer: { min: -5, max: 5, unit: '°C', decimals: 1 },
  hygrometer: { min: -20, max: 20, unit: '%', decimals: 1 },
  anemometer: { min: -20, max: 20, unit: 'km/h', decimals: 1 },
  rain_gauge: { min: -20, max: 20, unit: 'mm', decimals: 1 },
  pyranometer: { min: -400, max: 400, unit: 'W/m²', decimals: 1 }
};

/** El orden en que se enseñan, el mismo de la aplicación actual. */
export const CALIBRATION_ORDER = [
  'barometer',
  'wind_vane',
  'thermometer',
  'hygrometer',
  'anemometer',
  'rain_gauge',
  'pyranometer'
];

/**
 * Deja cada offset dentro de su rango y con sus decimales.
 *
 * Lo que no sea un número —un campo vacío, un texto— vale cero: no calibrar
 * es exactamente eso.
 */
export function normalizeCalibration(raw) {
  const values = {};
  for (const [key, spec] of Object.entries(CALIBRATION_SPECS)) {
    const value = Number(String(raw?.[key] ?? '').replace(',', '.'));
    if (!Number.isFinite(value)) {
      values[key] = 0;
      continue;
    }
    const clamped = Math.min(spec.max, Math.max(spec.min, value));
    const factor = 10 ** spec.decimals;
    values[key] = Math.round(clamped * factor) / factor;
  }
  return values;
}

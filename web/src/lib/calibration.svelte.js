/**
 * Calibración de los sensores de una estación de Weather Underground.
 *
 * Son siete desviaciones constantes —lo que el sensor marca de más o de
 * menos— que el backend suma a la lectura antes de derivar nada, de modo que
 * el punto de rocío o la presión al nivel del mar salen ya de los valores
 * corregidos. Solo WU: es la única red donde quien mira es el dueño del
 * aparato y sabe cuánto se desvía.
 *
 * Se guardan por estación y en este navegador, con la misma clave y la misma
 * forma que la aplicación actual —`{"IWU123": {...}}`—, para que quien ya
 * tenga sus offsets ajustados no los pierda al cambiar de interfaz.
 */
import { CALIBRATION_ORDER, CALIBRATION_SPECS, normalizeCalibration } from './calibration.js';
import { readSharedLegacyKey } from './legacy-storage.js';

export { CALIBRATION_ORDER, CALIBRATION_SPECS, normalizeCalibration };

const KEY = 'meteolabx_wu_calibrations';

let stored = $state({});

function read() {
  try {
    // Misma clave que la interfaz anterior, pero aquella la guardaba envuelta
    // en un objeto con la propia clave dentro; sin pelarla, la calibración de
    // cada estación no aparecía por ningún lado.
    return readSharedLegacyKey(KEY) || {};
  } catch {
    // Almacenamiento bloqueado: se sigue sin memoria, sin calibrar.
    return {};
  }
}

/** Relee lo guardado. Se llama al montar: en el servidor no hay dónde mirar. */
export function loadCalibrations() {
  stored = read();
  return stored;
}

/** Offsets de una estación, ya normalizados; `{}` si no tiene ninguno. */
export function calibrationFor(stationId) {
  const key = String(stationId || '').trim().toUpperCase();
  if (!key) return {};
  return normalizeCalibration(stored[key]);
}

/**
 * Lo que se manda al backend, o `null` si no hay nada que corregir.
 *
 * Enviar siete ceros funcionaría igual, pero ensucia cada petición con un
 * objeto que no cambia nada; y el propio backend distingue entre calibrar y
 * no calibrar.
 */
export function calibrationPayload(stationId) {
  const values = calibrationFor(stationId);
  const effective = Object.entries(values).filter(([, value]) => value !== 0);
  return effective.length ? Object.fromEntries(effective) : null;
}

/** Guarda los offsets de una estación. Los ceros no se guardan. */
export function saveCalibration(stationId, values) {
  const key = String(stationId || '').trim().toUpperCase();
  if (!key) return {};
  const clean = normalizeCalibration(values);
  const effective = Object.fromEntries(Object.entries(clean).filter(([, value]) => value !== 0));

  const next = { ...read() };
  if (Object.keys(effective).length) next[key] = effective;
  else delete next[key];

  stored = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* sin almacenamiento la calibración vale para esta sesión y nada más */
  }
  return clean;
}

/** Olvida los offsets de una estación. */
export function forgetCalibration(stationId) {
  return saveCalibration(stationId, {});
}

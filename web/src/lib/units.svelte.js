/** Estado global y persistencia de las preferencias de unidades. */
import {
  defaultUnitPreferences,
  normalizeUnitPreferences,
  unitOptions
} from './units.js';

export * from './units.js';

export const UNIT_STORAGE_KEY = 'meteolabx_unit_preferences';

export const unitPreferences = $state({ ...defaultUnitPreferences });

export function loadUnitPreferences() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(UNIT_STORAGE_KEY) || '{}');
  } catch {
    stored = {};
  }
  Object.assign(unitPreferences, normalizeUnitPreferences(stored));
}

export function chooseUnit(family, unit) {
  if (!unitOptions[family]?.[unit]) return;
  unitPreferences[family] = unit;
  try {
    localStorage.setItem(UNIT_STORAGE_KEY, JSON.stringify(unitPreferences));
  } catch {
    // En navegación privada se conserva al menos durante esta sesión.
  }
  window.dispatchEvent(new CustomEvent('mlx:units', { detail: { ...unitPreferences } }));
}
